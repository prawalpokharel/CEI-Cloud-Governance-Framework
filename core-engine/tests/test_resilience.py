"""
Latent single-point-of-failure detection.

The scenarios here mirror the shapes seen on a real cluster during
development: a PDB that blocks every drain, an HPA whose min equals its max,
and replicas that co-located despite a spread constraint set to
ScheduleAnyway.
"""

import pytest

from src.services.resilience import (
    CONFIG_FAN_IN_THRESHOLD,
    _parse_quantity,
    analyze,
)


def _workload(name, *, replicas=2, ns="prod", labels=None, probes=None,
              spread=None, config_maps=(), kind="Deployment", ownership=None):
    return {
        "key": f"{ns}/{kind}/{name}",
        "name": name,
        "namespace": ns,
        "kind": kind,
        "replicas_desired": replicas,
        "pod_labels": labels or {"app": name},
        "ownership": ownership or {},
        "probes": probes or {"containers": 1, "with_readiness": 1,
                             "with_liveness": 1, "with_startup": 0},
        "spread": spread or {"topology_spread_constraints": 1,
                             "topology_keys": ["kubernetes.io/hostname"],
                             "anti_affinity_required": 0,
                             "anti_affinity_preferred": 0,
                             "priority_class": None},
        "config_refs": {"config_maps": list(config_maps), "secrets": []},
    }


def _pod(name, workload_name, node, ns="prod"):
    return {"name": name, "namespace": ns, "node_name": node,
            "labels": {"app": workload_name}, "phase": "Running",
            "restart_count": 0, "waiting_reasons": [], "last_terminated_reasons": []}


def _snapshot(workloads, pods=(), edges=(), pdbs=(), hpas=()):
    return {
        "workloads": list(workloads),
        "pods": list(pods),
        "edges": list(edges),
        "disruption_budgets": list(pdbs),
        "autoscalers": list(hpas),
    }


def _edge(source, target, kind="env_reference", confidence=0.9):
    return {"source": source, "target": target,
            "confidence": confidence, "source_kind": kind}


def _kinds(result):
    return {f["kind"] for f in result["findings"]}


def _find(result, kind):
    return next(f for f in result["findings"] if f["kind"] == kind)


CEI = {
    "prod/Deployment/api": {"cei_score": 0.85, "classification": "critical"},
    "prod/Deployment/worker": {"cei_score": 0.40, "classification": "moderate"},
    "prod/Deployment/scratch": {"cei_score": 0.10, "classification": "low"},
}


@pytest.fixture
def central_api():
    """api has a dependent and sits behind an ingress: it is load-bearing."""
    return _snapshot(
        workloads=[_workload("api"), _workload("worker", replicas=1)],
        pods=[_pod("api-1", "api", "node-a"), _pod("api-2", "api", "node-b"),
              _pod("worker-1", "worker", "node-a")],
        edges=[
            _edge("prod/Deployment/worker", "prod/Deployment/api"),
            _edge("prod/Ingress/public", "prod/Deployment/api", "ingress", 1.0),
        ],
    )


# --- placement -------------------------------------------------------------


def test_all_replicas_on_one_node_is_detected(central_api):
    central_api["pods"] = [_pod("api-1", "api", "node-a"),
                           _pod("api-2", "api", "node-a")]
    result = analyze(central_api, CEI)

    finding = _find(result, "single_node_placement")
    assert finding["evidence"]["nodes"] == ["node-a"]
    assert finding["evidence"]["replicas"] == 2


def test_spread_across_nodes_is_not_flagged(central_api):
    assert "single_node_placement" not in _kinds(analyze(central_api, CEI))


def test_ineffective_spread_policy_is_called_out(central_api):
    """
    A constraint set to ScheduleAnyway is advisory. Saying "no spread policy"
    when one exists sends the operator to fix something already configured.
    """
    central_api["pods"] = [_pod("api-1", "api", "node-a"),
                           _pod("api-2", "api", "node-a")]
    finding = _find(analyze(central_api, CEI), "single_node_placement")

    assert finding["evidence"]["spread_policy_present"] is True
    assert "ScheduleAnyway" in finding["detail"]


def test_single_replica_is_not_a_placement_finding(central_api):
    """One replica on one node is not a co-location problem."""
    central_api["workloads"] = [_workload("api", replicas=1), _workload("worker", replicas=1)]
    central_api["pods"] = [_pod("api-1", "api", "node-a")]

    assert "single_node_placement" not in _kinds(analyze(central_api, CEI))


