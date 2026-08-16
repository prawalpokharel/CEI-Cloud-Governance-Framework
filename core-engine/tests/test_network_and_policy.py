"""
Phase 6 (segmentation) and Phase 7 (remediation policy).

Both modules decide things that, if wrong, cause an outage rather than a bad
number. The tests are written against that risk: what must never be generated,
and what must never be applied automatically.
"""

from __future__ import annotations

import pytest

from src.services.network import (
    SYSTEM_NAMESPACES,
    analyze_segmentation,
    generate_all_policies,
    generate_network_policy,
)
from src.services.policy import (
    AUTO_APPLY_CEI_CEILING,
    Action,
    ChangeKind,
    classify_version_change,
    decide,
    plan_remediation,
)


def _wl(name, namespace="app", labels=None):
    return {
        "key": f"{namespace}/Deployment/{name}",
        "name": name,
        "namespace": namespace,
        "kind": "Deployment",
        "pod_labels": labels or {"app": name},
    }


def _edge(src, dst, namespace="app", kind="env_reference", confidence=0.9):
    return {
        "source": f"{namespace}/Deployment/{src}",
        "target": f"{namespace}/Deployment/{dst}",
        "source_kind": kind,
        "confidence": confidence,
    }


SNAPSHOT = {
    "workloads": [_wl("api"), _wl("db"), _wl("worker")],
    "edges": [_edge("api", "db"), _edge("worker", "db")],
    "network_policies": [],
    "services": [],
}


# --------------------------------------------------------------------------
# Segmentation analysis
# --------------------------------------------------------------------------

def test_unsegmented_workloads_are_reported():
    result = analyze_segmentation(SNAPSHOT)
    assert result["summary"]["workloads_unsegmented"] == 3
    assert result["summary"]["coverage"] == 0.0


def test_an_empty_pod_selector_covers_the_whole_namespace():
    """
    That is how default-deny is written. Reading it as "selects nothing" would
    report a properly segmented namespace as unprotected.
    """
    snapshot = {
        **SNAPSHOT,
        "network_policies": [
            {"name": "default-deny", "namespace": "app", "pod_selector": {}}
        ],
    }
    result = analyze_segmentation(snapshot)
    assert result["summary"]["workloads_unsegmented"] == 0
    assert result["summary"]["coverage"] == 1.0


def test_system_namespaces_are_excluded_from_coverage():
    snapshot = {
        "workloads": [_wl("coredns", namespace="kube-system"), _wl("api")],
        "edges": [],
        "network_policies": [],
        "services": [],
    }
    result = analyze_segmentation(snapshot)
    assert result["summary"]["workloads_considered"] == 1


def test_findings_are_ranked_by_blast_radius():
    cei = {
        "app/Deployment/db": {"cei_score": 0.9},
        "app/Deployment/api": {"cei_score": 0.2},
        "app/Deployment/worker": {"cei_score": 0.1},
    }
    findings = analyze_segmentation(SNAPSHOT, cei)["findings"]
    assert "db" in findings[0]["title"]


def test_coverage_does_not_claim_the_policies_are_correct():
    """A policy that allows everything still counts as covered."""
    note = analyze_segmentation(SNAPSHOT)["summary"]["note"]
    assert "does not judge whether those policies are correct" in note


# --------------------------------------------------------------------------
# Policy generation
# --------------------------------------------------------------------------

def test_generated_policy_mirrors_observed_dependencies():
    policy = generate_network_policy(SNAPSHOT, "app/Deployment/db")
    spec = policy["manifest"]["spec"]

    assert spec["podSelector"] == {"matchLabels": {"app": "db"}}
    assert policy["observed_callers"] == 2
    # api and worker call db, so both appear as ingress sources.
    sources = str(spec["ingress"])
    assert "api" in sources and "worker" in sources


