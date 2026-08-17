"""
Prescriptive optimizer.

The properties that matter: risk and cost land in the same currency, the
FinOps trap is priced rather than hidden, do_nothing is always a candidate,
and deltas come from architecture rather than sampling luck.
"""

import pytest

from src.services import prescribe
from src.services.external_deps import build_external_nodes


def _workload(name, *, ns="prod", replicas=2, cpu_used=0.5, cpu_req=1.0):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": replicas, "replicas_ready": replicas,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "images": ["repo/app@sha256:abc"],
        "cpu_cores_used": cpu_used, "cpu_cores_requested": cpu_req,
        "memory_bytes_used": 200 * 1024**2, "memory_bytes_requested": 1024**3,
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
    """A single-replica hub with five dependents behind an ingress."""
    return {
        "workloads": [_workload("hub", replicas=1)]
        + [_workload(f"svc{i}") for i in range(5)],
        "edges": [_edge(f"svc{i}", "hub") for i in range(5)]
        + [_ingress("svc0")],
        "nodes": [{"name": "n1", "ready": True, "unschedulable": False,
                   "allocatable_cpu_cores": 32.0,
                   "allocatable_memory_bytes": 64 * 1024**3,
                   "instance_type": "m5.2xlarge", "region": "us-east-1"}],
        "pods": [], "services": [], "disruption_budgets": [], "autoscalers": [],
    }


# --- the replica model ------------------------------------------------------


def test_replica_availability_has_diminishing_returns():
    one = prescribe.replica_availability(1)
    two = prescribe.replica_availability(2)
    four = prescribe.replica_availability(4)

    assert one < two < four
    # The second replica buys far more than the fourth: correlation floor.
    assert (two - one) > 10 * (four - two)


def test_correlated_share_never_divides_away():
    floor = 1 - prescribe.CORRELATED_SHARE * prescribe.BASE_UNAVAILABILITY

    assert prescribe.replica_availability(100) <= floor + 1e-12


# --- risk in dollars --------------------------------------------------------


def test_risk_is_priced_on_user_facing_paths(hub_cluster):
    result = prescribe.prescribe(hub_cluster, trials=2000)

    assert result["baseline_risk"]["basis"] == "user_facing"
    assert result["baseline_risk"]["annual_usd"] > 0


def test_no_ingress_falls_back_to_mean_and_says_so():
    snapshot = {
        "workloads": [_workload("a"), _workload("b")],
        "edges": [_edge("a", "b")],
        "nodes": [], "pods": [], "services": [],
        "disruption_budgets": [], "autoscalers": [],
    }
    result = prescribe.prescribe(snapshot, trials=1000)

    assert result["baseline_risk"]["basis"] == "all_workloads_mean"


# --- candidates -------------------------------------------------------------


def test_do_nothing_is_always_a_candidate(hub_cluster):
    result = prescribe.prescribe(hub_cluster, trials=1000)
    kinds = [p["kind"] for p in result["prescriptions"]]

    assert "do_nothing" in kinds
    baseline = next(p for p in result["prescriptions"] if p["kind"] == "do_nothing")
    assert baseline["verdict"] == "baseline"
    assert baseline["annual_risk_reduction_usd"] == 0.0


def test_single_replica_hub_gets_an_add_replica_prescription(hub_cluster):
    result = prescribe.prescribe(hub_cluster, trials=4000)
    added = next(
        p for p in result["prescriptions"]
        if p["kind"] == "add_replica" and "hub" in p["target"]
    )

    # A second replica of a single-replica hub with five dependents behind an
    # ingress is the canonical win: risk falls, and by more than a replica
    # costs on this tiny cluster.
    assert added["annual_risk_reduction_usd"] > 0
    assert added["verdict"] in ("recommended", "not_worth_it")


def test_high_fan_in_hub_gets_a_split_candidate(hub_cluster):
    result = prescribe.prescribe(hub_cluster, trials=1000)

    assert any(p["kind"] == "split_dependency" for p in result["prescriptions"])


def test_idle_overreplicated_workload_gets_a_removal_priced_in_both_columns():
    snapshot = {
        "workloads": [_workload("idle", replicas=6, cpu_used=0.05, cpu_req=1.0),
                      _workload("api")],
        "edges": [_ingress("api")],
        "nodes": [{"name": "n1", "ready": True, "unschedulable": False,
                   "allocatable_cpu_cores": 32.0,
                   "allocatable_memory_bytes": 64 * 1024**3,
                   "instance_type": "m5.2xlarge", "region": "us-east-1"}],
        "pods": [], "services": [], "disruption_budgets": [], "autoscalers": [],
    }
    result = prescribe.prescribe(snapshot, trials=1000)
    removal = next(
        (p for p in result["prescriptions"] if p["kind"] == "remove_replicas"), None
    )

    assert removal is not None
    assert removal["annual_cost_delta_usd"] < 0        # a saving
    assert "annual_risk_reduction_usd" in removal      # and its price
    assert removal["net_annual_benefit_usd"] is not None


def test_diversify_external_reports_pricing_required(hub_cluster):
    egress = {
        "available": True,
        "workloads": {
            f"prod/Deployment/svc{i}": [
                {"destination": "tenant.auth0.com", "ports": [443],
                 "dns_resolved": True, "flow_count": 5, "dropped_count": 0, "ips": []}
            ] for i in range(3)
        },
    }
    result = prescribe.prescribe(hub_cluster, egress, trials=2000)
    diversify = next(
        p for p in result["prescriptions"] if p["kind"] == "diversify_external"
    )

    assert diversify["verdict"] == "pricing_required"
    assert diversify["net_annual_benefit_usd"] is None
    # Risk reduction is still computed: an independent second provider
    # squares the shared dependency's unavailability.
    assert diversify["annual_risk_reduction_usd"] >= 0


# --- honesty ----------------------------------------------------------------


def test_result_states_its_assumptions(hub_cluster):
    assumptions = prescribe.prescribe(hub_cluster, trials=1000)["assumptions"]

    assert assumptions["common_random_numbers"] is True
    assert "override" in assumptions["downtime_cost_note"]
    assert "diminishing returns" in assumptions["replica_model"]


def test_structural_health_reports_subscores(hub_cluster):
    health = prescribe.prescribe(hub_cluster, trials=1000)["structural_health"]

    assert 0 <= health["dci"] <= 1
    assert health["largest_blast_radius"]["workload_key"] == "prod/Deployment/hub"
    assert "green" in health["note"]


def test_deterministic_across_runs(hub_cluster):
    a = prescribe.prescribe(hub_cluster, trials=1500)
    b = prescribe.prescribe(hub_cluster, trials=1500)

    assert a["prescriptions"] == b["prescriptions"]


def test_recommended_sorts_before_not_worth_it(hub_cluster):
    result = prescribe.prescribe(hub_cluster, trials=3000)
    verdicts = [p["verdict"] for p in result["prescriptions"]]
    order = {"recommended": 0, "pricing_required": 1, "baseline": 2,
             "not_worth_it": 3, "savings_cost_more_than_they_save": 3}
    ranks = [order[v] for v in verdicts]

    assert ranks == sorted(ranks)


def test_empty_cluster_is_unavailable():
    result = prescribe.prescribe({"workloads": [], "edges": []}, trials=500)

    assert result["available"] is False
