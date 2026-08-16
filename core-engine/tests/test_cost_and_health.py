"""
Phase 2: cost allocation, waste detection, and health diagnostics.

The dollar figure is the headline the product is sold on, so the properties
that keep it honest are tested explicitly: allocation cannot exceed the bill,
unmeasured workloads are never called wasteful, and pricing confidence is
always reported.
"""

from __future__ import annotations

import pytest

from src.services.cost import (
    CPU_COST_SHARE,
    MEM_COST_SHARE,
    MIN_REPORTABLE_MONTHLY_WASTE,
    analyze_cluster_cost,
    price_node,
)
from src.services.health import diagnose

GIB = 1024 ** 3


def _node(name="n1", instance_type="m5.xlarge", cpu=4.0, memory=16 * GIB, labels=None):
    return {
        "name": name,
        "instance_type": instance_type,
        "allocatable_cpu_cores": cpu,
        "allocatable_memory_bytes": memory,
        "labels": labels or {},
    }


def _workload(name, *, cpu_req=1.0, cpu_used=0.1, mem_req=GIB, mem_used=GIB // 10,
              replicas=1, namespace="default", kind="Deployment"):
    return {
        "key": f"{namespace}/{kind}/{name}",
        "name": name,
        "namespace": namespace,
        "kind": kind,
        "pod_labels": {"app": name},
        "replicas_desired": replicas,
        "replicas_ready": replicas,
        "cpu_cores_requested": cpu_req,
        "cpu_cores_used": cpu_used,
        "memory_bytes_requested": mem_req,
        "memory_bytes_used": mem_used,
    }


# --------------------------------------------------------------------------
# Node pricing
# --------------------------------------------------------------------------

def test_known_instance_type_is_priced_from_the_table():
    cost = price_node(_node(instance_type="m5.xlarge"))
    assert cost.basis == "instance_type"
    assert cost.monthly_usd == pytest.approx(0.192 * 730, rel=1e-3)


def test_unknown_instance_type_falls_back_to_capacity_and_says_so():
    """Directionally right, precisely wrong -- and labelled as such."""
    cost = price_node(_node(instance_type=None, cpu=4.0, memory=16 * GIB))
    assert cost.basis == "estimated_from_capacity"
    assert cost.monthly_usd > 0


def test_node_with_no_usable_information_is_priced_at_zero_not_guessed():
    """
    Zero is visible in the output and reconciles to nothing. A guess would
    silently contaminate the cluster total.
    """
    cost = price_node({"name": "mystery"})
    assert cost.basis == "unknown"
    assert cost.monthly_usd == 0.0


def test_provider_is_inferred_from_node_labels():
    azure = price_node(_node(labels={"kubernetes.azure.com/cluster": "x"}))
    gcp = price_node(_node(labels={"cloud.google.com/gke-nodepool": "default"}))
    assert azure.provider == "azure"
    assert gcp.provider == "gcp"


# --------------------------------------------------------------------------
# Allocation
# --------------------------------------------------------------------------

def test_allocated_cost_never_exceeds_the_cluster_bill():
    """
    Allocation must reconcile. If shares summed instead of taking the binding
    dimension, a cluster could allocate more than it costs, and every figure
    downstream would be indefensible.
    """
    snapshot = {
        "nodes": [_node()],
        "workloads": [
            _workload("a", cpu_req=1.0, mem_req=8 * GIB),
            _workload("b", cpu_req=2.0, mem_req=2 * GIB),
            _workload("c", cpu_req=0.5, mem_req=4 * GIB),
        ],
    }
    result = analyze_cluster_cost(snapshot)
    summary = result["summary"]
    assert summary["allocated_monthly_usd"] <= summary["cluster_monthly_usd"] + 0.01


def test_each_dimension_is_charged_against_its_own_share_of_node_price():
    """
    A workload reserving 90% of memory and 5% of CPU pays 90% of the memory
    portion plus 5% of the CPU portion, not 90% of the whole node.

    Charging the binding dimension would be more intuitive, but summed across
    workloads it can allocate more than the cluster costs -- see
    test_allocated_cost_never_exceeds_the_cluster_bill.
    """
    snapshot = {
        "nodes": [_node(cpu=4.0, memory=16 * GIB)],
        "workloads": [_workload("memory_hog", cpu_req=0.2, mem_req=int(14.4 * GIB))],
    }
    result = analyze_cluster_cost(snapshot)
    node_monthly = result["summary"]["cluster_monthly_usd"]
    allocated = result["workloads"][0]["monthly_usd"]

    expected = node_monthly * (CPU_COST_SHARE * 0.05 + MEM_COST_SHARE * 0.90)
    assert allocated == pytest.approx(expected, rel=0.02)
    # Memory dominates the charge, as it dominates the reservation.
    assert allocated > node_monthly * CPU_COST_SHARE * 0.05 * 2