def test_generated_policy_always_permits_dns():
    """
    Omitting DNS is the most common way a hand-written NetworkPolicy breaks a
    cluster, and the resulting failure looks like an application bug.
    """
    policy = generate_network_policy(SNAPSHOT, "app/Deployment/api")
    egress = policy["manifest"]["spec"]["egress"]
    assert any("DNS" in (rule.get("_comment") or "") for rule in egress)
    dns_rule = next(r for r in egress if "DNS" in (r.get("_comment") or ""))
    assert {"protocol": "UDP", "port": 53} in dns_rule["ports"]


def test_generated_policy_carries_its_own_limitation():
    """
    A tool that hands you a policy and says "apply this" is handing you an
    outage. The warning has to travel with the manifest.
    """
    policy = generate_network_policy(SNAPSHOT, "app/Deployment/db")
    assert "non-enforcing posture first" in policy["warning"]
    annotations = policy["manifest"]["metadata"]["annotations"]
    assert annotations["cloudoptimizer.app/review-required"] == "true"


def test_a_workload_with_no_labels_gets_no_policy():
    """
    An empty podSelector would silently apply to every pod in the namespace.
    """
    snapshot = {
        "workloads": [{"key": "app/Deployment/x", "name": "x",
                       "namespace": "app", "pod_labels": {}}],
        "edges": [], "network_policies": [], "services": [],
    }
    assert generate_network_policy(snapshot, "app/Deployment/x") is None


def test_a_workload_with_no_observed_edges_is_skipped_entirely():
    """
    Generating a deny-everything policy from an absence of evidence looks like
    a tidy result and is actually just ignorance. "We observed no
    dependencies" and "there are no dependencies" are different statements.
    """
    snapshot = {
        "workloads": [_wl("orphan")],
        "edges": [], "network_policies": [], "services": [],
    }
    policy = generate_network_policy(snapshot, "app/Deployment/orphan")
    assert policy["confidence"] == "none"
    assert generate_all_policies(snapshot) == []


def test_system_namespaces_never_get_generated_policies():
    """Locking yourself out of your own control plane."""
    snapshot = {
        "workloads": [_wl("coredns", namespace="kube-system")],
        "edges": [_edge("coredns", "coredns", namespace="kube-system")],
        "network_policies": [], "services": [],
    }
    assert generate_all_policies(snapshot) == []


def test_policies_are_ordered_by_blast_radius():
    cei = {
        "app/Deployment/db": {"cei_score": 0.9},
        "app/Deployment/api": {"cei_score": 0.3},
    }
    policies = generate_all_policies(SNAPSHOT, cei)
    assert policies[0]["workload_key"] == "app/Deployment/db"


# --------------------------------------------------------------------------
# Version classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "installed,fixed,expected",
    [
        ("1.2.3", "1.2.4", ChangeKind.patch_bump),
        ("1.2.3", "1.3.0", ChangeKind.minor_bump),
        ("1.2.3", "2.0.0", ChangeKind.major_bump),
        ("v1.2.3", "v1.2.9", ChangeKind.patch_bump),
        ("1.26.18", "2.2.3", ChangeKind.major_bump),
        # Distro versions carry an upstream part and a distro revision.
        # Classification reads the upstream part only:
        #
        #   2.36-9 -> 2.36-9+deb12u9   upstream unchanged, revision moved.
        #                              Returns unknown, so it is alert_only
        #                              rather than being called a patch.
        #   3.5.1-... -> 3.5.5-...     upstream moved 3.5.1 to 3.5.5, which is
        #                              a patch-series bump.
        #
        # The conservative direction in both cases: a revision-only change is
        # never classified as safe, and an upstream change is classified by
        # what actually moved.
        ("2.36-9", "2.36-9+deb12u9", ChangeKind.unknown),
        ("3.5.1-1+deb13u1", "3.5.5-1~deb13u2", ChangeKind.patch_bump),
        (None, "1.0.0", ChangeKind.unknown),
        ("1.0.0", None, ChangeKind.unknown),
        ("garbage", "nonsense", ChangeKind.unknown),
    ],
)
def test_version_classification(installed, fixed, expected):
    assert classify_version_change(installed, fixed) == expected


