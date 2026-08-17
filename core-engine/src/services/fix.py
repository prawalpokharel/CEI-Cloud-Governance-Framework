"""
Automated fix generation.

Phase 4. Turns a prioritized vulnerability finding into a pull request.

## The division of labour

The edit is **deterministic**. Bumping a dependency means locating its
declaration and replacing a version string — an operation with an exactly
correct answer, which a parser produces and a language model can only
approximate. Asking a model to rewrite the file introduces failure modes that
otherwise do not exist: reformatting, dropped comments, unrelated "fixes".

The **explanation** is generated. Which risks a specific upgrade carries and
what a reviewer should check have no parser and genuinely benefit from a
model that has read the ecosystem.

So: code computes the diff, the model writes about it. If the model is
unavailable the fix still opens, with a terser body. If the parser cannot
find the declaration, nothing opens at all — an unverifiable edit is worse
than no pull request.

## What is never done

* No direct commits to a default branch
* No merging, approving, or closing
* No edit whose result was not verified by re-reading the file content
* No pull request for a finding the policy engine did not approve
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .git_provider import GitHubApp, PullRequest, branch_name
from .llm import LLMClient, LLMUnavailable
from .policy import Action, ChangeKind, classify_version_change

log = logging.getLogger(__name__)

# Dispatch table, populated after the editors are defined. It is the single
# source of truth for what can be edited: an earlier version was a separate
# descriptive dict that listed go.mod, which apply_edit never handled, so the
# constant advertised support that did not exist. Driving dispatch from it
# makes that drift impossible.
MANIFEST_EDITORS: dict[str, Any] = {}


@dataclass
class FileEdit:
    path: str
    before: str
    after: str
    line_number: int | None = None

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass
class FixResult:
    finding_id: str
    package: str
    from_version: str | None
    to_version: str | None
    edits: list[FileEdit] = field(default_factory=list)
    pull_request: PullRequest | None = None
    skipped_reason: str | None = None
    explanation: str | None = None
    llm_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "package": self.package,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "files_changed": [e.path for e in self.edits if e.changed],
            "pull_request": (
                {
                    "number": self.pull_request.number,
                    "url": self.pull_request.url,
                    "branch": self.pull_request.branch,
                    "created": self.pull_request.created,
                }
                if self.pull_request
                else None
            ),
            "skipped_reason": self.skipped_reason,
            "explanation": self.explanation,
            "llm_used": self.llm_used,
        }


# --------------------------------------------------------------------------
# Deterministic edits
# --------------------------------------------------------------------------

def bump_requirements_txt(
    content: str, package: str, to_version: str
) -> tuple[str, int | None]:
    """
    Raise a package's version in a requirements.txt.

    Three declaration shapes occur and all three are handled:

        httpx                 -> httpx==0.28.2      (unpinned: add a pin)
        httpx==0.28.1         -> httpx==0.28.2      (pinned: repin)
        scipy>=1.11.0         -> scipy>=1.11.5      (range: raise the floor)

    The existing operator is preserved. Rewriting `scipy>=1.11.0` as
    `scipy==1.11.5` would silently convert a range to a pin — a different
    change from the one being proposed, and one that can break a resolver
    working across several requirements files.

    Matching is anchored at the start of a line and the package name must end
    at a boundary, so `requests` does not rewrite `requests-oauthlib`.
    Everything else on the line, including trailing comments and environment
    markers, is left alone.
    """
    pattern = re.compile(
        rf"^(?P<indent>\s*)(?P<name>{re.escape(package)})"
        rf"(?P<extras>\[[^\]]*\])?"
        # The name must END here. Without this boundary, an optional operator
        # lets `requests` match inside `requests-oauthlib==1.3.1`, and the
        # remainder is carried through as trailing text — producing
        # `requests==2.32.4-oauthlib==1.3.1`, a corrupted line committed to a
        # pull request. A silent file corruption is the worst outcome this
        # module can produce, so the boundary is asserted explicitly rather
        # than implied by requiring an operator.
        rf"(?=\s|$|[=<>~!;#])"
        rf"(?:(?P<space1>\s*)(?P<op>===|==|>=|~=|<=|>|<)(?P<space2>\s*)"
        rf"(?P<version>[^\s;#,]+))?"
        rf"(?P<rest>.*)$",
        re.IGNORECASE,
    )
    lines = content.splitlines(keepends=True)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        match = pattern.match(line.rstrip("\r\n"))
        if not match:
            continue

        parts = match.groupdict()
        indent = parts["indent"]
        name = parts["name"]
        extras = parts["extras"] or ""
        rest = parts["rest"] or ""

        if parts["op"]:
            # Keep the operator and the spacing around it.
            rebuilt = (
                f"{indent}{name}{extras}{parts['space1']}{parts['op']}"
                f"{parts['space2']}{to_version}{rest}"
            )
        else:
            # Unpinned. Adding a pin is the whole remediation.
            rebuilt = f"{indent}{name}{extras}=={to_version}{rest}"

        ending = line[len(line.rstrip("\r\n")):]
        lines[index] = rebuilt + ending
        return "".join(lines), index + 1

    return content, None


def bump_package_json(
    content: str, package: str, to_version: str
) -> tuple[str, int | None]:
    """
    Update a dependency version in package.json.

    Edited as text rather than by parsing and re-serializing JSON: a
    round-trip through json.dumps reformats the whole file, producing a diff
    where every line changed and burying the one that matters.
    """
    pattern = re.compile(
        rf'("{re.escape(package)}"\s*:\s*")([\^~]?)([^"]+)(")'
    )
    match = pattern.search(content)
    if not match:
        return content, None
    line_number = content[: match.start()].count("\n") + 1
    # The existing range prefix is kept: rewriting ^1.2.3 as 1.2.4 silently
    # converts a range to a pin, which is a different change from the one
    # being proposed.
    updated = pattern.sub(rf'\g<1>\g<2>{to_version}\g<4>', content, count=1)
    return updated, line_number


def bump_dockerfile_base(
    content: str, image: str, to_tag: str
) -> tuple[str, int | None]:
    """Update a FROM line's tag."""
    pattern = re.compile(
        rf"^(\s*FROM\s+){re.escape(image)}(:[^\s]+)?(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(content)
    if not match:
        return content, None
    line_number = content[: match.start()].count("\n") + 1
    updated = pattern.sub(rf"\g<1>{image}:{to_tag}\g<3>", content, count=1)
    return updated, line_number


MANIFEST_EDITORS.update({
    "requirements.txt": bump_requirements_txt,
    "package.json": bump_package_json,
    # Prefix match, so Dockerfile.scanner and Dockerfile.prod are covered.
    "Dockerfile": bump_dockerfile_base,
})


def supported_manifests() -> list[str]:
    """Filenames this module can edit. Used by callers and by the tests."""
    return sorted(MANIFEST_EDITORS)


def apply_edit(
    path: str, content: str, package: str, to_version: str
) -> tuple[str, int | None]:
    """
    Dispatch to the right editor by filename.

    An unrecognized manifest returns the content unchanged rather than
    attempting a generic substitution. A wrong edit in a file format nobody
    modelled is worse than no pull request.
    """
    name = path.rsplit("/", 1)[-1]
    editor = MANIFEST_EDITORS.get(name)
    if editor is None and name.startswith("Dockerfile"):
        editor = MANIFEST_EDITORS["Dockerfile"]
    if editor is None:
        return content, None
    return editor(content, package, to_version)


# --------------------------------------------------------------------------
# Explanation
# --------------------------------------------------------------------------

_PROMPT = """You are reviewing an automated dependency upgrade for a Kubernetes platform team.

Package: {package}
Current version: {from_version}
Proposed version: {to_version}
Vulnerability: {finding_id} ({severity})
{title}

This workload has a CEI blast-radius score of {cei:.2f} (0 = nothing depends on it, 1 = most of the cluster does){reach}.

Write a concise PR description with exactly these three sections, as markdown:

### What changed
One sentence.

### Risk
What could break. Be specific to this package and this version jump. If the risk is genuinely low, say so plainly rather than inventing concerns.

### What to check before merging
Two or three concrete, checkable items. No generic advice like "run the tests".

Under 200 words total. No preamble, no closing remarks."""


def generate_explanation(
    client: LLMClient,
    *,
    package: str,
    from_version: str | None,
    to_version: str | None,
    finding_id: str,
    severity: str,
    title: str | None,
    cei_score: float,
    internet_reachable: bool,
) -> tuple[str | None, bool]:
    """Return (explanation, llm_used). Never raises: prose is optional."""
    prompt = _PROMPT.format(
        package=package,
        from_version=from_version or "unknown",
        to_version=to_version or "unknown",
        finding_id=finding_id,
        severity=severity,
        title=f"Advisory: {title}" if title else "",
        cei=cei_score,
        reach=(
            ", and it is reachable from outside the cluster"
            if internet_reachable
            else ""
        ),
    )
    try:
        response = client.complete(prompt, max_output_tokens=2048, temperature=0.2)
        return (response.text or None), bool(response.text)
    except LLMUnavailable as exc:
        log.info("LLM unavailable, opening PR without generated prose: %s", exc)
        return None, False
    except Exception:
        log.exception("Explanation generation failed; continuing without it")
        return None, False


def build_pr_body(
    *,
    finding: dict,
    edits: list[FileEdit],
    explanation: str | None,
    decision_reason: str,
) -> str:
    """
    Compose the PR body.

    The deterministic facts come first and are always present. Generated prose
    is appended and labelled as generated, so a reviewer knows which parts
    were computed and which were written by a model.
    """
    package = finding.get("package", "?")
    from_version = finding.get("installed_version") or "?"
    to_version = finding.get("fixed_version") or "?"
    cei = finding.get("cei_score")

    lines = [
        f"## Upgrade `{package}` {from_version} → {to_version}",
        "",
        f"Resolves **{finding.get('vulnerability_id')}** "
        f"({finding.get('severity')}"
        + (f", CVSS {finding['cvss_score']}" if finding.get("cvss_score") else "")
        + ").",
        "",
        "| | |",
        "|---|---|",
        f"| Image | `{finding.get('image_reference', '?')}` |",
        f"| Blast radius (CEI) | {cei:.2f} |" if cei is not None else "| Blast radius | unknown |",
        f"| Internet reachable | {'yes' if finding.get('internet_reachable') else 'no'} |",
        f"| Files changed | {', '.join(f'`{e.path}`' for e in edits) or 'none'} |",
        "",
        f"**Why this one:** {finding.get('rationale', '')}",
        "",
        f"**Automation policy:** {decision_reason}",
    ]

    if explanation:
        lines += [
            "",
            "---",
            "",
            explanation,
            "",
            "---",
            "",
            "_The three sections above were written by a language model from "
            "the advisory and version metadata. Everything else in this "
            "description was computed from the scan. The file edit itself is "
            "a deterministic version substitution — no model wrote code._",
        ]
    else:
        lines += [
            "",
            "---",
            "",
            "_Opened by CloudOptimizer. The edit is a deterministic version "
            "substitution._",
        ]

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def fix_finding(
    finding: dict,
    decision: dict,
    *,
    repo: str,
    manifest_paths: list[str],
    github: GitHubApp,
    llm: LLMClient,
    dry_run: bool = True,
) -> FixResult:
    """
    Produce a pull request for one approved finding.

    ``decision`` is the policy engine's verdict. A finding it did not approve
    for `open_pr` or `auto_apply` never reaches a repository — the gate is
    enforced here rather than trusted to the caller.
    """
    package = finding.get("package", "")
    from_version = finding.get("installed_version")
    to_version = finding.get("fixed_version")
    finding_id = finding.get("vulnerability_id", "unknown")

    result = FixResult(
        finding_id=finding_id,
        package=package,
        from_version=from_version,
        to_version=to_version,
    )

    action = decision.get("action")
    if action not in (Action.open_pr.value, Action.auto_apply.value):
        result.skipped_reason = (
            f"Policy decided {action!r}: {decision.get('reason', '')}"
        )
        return result

    if not to_version:
        result.skipped_reason = "No fixed version published."
        return result

    # A package name spanning several packages (the deduplicated top-risk
    # display) is not a target; the underlying per-package findings are.
    if "more package" in package:
        result.skipped_reason = (
            "Aggregated finding covering several packages. Fix each package "
            "individually."
        )
        return result

    # OS packages are not declared anywhere in the application's source.
    # They arrive with the base image, so the remediation is a rebase, not a
    # dependency pin -- and choosing the replacement tag needs a registry
    # query this does not perform. Saying "could not locate a declaration"
    # would be technically true and actively misleading: it implies a search
    # that could have succeeded.
    if finding.get("pkg_class") == "os-pkgs":
        result.skipped_reason = (
            f"{package} is an OS package supplied by the base image, not a "
            "declared dependency. The remediation is to rebuild on a current "
            "base image, which clears it along with the other OS findings in "
            "the same image — see the base image recommendations. Selecting "
            "the replacement tag needs a registry query this does not do."
        )
        return result

    change_kind = classify_version_change(from_version, to_version)
    branch = branch_name(
        "fix", f"{finding_id}-{package}-{to_version}"
    )

    # ---- locate and edit the declaration ------------------------------
    edits: list[FileEdit] = []
    for path in manifest_paths:
        existing = github.get_file(repo, path)
        if existing is None:
            continue
        updated, line_number = apply_edit(
            path, existing["content"], package, to_version
        )
        if updated != existing["content"]:
            edits.append(FileEdit(
                path=path,
                before=existing["content"],
                after=updated,
                line_number=line_number,
            ))

    if not edits:
        result.skipped_reason = (
            f"Could not locate a declaration for {package!r} in "
            f"{', '.join(manifest_paths) or 'any known manifest'}. No edit was "
            "guessed at."
        )
        return result

    result.edits = edits

    explanation, llm_used = generate_explanation(
        llm,
        package=package,
        from_version=from_version,
        to_version=to_version,
        finding_id=finding_id,
        severity=finding.get("severity", "UNKNOWN"),
        title=finding.get("title"),
        cei_score=finding.get("cei_score") or 0.0,
        internet_reachable=bool(finding.get("internet_reachable")),
    )
    result.explanation = explanation
    result.llm_used = llm_used

    if dry_run:
        result.skipped_reason = "Dry run — nothing was pushed."
        return result

    # ---- push and open the PR -----------------------------------------
    base = github.default_branch(repo)
    github.ensure_branch(repo, branch, base)

    for edit in edits:
        # Re-read on the branch: the blob SHA on the branch differs from the
        # one on the base once a previous commit has landed there.
        on_branch = github.get_file(repo, edit.path, ref=branch)
        github.put_file(
            repo,
            edit.path,
            edit.after,
            message=(
                f"fix({package}): {from_version or '?'} → {to_version} "
                f"for {finding_id}"
            ),
            branch=branch,
            sha=on_branch["sha"] if on_branch else None,
        )

    title = f"fix({package}): upgrade to {to_version} for {finding_id}"
    body = build_pr_body(
        finding=finding,
        edits=edits,
        explanation=explanation,
        decision_reason=decision.get("reason", ""),
    )
    labels = ["cloudoptimizer", "security"]
    if change_kind is ChangeKind.patch_bump:
        labels.append("patch")

    result.pull_request = github.open_pull_request(
        repo, branch=branch, base=base, title=title, body=body, labels=labels
    )
    return result