def test_daemonset_is_exempt_from_placement():
    snapshot = _snapshot(
        workloads=[_workload("agent", kind="DaemonSet", replicas=1)],
        pods=[_pod("agent-1", "agent", "node-a")],
    )
    assert "single_node_placement" not in _kinds(analyze(snapshot, {}))


def test_unscheduled_pods_do_not_count_as_placement(central_api):
    """A Pending pod has no node. That is health.py's finding, not this one."""
    central_api["pods"] = [_pod("api-1", "api", "node-a"),
                           dict(_pod("api-2", "api", "node-a"), node_name=None)]
    finding = _find(analyze(central_api, CEI), "single_node_placement")

    assert finding["evidence"]["nodes"] == ["node-a"]


# --- disruption budgets ----------------------------------------------------


def test_pdb_allowing_zero_disruptions_blocks_drains(central_api):
    central_api["disruption_budgets"] = [{
        "name": "api-pdb", "namespace": "prod", "selector": {"app": "api"},
        "min_available": "2", "max_unavailable": None,
        "disruptions_allowed": 0, "current_healthy": 2,
        "desired_healthy": 2, "expected_pods": 2,
    }]
    finding = _find(analyze(central_api, CEI), "disruption_budget_blocks_drain")

    assert finding["severity"] == "warning"
    assert "drain" in finding["detail"]


def test_pdb_permitting_full_eviction_is_useless(central_api):
    central_api["disruption_budgets"] = [{
        "name": "api-pdb", "namespace": "prod", "selector": {"app": "api"},
        "min_available": None, "max_unavailable": "2",
        "disruptions_allowed": 2, "current_healthy": 2,
        "desired_healthy": 0, "expected_pods": 2,
    }]
    finding = _find(analyze(central_api, CEI), "disruption_budget_permits_full_eviction")

    assert "no protection" in finding["detail"]


def test_healthy_pdb_produces_no_finding(central_api):
    central_api["disruption_budgets"] = [{
        "name": "api-pdb", "namespace": "prod", "selector": {"app": "api"},
        "min_available": "1", "max_unavailable": None,
        "disruptions_allowed": 1, "current_healthy": 2,
        "desired_healthy": 1, "expected_pods": 2,
    }]
    kinds = _kinds(analyze(central_api, CEI))

    assert not any(k.startswith("disruption_budget") for k in kinds)
    assert "missing_disruption_budget" not in kinds


def test_missing_pdb_flagged_only_when_central(central_api):
    result = analyze(central_api, CEI)
    missing = [f for f in result["findings"] if f["kind"] == "missing_disruption_budget"]

    assert [f["workload_key"] for f in missing] == ["prod/Deployment/api"]


def test_missing_pdb_not_flagged_on_isolated_workload():
    """A workload nothing depends on does not need a budget to be safe."""
    snapshot = _snapshot(
        workloads=[_workload("scratch")],
        pods=[_pod("s-1", "scratch", "node-a"), _pod("s-2", "scratch", "node-b")],
    )
    assert "missing_disruption_budget" not in _kinds(analyze(snapshot, CEI))


def test_pdb_selector_matches_by_subset(central_api):
    """A PDB selecting {app: api} governs pods labelled {app: api, ver: v2}."""
    central_api["workloads"][0]["pod_labels"] = {"app": "api", "ver": "v2"}
    central_api["disruption_budgets"] = [{
        "name": "api-pdb", "namespace": "prod", "selector": {"app": "api"},
        "min_available": "2", "max_unavailable": None, "disruptions_allowed": 0,
        "current_healthy": 2, "desired_healthy": 2, "expected_pods": 2,
    }]
    assert "disruption_budget_blocks_drain" in _kinds(analyze(central_api, CEI))


def test_pdb_in_another_namespace_does_not_match(central_api):
    central_api["disruption_budgets"] = [{
        "name": "api-pdb", "namespace": "other", "selector": {"app": "api"},
        "min_available": "2", "max_unavailable": None, "disruptions_allowed": 0,
        "current_healthy": 2, "desired_healthy": 2, "expected_pods": 2,
    }]
    kinds = _kinds(analyze(central_api, CEI))

    assert "disruption_budget_blocks_drain" not in kinds
    assert "missing_disruption_budget" in kinds


