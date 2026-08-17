"""
Phase C: intervention sets, metastability detection, provider substrate,
pre-scale playbook, carbon, complexity, GPU fragmentation.
"""

import pytest

from src.services import carbon, complexity, fleet, prescribe, recovery
from src.services.control_plane import gpu_fragmentation


def _workload(name, *, ns="prod", replicas=2, cpu=1.0, gpus=0):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": replicas, "replicas_ready": replicas,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "images": ["repo/app@sha256:abc"],
        "cpu_cores_used": cpu * 0.4, "cpu_cores_requested": cpu,
        "cpu_cores_requested_per_pod": cpu / max(1, replicas),
        "gpus_requested_per_pod": gpus,
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _edge(src, dst, ns="prod"):
    return {"source": f"{ns}/Deployment/{src}", "target": f"{ns}/Deployment/{dst}",
            "confidence": 0.9, "source_kind": "env_reference"}


def _ingress(target, ns="prod"):
    return {"source": f"{ns}/Ingress/public", "target": f"{ns}/Deployment/{target}",
            "confidence": 1.0, "source_kind": "ingress"}


@pytest.fixture
def hub_cluster():
    return {
        "workloads": [_workload("hub", replicas=1)]
        + [_workload(f"svc{i}") for i in range(5)],
        "edges": [_edge(f"svc{i}", "hub") for i in range(5)] + [_ingress("svc0")],
        "nodes": [{"name": "n1", "ready": True, "unschedulable": False,
                   "allocatable_cpu_cores": 32.0,
                   "allocatable_memory_bytes": 64 * 1024**3,
                   "instance_type": "m5.2xlarge", "region": "us-east-1"}],
        "pods": [], "services": [], "disruption_budgets": [], "autoscalers": [],
    }


# ═══ intervention sets ═══════════════════════════════════════════════════


def test_set_selection_stops_when_nothing_clears_the_bar():
    """On a healthy 2-replica topology the honest set is empty."""
    snapshot = {
        "workloads": [_workload("a"), _workload("b")],
        "edges": [_edge("a", "b"), _ingress("a")],
        "nodes": [], "pods": [], "services": [],
        "disruption_budgets": [], "autoscalers": [],
    }
    result = prescribe.prescribe_set(snapshot, trials=2000)

    assert result["available"] is True
    assert result["selected"] == []
    assert result["total_risk_reduction_usd"] == 0.0


def test_set_selection_picks_the_single_replica_hub(hub_cluster):
    result = prescribe.prescribe_set(
        hub_cluster, trials=4000, downtime_cost_per_hour=50_000
    )

    assert any("hub" in (p.get("target") or "") for p in result["selected"])
    assert result["final_risk_usd"] < result["baseline_risk_usd"]


def test_set_selection_reports_marginal_not_solo_benefit(hub_cluster):
    """The second pick's benefit is what it adds ON TOP of the first."""
    result = prescribe.prescribe_set(
        hub_cluster, trials=3000, downtime_cost_per_hour=50_000
    )
    if len(result["selected"]) >= 2:
        # Risk after each pick is monotone non-increasing.
        risks = [p["risk_after_usd"] for p in result["selected"]]
        assert risks == sorted(risks, reverse=True)
    for pick in result["selected"]:
        assert pick["marginal_net_benefit_usd"] > 0


def test_budget_bounds_added_spend(hub_cluster):
    result = prescribe.prescribe_set(
        hub_cluster, trials=2000, downtime_cost_per_hour=50_000, budget_usd=0.0
    )

    # Zero budget: only savings or free moves can be selected.
    assert result["total_added_annual_cost_usd"] == 0.0


def test_set_is_deterministic(hub_cluster):
    a = prescribe.prescribe_set(hub_cluster, trials=2000)
    b = prescribe.prescribe_set(hub_cluster, trials=2000)

    assert a["selected"] == b["selected"]


# ═══ metastability ═══════════════════════════════════════════════════════


def _series(values):
    return [{"cpu_cores_used": v} for v in values]


def test_metastable_signature_is_detected():
    """Baseline ~1.0, dip, recovery at ~2.0: recovered but load did not."""
    history = _series([1.0, 1.1, 0.9, 1.0, 1.05, 0.1, 1.9, 2.1, 2.0, 2.05])
    verdict = recovery.detect_metastability(history)

    assert verdict["detectable"] is True
    assert verdict["metastable_suspected"] is True
    assert verdict["load_ratio"] >= 1.5
    assert "retry" in verdict["note"]


