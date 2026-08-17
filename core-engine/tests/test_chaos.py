"""
Chaos validation harness.

The manifests and the measurement are unit-tested; the correlation itself was
established live (docs/CHAOS-VALIDATION.md). What must never regress here:
predictions freeze before experiments, unknown readiness never fabricates
impact, and the target never counts toward its own blast radius.
"""

import pytest

from src.services.chaos import (
    ExperimentResult,
    _spearman,
    chaos_mesh_experiment,
    evaluate,
    litmus_experiment,
    measure_impact,
    plan,
    predict,
)


def _workload(name, desired=2, ready=2, ns="bt"):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": desired, "replicas_ready": ready,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _snapshot(ready_map):
    """ready_map: {name: (desired, ready)}"""
    return {
        "workloads": [_workload(n, d, r) for n, (d, r) in ready_map.items()],
        "edges": [], "services": [], "pods": [],
    }


HEALTHY = {"db": (2, 2), "api": (2, 2), "web": (2, 2), "worker": (2, 2)}


# --- prediction --------------------------------------------------------------


def test_predictions_rank_by_blast_radius():
    snapshot = {
        "workloads": [_workload(n) for n in ("db", "api", "web")],
        "edges": [
            {"source": "bt/Deployment/api", "target": "bt/Deployment/db",
             "confidence": 0.9, "source_kind": "env_reference"},
            {"source": "bt/Deployment/web", "target": "bt/Deployment/api",
             "confidence": 0.9, "source_kind": "env_reference"},
        ],
        "services": [], "pods": [],
    }
    predictions = predict(snapshot)

    assert predictions[0].workload_key == "bt/Deployment/db"
    assert predictions[0].predicted == {"bt/Deployment/api", "bt/Deployment/web"}


# --- experiment manifests ----------------------------------------------------


def test_chaos_mesh_manifest_is_scoped_to_the_target():
    manifest = chaos_mesh_experiment(_workload("db"), duration_seconds=90)

    assert manifest["kind"] == "PodChaos"
    assert manifest["spec"]["action"] == "pod-failure"
    assert manifest["spec"]["selector"]["labelSelectors"] == {"app": "db"}
    assert manifest["spec"]["selector"]["namespaces"] == ["bt"]
    assert manifest["spec"]["duration"] == "90s"


def test_pod_failure_not_pod_kill():
    """
    A killed pod reschedules faster than a dependent's readiness probe can
    observe, which reads as zero impact for a dependency that is real.
    """
    assert chaos_mesh_experiment(_workload("db"))["spec"]["action"] == "pod-failure"


def test_workload_without_labels_is_refused():
    """No selector means the experiment would target the whole namespace."""
    workload = _workload("db")
    workload["pod_labels"] = {}

    with pytest.raises(ValueError, match="entire namespace"):
        chaos_mesh_experiment(workload)


def test_litmus_manifest_targets_the_same_way():
    manifest = litmus_experiment(_workload("db"), duration_seconds=45)

    assert manifest["kind"] == "ChaosEngine"
    assert manifest["spec"]["appinfo"]["applabel"] == "app=db"
    assert manifest["spec"]["experiments"][0]["name"] == "pod-delete"


def test_manifest_names_its_purpose():
    annotations = chaos_mesh_experiment(_workload("db"))["metadata"]["annotations"]

    assert "cloudoptimizer.io/target" in annotations


# --- measurement -------------------------------------------------------------


def test_lost_readiness_is_measured():
    during = _snapshot({**HEALTHY, "db": (2, 0), "api": (2, 0), "web": (2, 0)})
    affected, _ = measure_impact(_snapshot(HEALTHY), during, "bt/Deployment/db")

    assert affected == {"bt/Deployment/api", "bt/Deployment/web"}


def test_target_is_excluded_from_its_own_impact():
    during = _snapshot({**HEALTHY, "db": (2, 0)})
    affected, _ = measure_impact(_snapshot(HEALTHY), during, "bt/Deployment/db")

    assert "bt/Deployment/db" not in affected
    assert affected == set()


def test_partial_readiness_counts_as_degraded():
    """2 desired, 1 ready is degraded, not healthy."""
    during = _snapshot({**HEALTHY, "api": (2, 1)})
    affected, _ = measure_impact(_snapshot(HEALTHY), during, "bt/Deployment/db")

    assert affected == {"bt/Deployment/api"}


def test_already_unhealthy_workload_is_not_credited_to_the_experiment():
    baseline = _snapshot({**HEALTHY, "worker": (2, 0)})
    during = _snapshot({**HEALTHY, "db": (2, 0), "worker": (2, 0)})
    affected, caveats = measure_impact(baseline, during, "bt/Deployment/db")

    assert "bt/Deployment/worker" not in affected
    assert any("already unhealthy" in c for c in caveats)


