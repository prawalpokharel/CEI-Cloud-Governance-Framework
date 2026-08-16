"""
Phase 4: automated fix generation.

The file edits are the dangerous part. Everything else in this product
produces a number that can be wrong; this produces a commit. So the tests
concentrate on what must never happen to a file, and on the gates that decide
whether a pull request is opened at all.

Nothing here touches the network.
"""

from __future__ import annotations

import pytest

from src.services.fix import (
    apply_edit,
    build_pr_body,
    bump_dockerfile_base,
    bump_package_json,
    bump_requirements_txt,
    fix_finding,
)
from src.services.git_provider import branch_name
from src.services.llm import RETIRED_GEMINI_MODELS, LLMClient, LLMUnavailable
from src.services.policy import Action


# --------------------------------------------------------------------------
# requirements.txt
# --------------------------------------------------------------------------

def test_unpinned_dependency_gains_a_pin():
    """Adding a pin where none existed is the remediation, not a failure."""
    out, line = bump_requirements_txt("fastapi\nhttpx\nscipy>=1.11.0\n", "httpx", "0.28.2")
    assert "httpx==0.28.2" in out
    assert line == 2


def test_pinned_dependency_is_repinned():
    out, _ = bump_requirements_txt("httpx==0.28.1\n", "httpx", "0.28.2")
    assert out == "httpx==0.28.2\n"


def test_a_range_constraint_keeps_its_operator():
    """
    Rewriting `scipy>=1.11.0` as `scipy==1.11.5` silently converts a range to
    a pin — a different change from the one proposed, and one that can break
    resolution across several requirements files.
    """
    out, _ = bump_requirements_txt("scipy>=1.11.0\n", "scipy", "1.11.5")
    assert out == "scipy>=1.11.5\n"


@pytest.mark.parametrize(
    "content,package",
    [
        ("requests-oauthlib==1.3.1\n", "requests"),
        ("requests-oauthlib\n", "requests"),
        ("httpxx==1.0\n", "httpx"),
        ("python-dateutil==2.9.0\n", "dateutil"),
    ],
)
def test_a_package_name_never_matches_inside_another(content, package):
    """
    The regression this guards: with an optional version operator, `requests`
    matched inside `requests-oauthlib==1.3.1` and the remainder was carried
    through as trailing text, producing `requests==2.32.4-oauthlib==1.3.1`.
    A corrupted file committed to a pull request is the worst thing this
    module can do.
    """
    out, line = bump_requirements_txt(content, package, "9.9.9")
    assert out == content
    assert line is None


def test_extras_markers_and_comments_survive():
    content = "uvicorn[standard]==0.30.0  ; python_version>='3.9'  # server\n"
    out, _ = bump_requirements_txt(content, "uvicorn", "0.32.1")
    assert out == (
        "uvicorn[standard]==0.32.1  ; python_version>='3.9'  # server\n"
    )


def test_commented_and_flag_lines_are_skipped():
    content = "# httpx==0.1.0\n-r base.txt\nhttpx==0.28.1\n"
    out, line = bump_requirements_txt(content, "httpx", "0.28.2")
    assert out.startswith("# httpx==0.1.0\n-r base.txt\n")
    assert line == 3


def test_only_the_matched_line_changes():
    content = "fastapi==0.141.1\nhttpx==0.28.1\nnumpy==2.5.2\n"
    out, _ = bump_requirements_txt(content, "httpx", "0.28.2")
    before, after = content.splitlines(), out.splitlines()
    assert sum(1 for a, b in zip(before, after) if a != b) == 1


# --------------------------------------------------------------------------
# package.json
# --------------------------------------------------------------------------

def test_package_json_keeps_the_range_prefix():
    content = '{\n  "dependencies": {\n    "axios": "^1.6.2"\n  }\n}\n'
    out, _ = bump_package_json(content, "axios", "1.7.9")
    assert '"axios": "^1.7.9"' in out


def test_package_json_is_not_reformatted():
    """
    Round-tripping through json.dumps rewrites every line and buries the one
    that matters in a whole-file diff.
    """
    content = '{\n  "name": "app",\n  "dependencies": {\n    "axios": "^1.6.2",\n    "lodash": "4.17.21"\n  }\n}\n'
    out, _ = bump_package_json(content, "axios", "1.7.9")
    assert out.count("\n") == content.count("\n")
    assert '"lodash": "4.17.21"' in out


def test_package_json_absent_dependency_is_left_alone():
    content = '{"dependencies": {"axios": "^1.6.2"}}'
    out, line = bump_package_json(content, "missing", "1.0.0")
    assert out == content and line is None


# --------------------------------------------------------------------------
# Dockerfile
# --------------------------------------------------------------------------

def test_dockerfile_base_tag_is_updated_preserving_the_stage_name():
    out, line = bump_dockerfile_base(
        "FROM python:3.9-slim AS build\nRUN pip install .\n", "python", "3.12-slim"
    )
    assert out.startswith("FROM python:3.12-slim AS build")
    assert line == 1


def test_apply_edit_dispatches_on_filename():
    assert apply_edit("a/requirements.txt", "httpx\n", "httpx", "1.0")[0] == "httpx==1.0\n"
    assert apply_edit("x/Dockerfile.scanner", "FROM python:3.9\n", "python", "3.12")[0] == "FROM python:3.12\n"
    # An unknown manifest is not guessed at.
    assert apply_edit("a/Cargo.toml", 'httpx = "1"\n', "httpx", "2")[0] == 'httpx = "1"\n'


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