def test_a_full_node_allocates_the_whole_node_price():
    """Two workloads that between them reserve everything account for it all."""
    snapshot = {
        "nodes": [_node(cpu=4.0, memory=16 * GIB)],
        "workloads": [
            _workload("a", cpu_req=2.0, mem_req=8 * GIB),
            _workload("b", cpu_req=2.0, mem_req=8 * GIB),
        ],
    }
    summary = analyze_cluster_cost(snapshot)["summary"]
    assert summary["allocated_monthly_usd"] == pytest.approx(
        summary["cluster_monthly_usd"], rel=1e-6
    )
    assert summary["unallocated_monthly_usd"] == pytest.approx(0.0, abs=0.01)


def test_unallocated_capacity_is_reported_separately():
    """
    Idle node capacity is real spend but a different problem from workload
    waste -- shrink the node pool, not the requests. Folding it into the
    waste headline would overstate what rightsizing can recover.
    """
    snapshot = {
        "nodes": [_node(cpu=8.0, memory=32 * GIB)],
        "workloads": [_workload("small", cpu_req=0.1, mem_req=GIB // 2)],
    }
    summary = analyze_cluster_cost(snapshot)["summary"]
    assert summary["unallocated_monthly_usd"] > summary["allocated_monthly_usd"]


# --------------------------------------------------------------------------
# Waste
# --------------------------------------------------------------------------

def test_over_provisioned_workload_is_flagged_with_a_dollar_figure():
    snapshot = {
        "nodes": [_node()],
        "workloads": [_workload("idle", cpu_req=2.0, cpu_used=0.02,
                                mem_req=8 * GIB, mem_used=GIB // 4)],
    }
    result = analyze_cluster_cost(snapshot)
    assert result["summary"]["workloads_over_provisioned"] == 1
    assert result["summary"]["wasted_monthly_usd"] > 0
    assert result["opportunities"][0]["verdict"] == "over_provisioned"


def test_well_utilized_workload_is_not_flagged():
    snapshot = {
        "nodes": [_node()],
        "workloads": [_workload("busy", cpu_req=1.0, cpu_used=0.85,
                                mem_req=GIB, mem_used=int(0.8 * GIB))],
    }
    result = analyze_cluster_cost(snapshot)
    assert result["summary"]["workloads_over_provisioned"] == 0
    assert result["workloads"][0]["verdict"] == "right_sized"


def test_unmeasured_workload_is_never_called_wasteful():
    """
    Without metrics there is no evidence of waste. Recommending a reduction
    from missing data is how a tool loses a customer's trust permanently.
    """
    snapshot = {
        "nodes": [_node()],
        "workloads": [_workload("blind", cpu_used=None, mem_used=None)],
    }
    result = analyze_cluster_cost(snapshot)
    assert result["workloads"][0]["verdict"] == "unmeasured"
    assert result["workloads"][0]["wasted_monthly_usd"] == 0.0
    assert result["summary"]["wasted_monthly_usd"] == 0.0


def test_workload_without_requests_is_a_different_finding():
    snapshot = {
        "nodes": [_node()],
        "workloads": [_workload("norequests", cpu_req=None, mem_req=None)],
    }
    result = analyze_cluster_cost(snapshot)
    assert result["workloads"][0]["verdict"] == "no_requests"
    assert "requests" in result["workloads"][0]["recommendation"]


def test_trivial_savings_are_not_reported():
    """Fifty $3/month findings bury the one worth $400."""
    snapshot = {
        "nodes": [_node(instance_type="t3.small", cpu=2.0, memory=2 * GIB)],
        "workloads": [_workload("tiny", cpu_req=0.01, cpu_used=0.0001,
                                mem_req=GIB // 100, mem_used=GIB // 1000)],
    }
    result = analyze_cluster_cost(snapshot)
    assert result["workloads"][0]["wasted_monthly_usd"] < MIN_REPORTABLE_MONTHLY_WASTE
    assert result["opportunities"] == []


def test_response_always_states_that_figures_are_list_price():
    """A number 40% off presented as exact discredits every other number."""
    result = analyze_cluster_cost({"nodes": [_node()], "workloads": []})
    assert result["basis"]["list_price_estimate"] is True
    assert "Reserved Instances" in result["basis"]["note"]


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

def _pod(name, **kw):
    base = {
        "name": name,
        "namespace": "default",
        "phase": "Running",
        "labels": {"app": name.split("-")[0]},
        "restart_count": 0,
        "waiting_reasons": [],
        "last_terminated_reasons": [],
    }
    base.update(kw)
    return base


def test_crash_loop_is_detected_even_when_sampled_in_the_running_phase():
    """
    The regression this guards: CrashLoopBackOff is transient, so a periodic
    snapshot frequently catches the pod mid-restart with an empty waiting
    list. Detecting only on the waiting reason missed real crash loops most
    of the time.
    """
    snapshot = {
        "pods": [
            _pod("api-1", phase="Running", restart_count=4,
                 last_terminated_reasons=["Error"])
        ],
        "workloads": [_workload("api")],
        "edges": [],
    }
    kinds = [f["kind"] for f in diagnose(snapshot)["findings"]]
    assert "crash_loop" in kinds


def test_crash_loop_is_detected_from_the_waiting_reason_too():
    snapshot = {
        "pods": [_pod("api-1", phase="Pending",
                      waiting_reasons=["CrashLoopBackOff"], restart_count=7)],
        "workloads": [_workload("api")],
        "edges": [],
    }
    assert any(f["kind"] == "crash_loop" for f in diagnose(snapshot)["findings"])


def test_a_restarting_pod_yields_one_finding_not_two():
    snapshot = {
        "pods": [_pod("api-1", restart_count=9,
                      last_terminated_reasons=["Error"])],
        "workloads": [_workload("api")],
        "edges": [],
    }
    kinds = [f["kind"] for f in diagnose(snapshot)["findings"]]
    assert kinds.count("crash_loop") == 1
    assert "frequent_restarts" not in kinds


def test_oom_kill_is_reported():
    snapshot = {
        "pods": [_pod("api-1", last_terminated_reasons=["OOMKilled"],
                      restart_count=1)],
        "workloads": [_workload("api")],
        "edges": [],
    }
    assert any(f["kind"] == "oom_killed" for f in diagnose(snapshot)["findings"])


def test_single_replica_is_only_a_finding_when_something_depends_on_it():
    """A one-replica batch job is fine. A one-replica shared service is not."""
    depended_on = {
        "pods": [],
        "workloads": [_workload("db"), _workload("api")],
        "edges": [{"source": "default/Deployment/api",
                   "target": "default/Deployment/db"}],
    }
    standalone = {
        "pods": [],
        "workloads": [_workload("batch")],
        "edges": [],
    }
    assert any(
        f["kind"] == "single_replica" and "db" in f["title"]
        for f in diagnose(depended_on)["findings"]
    )
    assert not any(
        f["kind"] == "single_replica" for f in diagnose(standalone)["findings"]
    )


def test_findings_are_ranked_by_cei_within_a_severity_band():
    """
    The differentiator. Every dashboard lists crash loops; ordering them by
    how much depends on the workload is the part worth paying for.
    """
    snapshot = {
        "pods": [
            _pod("trivial-1", restart_count=5, last_terminated_reasons=["Error"]),
            _pod("important-1", restart_count=5, last_terminated_reasons=["Error"]),
        ],
        "workloads": [_workload("trivial"), _workload("important")],
        "edges": [],
    }
    cei = {
        "default/Deployment/trivial": {"cei_score": 0.1},
        "default/Deployment/important": {"cei_score": 0.9},
    }
    findings = diagnose(snapshot, cei)["findings"]
    crash = [f for f in findings if f["kind"] == "crash_loop"]
    assert "important" in crash[0]["title"]
    assert crash[0]["cei_score"] == 0.9


def test_critical_outranks_a_higher_cei_warning():
    """Severity dominates; CEI orders within a band, not across bands."""
    snapshot = {
        "pods": [_pod("minor-1", waiting_reasons=["ImagePullBackOff"])],
        "workloads": [_workload("minor"), _workload("major")],
        "edges": [{"source": "default/Deployment/minor",
                   "target": "default/Deployment/major"}],
    }
    cei = {
        "default/Deployment/minor": {"cei_score": 0.1},
        "default/Deployment/major": {"cei_score": 0.99},
    }
    findings = diagnose(snapshot, cei)["findings"]
    assert findings[0]["severity"] == "critical"


# --------------------------------------------------------------------------
# Over-commitment
# --------------------------------------------------------------------------

def test_waste_can_never_exceed_the_cluster_bill():
    """
    Found by the scale test: a cluster requesting more than it can allocate
    reported 108% waste -- $9,728 wasted against an $8,970 bill. Impossible,
    and exactly the kind of number that ends a sales conversation.

    Requests exceeding capacity is normally impossible (the scheduler refuses
    to place a pod that does not fit) but happens transiently mid-autoscale or
    when node data is stale.
    """
    snapshot = {
        "nodes": [_node(cpu=4.0, memory=16 * GIB)],
        "workloads": [
            _workload(f"w{i}", cpu_req=2.0, cpu_used=0.01,
                      mem_req=8 * GIB, mem_used=GIB // 10)
            for i in range(6)  # 12 cores requested against 4 allocatable
        ],
    }
    summary = analyze_cluster_cost(snapshot)["summary"]

    assert summary["over_committed"] is True
    assert summary["cpu_commitment_ratio"] > 1.0
    assert summary["allocated_monthly_usd"] <= summary["cluster_monthly_usd"] + 0.01
    assert summary["wasted_monthly_usd"] <= summary["cluster_monthly_usd"] + 0.01
    assert summary["waste_as_pct_of_cluster"] <= 100.0


def test_a_normally_committed_cluster_is_not_flagged_as_over_committed():
    snapshot = {
        "nodes": [_node(cpu=8.0, memory=32 * GIB)],
        "workloads": [_workload("a", cpu_req=2.0, mem_req=8 * GIB)],
    }
    summary = analyze_cluster_cost(snapshot)["summary"]
    assert summary["over_committed"] is False
    assert summary["cpu_commitment_ratio"] < 1.0