def test_unknown_readiness_never_fabricates_impact():
    """
    Kubernetes omits readyReplicas when zero pods are ready, so None is "not
    known to be serving". In the BASELINE that must exclude the workload from
    measurement -- uncertainty suppresses, never fabricates. This is the bug
    the first live run found, pinned.
    """
    baseline = _snapshot({**HEALTHY, "api": (2, None)})
    during = _snapshot({**HEALTHY, "api": (2, 0), "db": (2, 0)})
    affected, _ = measure_impact(baseline, during, "bt/Deployment/db")

    assert "bt/Deployment/api" not in affected


def test_vanished_workload_is_a_caveat_not_impact():
    during = _snapshot({k: v for k, v in HEALTHY.items() if k != "worker"})
    during["workloads"] = [w for w in during["workloads"]]
    affected, caveats = measure_impact(_snapshot(HEALTHY), during, "bt/Deployment/db")

    assert "bt/Deployment/worker" not in affected
    assert any("vanished" in c for c in caveats)


# --- evaluation --------------------------------------------------------------


def _result(name, predicted, measured):
    return ExperimentResult(
        workload_key=f"bt/Deployment/{name}",
        predicted={f"bt/Deployment/{p}" for p in predicted},
        measured={f"bt/Deployment/{m}" for m in measured},
    )


def test_perfect_agreement():
    result = _result("db", ["api", "web"], ["api", "web"])

    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.jaccard == 1.0


def test_error_directions_are_distinguished():
    result = _result("db", ["api", "web"], ["api", "worker"])

    assert result.false_positives == {"bt/Deployment/web"}
    assert result.false_negatives == {"bt/Deployment/worker"}


def test_rank_correlation_on_the_live_shape():
    """The actual result shape from cei-test: sizes 4,2,1,0,0,0 vs 4,1,1,0,0,0."""
    results = [
        _result("db", ["a", "b", "c", "d"], ["a", "b", "c", "d"]),
        _result("auth", ["a", "b"], ["a"]),
        _result("api", ["a"], ["a"]),
        _result("web", [], []),
        _result("worker", [], []),
        _result("reporting", [], []),
    ]
    evaluation = evaluate(results)

    assert evaluation["rank_correlation"] > 0.95
    assert evaluation["false_negatives"] == 0


def test_evaluation_states_independence():
    note = evaluate([_result("db", ["a"], ["a"]), _result("b", [], []),
                     _result("c", ["x"], ["x"])])["rank_correlation_note"]

    assert "does not use the dependency graph" in note


def test_too_few_experiments_yield_no_correlation():
    assert evaluate([_result("db", ["a"], ["a"])])["rank_correlation"] is None


def test_empty_evaluation():
    assert evaluate([])["experiments"] == 0


@pytest.mark.parametrize("a,b,expected", [
    ([1, 2, 3, 4], [1, 2, 3, 4], 1.0),
    ([1, 2, 3, 4], [4, 3, 2, 1], -1.0),
])
def test_spearman_extremes(a, b, expected):
    assert _spearman(a, b) == pytest.approx(expected)


def test_spearman_handles_ties():
    """Tied sizes (three predictions of 0) must not fabricate correlation."""
    value = _spearman([4, 2, 1, 0, 0, 0], [4, 1, 1, 0, 0, 0])

    assert value is not None
    assert 0.9 < value <= 1.0


def test_spearman_constant_input_is_undefined():
    assert _spearman([1, 1, 1], [1, 2, 3]) is None


# --- plan --------------------------------------------------------------------


def test_plan_pairs_predictions_with_manifests():
    snapshot = {
        "workloads": [_workload(n) for n in ("db", "api")],
        "edges": [{"source": "bt/Deployment/api", "target": "bt/Deployment/db",
                   "confidence": 0.9, "source_kind": "env_reference"}],
        "services": [], "pods": [],
    }
    result = plan(snapshot, limit=2)

    first = result["experiments"][0]
    assert first["workload_key"] == "bt/Deployment/db"
    assert first["manifest"]["kind"] == "PodChaos"
    assert first["prediction"]["predicted_count"] == 1
    assert "real failures" in result["warning"]


def test_plan_can_emit_litmus():
    snapshot = {"workloads": [_workload("db")], "edges": [], "services": [], "pods": []}
    result = plan(snapshot, platform="litmus", limit=1)

    assert result["experiments"][0]["manifest"]["kind"] == "ChaosEngine"