@pytest.mark.parametrize("value,replicas,expected", [
    ("2", 4, 2), (2, 4, 2), ("50%", 4, 2), ("25%", 8, 2),
    ("100%", 3, 3), ("0", 3, 0), (None, 3, None), ("garbage", 3, None),
])
def test_quantity_parsing(value, replicas, expected):
    assert _parse_quantity(value, replicas) == expected


# --- readiness -------------------------------------------------------------


def test_missing_readiness_probe_on_central_workload(central_api):
    central_api["workloads"][0]["probes"] = {
        "containers": 2, "with_readiness": 0, "with_liveness": 2, "with_startup": 0,
    }
    finding = _find(analyze(central_api, CEI), "missing_readiness_probe")

    assert finding["evidence"] == {"containers": 2, "with_readiness": 0}


def test_partial_readiness_coverage_is_flagged(central_api):
    central_api["workloads"][0]["probes"] = {
        "containers": 3, "with_readiness": 2, "with_liveness": 3, "with_startup": 0,
    }
    finding = _find(analyze(central_api, CEI), "missing_readiness_probe")

    assert "1 of 3 containers" in finding["detail"]


def test_full_readiness_coverage_passes(central_api):
    assert "missing_readiness_probe" not in _kinds(analyze(central_api, CEI))


# --- autoscaling -----------------------------------------------------------


def test_hpa_with_min_equal_to_max_cannot_scale(central_api):
    central_api["autoscalers"] = [{
        "name": "api-hpa", "namespace": "prod", "target_kind": "Deployment",
        "target_name": "api", "min_replicas": 2, "max_replicas": 2,
        "current_replicas": 2, "desired_replicas": 2,
    }]
    finding = _find(analyze(central_api, CEI), "autoscaler_at_ceiling")

    assert "cannot scale" in finding["title"]
    assert "fixed replica count" in finding["detail"]


def test_hpa_pinned_at_ceiling_with_range(central_api):
    central_api["autoscalers"] = [{
        "name": "api-hpa", "namespace": "prod", "target_kind": "Deployment",
        "target_name": "api", "min_replicas": 2, "max_replicas": 10,
        "current_replicas": 10, "desired_replicas": 10,
    }]
    finding = _find(analyze(central_api, CEI), "autoscaler_at_ceiling")

    assert "pinned at its ceiling" in finding["title"]
    assert "no headroom" in finding["detail"]


def test_hpa_with_headroom_is_not_flagged(central_api):
    central_api["autoscalers"] = [{
        "name": "api-hpa", "namespace": "prod", "target_kind": "Deployment",
        "target_name": "api", "min_replicas": 2, "max_replicas": 10,
        "current_replicas": 4, "desired_replicas": 4,
    }]
    assert "autoscaler_at_ceiling" not in _kinds(analyze(central_api, CEI))


# --- the combination -------------------------------------------------------


def test_compounding_weaknesses_escalate_to_critical(central_api):
    """
    Three defaults that each review as unremarkable. Together they describe a
    user-facing workload that cannot survive a node drain.
    """
    central_api["pods"] = [_pod("api-1", "api", "node-a"), _pod("api-2", "api", "node-a")]
    central_api["workloads"][0]["probes"] = {
        "containers": 1, "with_readiness": 0, "with_liveness": 1, "with_startup": 0,
    }
    result = analyze(central_api, CEI)

    finding = _find(result, "fragile_critical_workload")
    assert finding["severity"] == "critical"
    assert finding["evidence"]["user_facing"] is True
    assert len(finding["evidence"]["weaknesses"]) >= 2
    assert result["summary"]["fragile_workloads"] == 1


def test_single_weakness_does_not_escalate(central_api):
    central_api["workloads"][0]["probes"] = {
        "containers": 1, "with_readiness": 0, "with_liveness": 1, "with_startup": 0,
    }
    central_api["disruption_budgets"] = [{
        "name": "api-pdb", "namespace": "prod", "selector": {"app": "api"},
        "min_available": "1", "max_unavailable": None, "disruptions_allowed": 1,
        "current_healthy": 2, "desired_healthy": 1, "expected_pods": 2,
    }]
    result = analyze(central_api, CEI)

    assert "fragile_critical_workload" not in _kinds(result)
    assert result["summary"]["fragile_workloads"] == 0


