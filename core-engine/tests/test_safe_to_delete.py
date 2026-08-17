"""
Safety gating for cost recommendations.

The case that matters most is the one utilization data cannot express: a
workload sitting at 1% CPU that three services depend on. Every test here
exists to keep that from ever being reported as safe.
"""

import pytest

from src.services.safe_to_delete import (
    DELETE,
    REDUCE_REQUESTS,
    SCALE_TO_ZERO,
    assess,
    review_cost_recommendations,
)


def _workload(name, *, ns="prod", kind="Deployment", labels=None,
              cpu_used=0.5, cpu_requested=1.0, replicas=2, ownership=None):
    return {
        "key": f"{ns}/{kind}/{name}",
        "name": name,
        "namespace": ns,
        "kind": kind,
        "replicas_desired": replicas,
        "labels": labels or {"app": name},
        "pod_labels": {"app": name},
        "ownership": ownership or {},
        "images": [f"repo/{name}:1.0"],
        "cpu_cores_used": cpu_used,
        "cpu_cores_requested": cpu_requested,
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _snapshot(workloads, edges=(), services=(), pdbs=()):
    return {
        "workloads": list(workloads),
        "edges": list(edges),
        "services": list(services),
        "disruption_budgets": list(pdbs),
        "pods": [],
    }


def _edge(source, target, kind="env_reference", confidence=0.9):
    return {"source": source, "target": target, "confidence": confidence,
            "source_kind": kind}


# --- the failover trap -----------------------------------------------------


def test_idle_with_dependents_is_unsafe():
    """
    1% CPU and three services depend on it. Every utilization-based tool says
    delete. This is a standby path.
    """
    snapshot = _snapshot(
        workloads=[
            _workload("standby-db", cpu_used=0.01, cpu_requested=1.0),
            *(_workload(f"svc{i}") for i in range(3)),
        ],
        edges=[_edge(f"prod/Deployment/svc{i}", "prod/Deployment/standby-db")
               for i in range(3)],
    )
    verdict = assess(snapshot, "prod/Deployment/standby-db", action=DELETE)

    assert verdict.verdict == "unsafe"
    assert any("failover or standby" in b for b in verdict.blocking)
    assert verdict.evidence["cpu_utilization"] == pytest.approx(0.01)


def test_idle_with_no_dependents_is_safe():
    snapshot = _snapshot([_workload("forgotten", cpu_used=0.005, cpu_requested=1.0)])
    verdict = assess(snapshot, "prod/Deployment/forgotten", action=DELETE)

    assert verdict.verdict == "safe"
    assert verdict.blocking == []


def test_busy_workload_with_dependents_is_still_blocked():
    """Dependents block deletion whether or not the workload looks idle."""
    snapshot = _snapshot(
        workloads=[_workload("api", cpu_used=0.9), _workload("client")],
        edges=[_edge("prod/Deployment/client", "prod/Deployment/api")],
    )
    verdict = assess(snapshot, "prod/Deployment/api", action=DELETE)

    assert verdict.verdict == "unsafe"
    assert any("declare a dependency" in b for b in verdict.blocking)


# --- user-facing -----------------------------------------------------------


def test_ingress_reach_blocks_deletion():
    snapshot = _snapshot(
        workloads=[_workload("frontend", cpu_used=0.01)],
        edges=[_edge("prod/Ingress/public", "prod/Deployment/frontend", "ingress", 1.0)],
    )
    verdict = assess(snapshot, "prod/Deployment/frontend", action=DELETE)

    assert verdict.verdict == "unsafe"
    assert any("ingress" in b.lower() for b in verdict.blocking)
    assert verdict.evidence["user_facing"] is True


# --- deliberate-intent signals ---------------------------------------------


@pytest.mark.parametrize("name", [
    "db-standby", "failover-proxy", "backup-worker", "warm-cache", "passive-node",
])
def test_intent_in_the_name_blocks_deletion(name):
    snapshot = _snapshot([_workload(name, cpu_used=0.0)])
    verdict = assess(snapshot, f"prod/Deployment/{name}", action=DELETE)

    assert verdict.verdict == "unsafe"
    assert any("on purpose" in b for b in verdict.blocking)


def test_intent_in_a_label_blocks_deletion():
    snapshot = _snapshot([
        _workload("worker-b", cpu_used=0.0, labels={"app": "worker-b", "role": "failover"})
    ])
    verdict = assess(snapshot, "prod/Deployment/worker-b", action=DELETE)

    assert verdict.verdict == "unsafe"


def test_disruption_budget_downgrades_to_review():
    """
    A PDB is a colleague saying this matters. Not blocking on its own, but
    never automatic.
    """
    snapshot = _snapshot(
        workloads=[_workload("cache", cpu_used=0.01)],
        pdbs=[{"name": "cache-pdb", "namespace": "prod", "selector": {"app": "cache"},
               "min_available": "1", "max_unavailable": None,
               "disruptions_allowed": 1, "expected_pods": 2}],
    )
    verdict = assess(snapshot, "prod/Deployment/cache", action=DELETE)

    assert verdict.verdict == "review"
    assert any("PodDisruptionBudget" in c for c in verdict.cautions)


def test_backing_a_service_downgrades_to_review():
    """DNS clients are invisible to the dependency graph."""
    snapshot = _snapshot(
        workloads=[_workload("api", cpu_used=0.01)],
        services=[{"name": "api", "namespace": "prod", "selector": {"app": "api"}}],
    )
    verdict = assess(snapshot, "prod/Deployment/api", action=DELETE)

    assert verdict.verdict == "review"
    assert any("DNS" in c for c in verdict.cautions)
    assert verdict.evidence["services"] == ["prod/api"]


def test_daemonset_is_flagged_as_wrong_lens():
    snapshot = _snapshot([_workload("log-agent", kind="DaemonSet", cpu_used=0.01)])
    verdict = assess(snapshot, "prod/DaemonSet/log-agent", action=DELETE)

    assert verdict.verdict == "review"
    assert any("one pod per node" in c for c in verdict.cautions)


# --- missing data defaults to caution --------------------------------------


def test_unmeasured_workload_is_not_declared_safe():
    """
    Absence of evidence is not evidence of absence. A workload with no metrics
    has not been cleared.
    """
    snapshot = _snapshot([_workload("mystery", cpu_used=None, cpu_requested=1.0)])
    verdict = assess(snapshot, "prod/Deployment/mystery", action=DELETE)

    assert verdict.verdict == "review"
    assert any("metrics-server" in c for c in verdict.cautions)


def test_unknown_workload_returns_review_not_error():
    verdict = assess(_snapshot([]), "prod/Deployment/ghost", action=DELETE)

    assert verdict.verdict == "review"
    assert "not found" in verdict.headline.lower()


def test_bursty_usage_is_a_caution():
    snapshot = _snapshot([_workload("batch", cpu_used=0.01)])
    cei = {"prod/Deployment/batch": {"cei_score": 0.3, "entropy": 0.85}}
    verdict = assess(snapshot, "prod/Deployment/batch", cei, action=DELETE)

    assert verdict.verdict == "review"
    assert any("variable" in c for c in verdict.cautions)


# --- action sensitivity ----------------------------------------------------


def test_reducing_requests_is_not_blocked_by_dependents_alone():
    """
    Trimming a request is not removing the workload. Dependents alone should
    not block it, or every rightsizing recommendation becomes unactionable.
    """
    snapshot = _snapshot(
        workloads=[_workload("api", cpu_used=0.2), _workload("client")],
        edges=[_edge("prod/Deployment/client", "prod/Deployment/api")],
    )
    verdict = assess(snapshot, "prod/Deployment/api", action=REDUCE_REQUESTS)

    assert verdict.verdict == "safe"


def test_reducing_requests_on_bursty_dependency_is_blocked():
    snapshot = _snapshot(
        workloads=[_workload("api", cpu_used=0.2), _workload("client")],
        edges=[_edge("prod/Deployment/client", "prod/Deployment/api")],
    )
    cei = {"prod/Deployment/api": {"cei_score": 0.8, "entropy": 0.9}}
    verdict = assess(snapshot, "prod/Deployment/api", cei, action=REDUCE_REQUESTS)

    assert verdict.verdict == "unsafe"
    assert any("headroom" in b for b in verdict.blocking)


def test_scale_to_zero_is_treated_like_deletion():
    snapshot = _snapshot(
        workloads=[_workload("api", cpu_used=0.01), _workload("client")],
        edges=[_edge("prod/Deployment/client", "prod/Deployment/api")],
    )
    verdict = assess(snapshot, "prod/Deployment/api", action=SCALE_TO_ZERO)

    assert verdict.verdict == "unsafe"


# --- gating a cost report --------------------------------------------------


def _cost_result():
    return {
        "workloads": [
            {"workload_key": "prod/Deployment/standby-db", "verdict": "idle",
             "wasted_monthly_usd": 400.0},
            {"workload_key": "prod/Deployment/forgotten", "verdict": "idle",
             "wasted_monthly_usd": 120.0},
            {"workload_key": "prod/Deployment/svc0", "verdict": "over_provisioned",
             "wasted_monthly_usd": 30.0},
        ]
    }


@pytest.fixture
def gated_snapshot():
    return _snapshot(
        workloads=[
            _workload("standby-db", cpu_used=0.01, cpu_requested=1.0),
            _workload("forgotten", cpu_used=0.005, cpu_requested=1.0),
            *(_workload(f"svc{i}") for i in range(3)),
        ],
        edges=[_edge(f"prod/Deployment/svc{i}", "prod/Deployment/standby-db")
               for i in range(3)],
    )


def test_review_splits_savings_by_verdict(gated_snapshot):
    result = review_cost_recommendations(gated_snapshot, _cost_result())
    summary = result["summary"]

    assert summary["claimed_monthly_usd"] == 550.0
    assert summary["blocked_monthly_usd"] == 400.0
    assert summary["safe_monthly_usd"] == 150.0
    assert summary["unsafe"] == 1


def test_review_reports_the_claimed_total_too(gated_snapshot):
    """
    A number that quietly shrinks is not credible. The gap between claimed and
    safe is the entire argument for the module.
    """
    summary = review_cost_recommendations(gated_snapshot, _cost_result())["summary"]

    assert summary["claimed_monthly_usd"] > summary["safe_monthly_usd"]
    assert "400.00/month" in summary["note"]


def test_review_orders_unsafe_first(gated_snapshot):
    result = review_cost_recommendations(gated_snapshot, _cost_result())
    verdicts = [r["safety"]["verdict"] for r in result["recommendations"]]

    assert verdicts[0] == "unsafe"
    assert verdicts == sorted(verdicts, key=lambda v: {"unsafe": 0, "review": 1, "safe": 2}[v])


def test_review_preserves_the_original_entry(gated_snapshot):
    result = review_cost_recommendations(gated_snapshot, _cost_result())
    blocked = next(r for r in result["recommendations"]
                   if r["workload_key"] == "prod/Deployment/standby-db")

    assert blocked["wasted_monthly_usd"] == 400.0
    assert blocked["verdict"] == "idle"
    assert blocked["safety"]["action"] == "scale_to_zero"


def test_clean_report_says_so(gated_snapshot):
    cost = {"workloads": [
        {"workload_key": "prod/Deployment/forgotten", "verdict": "idle",
         "wasted_monthly_usd": 120.0},
    ]}
    summary = review_cost_recommendations(gated_snapshot, cost)["summary"]

    assert summary["blocked_monthly_usd"] == 0.0
    assert "No recommendation in this set" in summary["note"]


def test_review_handles_empty_cost_report(gated_snapshot):
    result = review_cost_recommendations(gated_snapshot, {"workloads": []})

    assert result["summary"]["recommendations_reviewed"] == 0
    assert result["recommendations"] == []