def test_clean_recovery_is_not_flagged():
    history = _series([1.0, 1.1, 0.9, 1.0, 1.05, 0.1, 0.95, 1.0, 1.05, 0.98])
    verdict = recovery.detect_metastability(history)

    assert verdict["metastable_suspected"] is False
    assert "clean" in verdict["note"]


def test_median_windows_resist_a_baseline_spike():
    """One retry spike in the baseline must not hide the pattern."""
    history = _series([1.0, 9.0, 1.0, 1.1, 0.9, 0.1, 2.0, 2.1, 1.9, 2.0])
    verdict = recovery.detect_metastability(history)

    assert verdict["metastable_suspected"] is True


def test_too_little_history_says_so():
    verdict = recovery.detect_metastability(_series([1.0, 0.1, 1.0]))

    assert verdict["detectable"] is False
    assert "samples" in verdict["reason"]


def test_none_values_are_skipped():
    history = _series([1.0, None, 1.0, 1.1, 0.9, 0.1, 2.0, None, 2.1, 1.9, 2.0])
    assert recovery.detect_metastability(history)["detectable"] is True


# ═══ pre-scale playbook ══════════════════════════════════════════════════


def test_playbook_orders_user_facing_first():
    snapshot = {
        "workloads": [_workload("db"), _workload("api"), _workload("batch")],
        "edges": [_edge("api", "db"), _edge("batch", "db"), _ingress("api")],
        "nodes": [], "pods": [], "services": [],
        "disruption_budgets": [], "autoscalers": [],
    }
    playbook = recovery.pre_scale_playbook(snapshot, "prod/Deployment/db")

    assert playbook["found"] is True
    assert playbook["steps"][0]["workload_key"] == "prod/Deployment/api"
    assert playbook["steps"][0]["user_facing"] is True
    assert playbook["steps"][0]["suggested_replicas"] > playbook["steps"][0]["current_replicas"]
    assert "Advisory" in playbook["note"]


def test_playbook_for_unknown_upstream():
    assert recovery.pre_scale_playbook({"workloads": [], "edges": []}, "x/y/z")["found"] is False


# ═══ provider substrate ══════════════════════════════════════════════════


def _egress_cluster(name, provider, endpoint):
    return {"name": name, "provider": provider, "snapshot": {
        "workloads": [_workload("api")],
        "edges": [],
        "egress": {"available": True, "workloads": {
            "prod/Deployment/api": [{"destination": endpoint, "ports": [443],
                                     "dns_resolved": True, "flow_count": 3,
                                     "dropped_count": 0, "ips": []}]}},
    }}


def test_substrate_overlap_catches_diversification_that_is_not():
    """An AWS cluster using Auth0 shares AWS's failure domain twice."""
    findings = fleet.substrate_overlaps([
        _egress_cluster("prod", "eks", "tenant.auth0.com"),
    ])

    # Two overlaps, both real: Auth0 runs on AWS, and so does Docker Hub
    # (the fixture's bare image name implies it). The identity one is the
    # warning; the registry one is informational.
    by_endpoint = {f["endpoint"]: f for f in findings}
    assert set(by_endpoint) == {"tenant.auth0.com", "docker.io"}
    finding = by_endpoint["tenant.auth0.com"]
    assert finding["severity"] == "warning"
    assert by_endpoint["docker.io"]["severity"] == "info"
    assert finding["shared_substrate"] == "aws"
    assert finding["provenance"] == "assumed_public_knowledge"
    assert "lead for an architecture review" in finding["detail"]


def test_substrate_overlap_ignores_different_clouds():
    """Auth0-on-AWS is genuine diversification for an Azure cluster."""
    assert fleet.substrate_overlaps([
        _egress_cluster("prod", "aks", "tenant.auth0.com"),
    ]) == []


def test_multi_substrate_providers_are_not_flagged():
    findings = fleet.substrate_overlaps([
        _egress_cluster("prod", "eks", "app.datadoghq.com"),
    ])
    # Datadog is multi-substrate and must not be flagged; the fixture's
    # Docker Hub registry legitimately is.
    assert "app.datadoghq.com" not in {f["endpoint"] for f in findings}


# ═══ carbon ══════════════════════════════════════════════════════════════


def test_carbon_estimate_uses_the_node_region(hub_cluster):
    result = carbon.estimate(hub_cluster)

    assert result["region"] == "us-east-1"
    assert result["region_known"] is True
    assert result["total_annual_kwh_estimate"] > 0
    assert "RELATIVE" in result["assumptions"]["note"]