class _NoGitHub:
    """Fails loudly if the gate lets anything through to the network."""

    def get_file(self, *a, **k):
        raise AssertionError("GitHub must not be contacted for a gated finding")

    default_branch = ensure_branch = put_file = open_pull_request = get_file


def _finding(**kw):
    base = {
        "vulnerability_id": "CVE-1", "package": "httpx",
        "installed_version": "0.28.1", "fixed_version": "0.28.2",
        "pkg_class": "lang-pkgs", "severity": "HIGH", "cei_score": 0.2,
    }
    base.update(kw)
    return base


def _decision(action=Action.open_pr, reason="approved"):
    return {"action": action.value, "reason": reason}


def test_a_finding_the_policy_refused_never_reaches_a_repository():
    """
    The gate is enforced here rather than trusted to the caller: a bug in a
    caller must not be able to open a pull request the policy declined.
    """
    result = fix_finding(
        _finding(), _decision(Action.alert_only, "major bump"),
        repo="o/r", manifest_paths=["requirements.txt"],
        github=_NoGitHub(), llm=LLMClient(), dry_run=True,
    )
    assert result.pull_request is None
    assert "alert_only" in result.skipped_reason


def test_an_os_package_is_routed_to_a_rebase_not_a_manifest_edit():
    """
    OS packages arrive with the base image and are declared nowhere in the
    source. "Could not locate a declaration" would be true and misleading.
    """
    result = fix_finding(
        _finding(package="openssl", pkg_class="os-pkgs"), _decision(),
        repo="o/r", manifest_paths=["requirements.txt"],
        github=_NoGitHub(), llm=LLMClient(), dry_run=True,
    )
    assert "base image" in result.skipped_reason
    assert result.pull_request is None


def test_a_finding_with_no_fix_is_not_attempted():
    result = fix_finding(
        _finding(fixed_version=None), _decision(),
        repo="o/r", manifest_paths=["requirements.txt"],
        github=_NoGitHub(), llm=LLMClient(), dry_run=True,
    )
    assert "No fixed version" in result.skipped_reason


def test_an_aggregated_multi_package_finding_is_not_attempted():
    """
    The deduplicated top-risk display names one package "+2 more packages".
    That label is not a package and must not be searched for.
    """
    result = fix_finding(
        _finding(package="libssl3 +2 more packages"), _decision(),
        repo="o/r", manifest_paths=["requirements.txt"],
        github=_NoGitHub(), llm=LLMClient(), dry_run=True,
    )
    assert "individually" in result.skipped_reason


# --------------------------------------------------------------------------
# PR body
# --------------------------------------------------------------------------

def test_pr_body_separates_computed_facts_from_generated_prose():
    """
    A reviewer has to know which parts a model wrote. Presenting generated
    prose as computed output is how a wrong sentence gets trusted.
    """
    body = build_pr_body(
        finding=_finding(image_reference="r/i:1", rationale="because"),
        edits=[], explanation="### Risk\nLow.", decision_reason="proposed",
    )
    assert "written by a language model" in body
    assert "no model wrote code" in body


def test_pr_body_is_complete_without_the_model():
    body = build_pr_body(
        finding=_finding(image_reference="r/i:1", rationale="because"),
        edits=[], explanation=None, decision_reason="proposed",
    )
    assert "CVE-1" in body and "0.28.2" in body
    assert "written by a language model" not in body


# --------------------------------------------------------------------------
# Branch naming
# --------------------------------------------------------------------------

def test_branch_names_are_deterministic_and_safe():
    """
    Same finding must produce the same branch, so a daily job updates one pull
    request instead of opening a new one every day.
    """
    a = branch_name("fix", "CVE-2026-1-openssl-3.0.15")
    b = branch_name("fix", "CVE-2026-1-openssl-3.0.15")
    assert a == b
    assert a.startswith("cloudoptimizer/fix/")
    assert " " not in a and len(a) <= 240


def test_branch_names_strip_characters_git_rejects():
    name = branch_name("fix", "CVE 2026/1: openssl~3.0")
    assert all(c.isalnum() or c in "-._/" for c in name)


# --------------------------------------------------------------------------
# LLM client
# --------------------------------------------------------------------------

def test_retired_models_are_rejected_with_an_actionable_message():
    client = LLMClient(provider="gemini", model="gemini-2.5-flash", api_key="x")
    with pytest.raises(LLMUnavailable, match="gemini-3.7-flash"):
        client.complete("hi")


def test_unconfigured_client_reports_unavailable_not_error():
    """Callers degrade on unavailable and surface real errors."""
    with pytest.raises(LLMUnavailable):
        LLMClient(provider="", model="", api_key="").complete("hi")


def test_describe_never_exposes_the_key():
    described = LLMClient(provider="gemini", model="m", api_key="secret-value").describe()
    assert described["api_key_present"] is True
    assert "secret-value" not in str(described)


def test_the_retired_list_names_models_the_docs_still_recommend():
    assert "gemini-2.5-flash" in RETIRED_GEMINI_MODELS