def test_weaknesses_on_isolated_workload_do_not_escalate():
    """
    The same three defaults on something nothing depends on. Still reported
    individually, never escalated -- that distinction is the whole design.
    """
    snapshot = _snapshot(
        workloads=[_workload("scratch", probes={
            "containers": 1, "with_readiness": 0, "with_liveness": 0, "with_startup": 0})],
        pods=[_pod("s-1", "scratch", "node-a"), _pod("s-2", "scratch", "node-a")],
    )
    result = analyze(snapshot, CEI)

    assert result["summary"]["fragile_workloads"] == 0
    assert "single_node_placement" in _kinds(result)
    assert _find(result, "single_node_placement")["severity"] == "info"


def test_fragile_list_ranks_by_dependents(central_api):
    central_api["pods"] = [_pod("api-1", "api", "node-a"), _pod("api-2", "api", "node-a")]
    central_api["workloads"][0]["probes"] = {
        "containers": 1, "with_readiness": 0, "with_liveness": 1, "with_startup": 0}
    result = analyze(central_api, CEI)

    assert result["fragile_workloads"][0]["workload_key"] == "prod/Deployment/api"
    assert result["fragile_workloads"][0]["dependents"] == 1


# --- shared configuration --------------------------------------------------


def test_configmap_fan_in_is_reported():
    workloads = [
        _workload(f"svc{i}", config_maps=["shared-config"]) for i in range(4)
    ]
    result = analyze(_snapshot(workloads), {})

    finding = _find(result, "shared_config_fan_in")
    assert finding["evidence"]["config_map"] == "shared-config"
    assert len(finding["evidence"]["readers"]) == 4


def test_configmap_below_threshold_is_ignored():
    workloads = [_workload("a", config_maps=["c"]), _workload("b", config_maps=["c"])]
    assert CONFIG_FAN_IN_THRESHOLD == 3
    assert "shared_config_fan_in" not in _kinds(analyze(_snapshot(workloads), {}))


def test_configmap_fan_in_is_namespace_scoped():
    """Two namespaces each with a `config` ConfigMap are two separate objects."""
    workloads = (
        [_workload(f"a{i}", ns="ns1", config_maps=["config"]) for i in range(2)]
        + [_workload(f"b{i}", ns="ns2", config_maps=["config"]) for i in range(2)]
    )
    assert "shared_config_fan_in" not in _kinds(analyze(_snapshot(workloads), {}))


def test_wide_fan_in_escalates_severity():
    workloads = [_workload(f"svc{i}", config_maps=["shared"]) for i in range(6)]
    assert _find(analyze(_snapshot(workloads), {}), "shared_config_fan_in")["severity"] == "warning"


# --- degradation and ordering ----------------------------------------------


def test_snapshot_without_resilience_data_still_works(central_api):
    """A v1 agent sends no PDBs or HPAs. Placement findings must still work."""
    del central_api["disruption_budgets"]
    del central_api["autoscalers"]
    central_api["pods"] = [_pod("api-1", "api", "node-a"), _pod("api-2", "api", "node-a")]
    result = analyze(central_api, CEI)

    assert "single_node_placement" in _kinds(result)
    assert not any(k.startswith("disruption_budget_") for k in _kinds(result))


def test_empty_snapshot():
    result = analyze({"workloads": [], "pods": [], "edges": []}, {})

    assert result["summary"]["total"] == 0
    assert result["findings"] == []


def test_findings_sort_critical_first_then_by_blast_radius(central_api):
    central_api["pods"] = [_pod("api-1", "api", "node-a"), _pod("api-2", "api", "node-a")]
    central_api["workloads"][0]["probes"] = {
        "containers": 1, "with_readiness": 0, "with_liveness": 1, "with_startup": 0}
    findings = analyze(central_api, CEI)["findings"]

    assert findings[0]["kind"] == "fragile_critical_workload"
    severities = [f["severity"] for f in findings]
    assert severities == sorted(severities, key=lambda s: {"critical": 0, "warning": 1, "info": 2}[s])


def test_analysis_runs_without_cei_scores(central_api):
    result = analyze(central_api, None)

    assert result["summary"]["total"] >= 0
    assert all(f["cei_score"] is None or isinstance(f["cei_score"], float)
               for f in result["findings"])