def test_unknown_region_uses_global_default_and_says_so(hub_cluster):
    hub_cluster["nodes"][0]["region"] = "mars-north-1"
    result = carbon.estimate(hub_cluster)

    assert result["region_known"] is False
    assert result["region_intensity_g_per_kwh"] == carbon.GLOBAL_DEFAULT_INTENSITY
    assert "global average" in result["assumptions"]["note"]


def test_region_move_carries_the_fragility_warning(hub_cluster):
    result = carbon.region_move_delta(hub_cluster, "eu-north-1")

    assert result["available"] is True
    assert result["annual_kg_co2e_delta_estimate"] < 0  # greener
    assert "concentration decision" in result["warning"]


def test_region_move_to_unknown_region_is_refused(hub_cluster):
    assert carbon.region_move_delta(hub_cluster, "nowhere-1")["available"] is False


# ═══ complexity ══════════════════════════════════════════════════════════


def test_complexity_rises_with_kinds_and_wiring():
    simple = {
        "workloads": [_workload(f"w{i}") for i in range(5)],
        "edges": [], "services": [], "ingresses": [],
        "network_policies": [], "disruption_budgets": [], "autoscalers": [],
    }
    tangled = {
        "workloads": (
            [_workload(f"w{i}", ns=f"ns{i%6}") for i in range(20)]
            + [dict(_workload(f"s{i}", ns=f"ns{i%6}"), kind="StatefulSet",
                    key=f"ns{i%6}/StatefulSet/s{i}") for i in range(8)]
        ),
        "edges": [
            {"source": f"ns{i%6}/Deployment/w{i}",
             "target": f"ns{(i+1)%6}/Deployment/w{(i+7)%20}",
             "confidence": 0.9, "source_kind": "env_reference"}
            for i in range(20)
        ],
        "services": [{"name": f"svc{i}"} for i in range(20)],
        "ingresses": [{"name": "public"}],
        "network_policies": [{"name": f"np{i}"} for i in range(6)],
        "disruption_budgets": [], "autoscalers": [],
    }
    low = complexity.index(simple)
    high = complexity.index(tangled)

    assert high["index"] > low["index"]
    assert high["subscores"]["diversity"] > low["subscores"]["diversity"]
    assert high["counts"]["cross_namespace_edges"] > 0


def test_complexity_subscores_are_bounded():
    result = complexity.index({
        "workloads": [_workload(f"w{i}", ns=f"ns{i}") for i in range(100)],
        "edges": [], "services": [], "ingresses": [],
        "network_policies": [], "disruption_budgets": [], "autoscalers": [],
    })
    assert all(0 <= v <= 1 for v in result["subscores"].values())
    assert 0 <= result["index"] <= 1


def test_empty_cluster_scores_zero():
    assert complexity.index({"workloads": [], "edges": []})["index"] == 0.0


# ═══ GPU fragmentation ═══════════════════════════════════════════════════


def _gpu_node(name, gpus):
    return {"name": name, "ready": True, "unschedulable": False,
            "allocatable_cpu_cores": 64.0,
            "allocatable_memory_bytes": 512 * 1024**3,
            "allocatable_gpus": gpus}


def test_free_gpus_below_the_largest_shape_is_stranding_risk():
    """6 GPUs free across 3 nodes cannot schedule one 4-GPU pod."""
    snapshot = {
        "workloads": [
            _workload("train", replicas=1, gpus=4),
            _workload("infer", replicas=2, gpus=1),
        ],
        "nodes": [_gpu_node(f"g{i}", 4) for i in range(3)],
        "edges": [], "pods": [], "services": [],
    }
    result = gpu_fragmentation(snapshot)

    assert result["available"] is True
    assert result["total_gpus"] == 12
    assert result["requested_gpus"] == 6
    assert result["free_gpus_total"] == 6
    assert result["largest_request_shape"] == 4
    assert result["stranding_risk"] is True
    assert "single node" in result["note"]


def test_no_gpu_nodes_is_unavailable_not_zero():
    result = gpu_fragmentation({"workloads": [], "nodes": [_gpu_node("n", 0)]})

    assert result["available"] is False


def test_ample_contiguous_capacity_is_not_stranding():
    snapshot = {
        "workloads": [_workload("infer", replicas=1, gpus=1)],
        "nodes": [_gpu_node("g0", 8)],
        "edges": [], "pods": [], "services": [],
    }
    assert gpu_fragmentation(snapshot)["stranding_risk"] is False