# --------------------------------------------------------------------------
# The automation gate
# --------------------------------------------------------------------------

def _base(**kw):
    args = dict(
        change_kind=ChangeKind.patch_bump,
        cei_score=0.1,
        namespace="app",
        internet_reachable=False,
        auto_apply_enabled=True,
        has_tests=True,
    )
    args.update(kw)
    return decide(**args)


def test_a_safe_patch_on_an_isolated_workload_may_be_applied():
    assert _base().action is Action.auto_apply


def test_automation_is_off_unless_explicitly_enabled():
    """A system that changes things because it was installed is uninstalled."""
    assert _base(auto_apply_enabled=False).action is Action.open_pr


def test_blast_radius_stops_automatic_application():
    """
    The most urgent fix on the most critical workload is exactly the change
    you least want applied unattended.
    """
    decision = _base(cei_score=AUTO_APPLY_CEI_CEILING + 0.01)
    assert decision.action is Action.open_pr
    assert "Blast radius too large" in decision.reason


def test_internet_reachable_workloads_never_auto_apply():
    decision = _base(internet_reachable=True)
    assert decision.action is Action.open_pr
    assert "attack surface" in decision.reason


def test_untested_components_never_auto_apply():
    decision = _base(has_tests=False)
    assert decision.action is Action.open_pr
    assert "test coverage" in decision.reason


def test_a_major_bump_is_never_automatic_at_any_blast_radius():
    decision = _base(change_kind=ChangeKind.major_bump, cei_score=0.0)
    assert decision.action is Action.alert_only


def test_an_unclassifiable_version_is_never_automatic():
    decision = _base(change_kind=ChangeKind.unknown)
    assert decision.action is Action.alert_only


def test_protected_namespaces_are_never_touched():
    for namespace in list(SYSTEM_NAMESPACES)[:3]:
        decision = _base(namespace=namespace)
        assert decision.action is Action.alert_only
        assert "protected_namespace" in decision.guardrails


def test_missing_cei_is_treated_as_maximum_blast_radius():
    """Unknown must fail safe, not fail permissive."""
    assert _base(cei_score=None).action is Action.open_pr


def test_capacity_and_connectivity_changes_are_only_ever_proposed():
    for kind in (ChangeKind.replica_change, ChangeKind.network_policy,
                 ChangeKind.resource_rightsize):
        decision = _base(change_kind=kind, cei_score=0.0)
        assert decision.action is Action.open_pr


def test_every_refusal_explains_which_gate_stopped_it():
    """
    "Needs approval" with no reason is how a policy engine becomes something
    people disable.
    """
    for decision in (
        _base(auto_apply_enabled=False),
        _base(cei_score=0.9),
        _base(internet_reachable=True),
        _base(has_tests=False),
        _base(change_kind=ChangeKind.major_bump),
    ):
        assert len(decision.reason) > 40


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def test_a_finding_with_no_fix_is_alert_only():
    plan = plan_remediation([{
        "vulnerability_id": "CVE-1", "package": "zlib",
        "installed_version": "1.2.13", "fixed_version": None,
        "cei_score": 0.1, "workload_keys": ["app/Deployment/x"],
    }])
    assert plan["plan"][0]["decision"]["action"] == "alert_only"
    assert "no_fix_available" in plan["plan"][0]["decision"]["guardrails"]


def test_nothing_auto_applies_before_test_detection_exists():
    """
    Test coverage cannot be detected until the Git integration lands, so
    has_tests is passed as False rather than defaulted to True — which would
    silently authorize unattended changes.
    """
    plan = plan_remediation(
        [{
            "vulnerability_id": "CVE-1", "package": "openssl",
            "installed_version": "3.0.11", "fixed_version": "3.0.15",
            "cei_score": 0.05, "workload_keys": ["app/Deployment/x"],
        }],
        auto_apply_enabled=True,
    )
    assert plan["summary"]["by_action"]["auto_apply"] == 0
