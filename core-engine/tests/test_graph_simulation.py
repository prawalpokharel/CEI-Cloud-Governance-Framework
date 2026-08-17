"""
Scoring the graph a pull request implies.

The cases that matter are the ones the existing blast-radius check cannot
see at all: a service that does not exist yet, and a dependency edge that
does not exist yet.
"""

import pytest

from src.services.graph_simulation import (
    compare,
    concentration,
    project_snapshot,
    structural_centrality,
)
from src.services.blast_radius import build_dependency_graph


def _workload(name, ns="prod"):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": 2,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "images": [f"repo/{name}:1.0"],
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _edge(src, dst, ns="prod"):
    return {"source": f"{ns}/Deployment/{src}", "target": f"{ns}/Deployment/{dst}",
            "confidence": 0.9, "source_kind": "env_reference"}


def _service(name, ns="prod"):
    return {"name": name, "namespace": ns, "selector": {"app": name}}


def _service_manifest(name, ns="prod"):
    return f"""
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {ns}
spec:
  selector:
    app: {name}
  ports:
    - port: 8080
""".strip()


def _manifest(name, *, env=None, ns="prod", replicas=2):
    env_block = ""
    if env:
        entries = "\n".join(
            f"            - name: {k}\n              value: \"{v}\""
            for k, v in env.items()
        )
        env_block = f"\n          env:\n{entries}"
    return f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {ns}
spec:
  replicas: {replicas}
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: app
          image: repo/{name}:1.0{env_block}
""".strip()


@pytest.fixture
def flat_cluster():
    """Four services, nothing depends on anything. Concentration is minimal."""
    names = ["api", "worker", "reporting", "billing"]
    return {
        "workloads": [_workload(n) for n in names],
        "services": [_service(n) for n in names],
        "edges": [],
        "pods": [],
    }


@pytest.fixture
def layered_cluster():
    """Three services all calling `core`."""
    names = ["api", "worker", "reporting", "core"]
    return {
        "workloads": [_workload(n) for n in names],
        "services": [_service(n) for n in names],
        "edges": [_edge(n, "core") for n in ("api", "worker", "reporting")],
        "pods": [],
    }


def _kinds(result):
    return {f["kind"] for f in result["findings"]}


def _find(result, kind):
    return next(f for f in result["findings"] if f["kind"] == kind)


# --- concentration ----------------------------------------------------------


def test_concentration_of_a_flat_distribution_is_zero():
    assert concentration({"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25}) == pytest.approx(0.0)


def test_concentration_of_a_single_holder_is_one():
    assert concentration({"a": 1.0, "b": 0.0, "c": 0.0}) == pytest.approx(1.0)


def test_concentration_is_between_for_a_skewed_distribution():
    value = concentration({"a": 0.7, "b": 0.1, "c": 0.1, "d": 0.1})

    assert 0.0 < value < 1.0


def test_concentration_is_scale_free():
    """
    The normalisation is the point: a raw HHI falls as n grows, which would
    report every added service as a structural improvement.
    """
    small = concentration({f"s{i}": 1.0 for i in range(4)})
    large = concentration({f"s{i}": 1.0 for i in range(400)})

    assert small == pytest.approx(large, abs=1e-6)


@pytest.mark.parametrize("scores,expected", [({}, 0.0), ({"a": 1.0}, 1.0)])
def test_concentration_degenerate_cases(scores, expected):
    assert concentration(scores) == expected


# --- centrality -------------------------------------------------------------


def test_centrality_flows_toward_dependencies(layered_cluster):
    """`core` is called by three services and should dominate."""
    scores = structural_centrality(build_dependency_graph(layered_cluster))

    assert max(scores, key=scores.get) == "prod/Deployment/core"


def test_edgeless_graph_is_uniform(flat_cluster):
    scores = structural_centrality(build_dependency_graph(flat_cluster))

    assert len(set(round(v, 9) for v in scores.values())) == 1
    assert concentration(scores) == pytest.approx(0.0)


def test_empty_graph_yields_no_scores():
    assert structural_centrality(build_dependency_graph({"workloads": [], "edges": []})) == {}


# --- projection -------------------------------------------------------------


def test_new_service_appears_in_the_projected_graph(flat_cluster):
    projected, changes = project_snapshot(
        flat_cluster,
        [{"path": "k8s/auth.yaml", "before": None,
         "after": _manifest("auth") + "\n---\n" + _service_manifest("auth")}],
    )

    assert changes["prod/Deployment/auth"] == "added"
    assert any(w["key"] == "prod/Deployment/auth" for w in projected["workloads"])


def test_new_dependency_edge_is_inferred_from_the_manifest(flat_cluster):
    """
    The case the existing check is blind to: an env var pointing an existing
    service at a new one creates an edge that is in no snapshot.
    """
    projected, _ = project_snapshot(flat_cluster, [
        {"path": "k8s/auth.yaml", "before": None,
         "after": _manifest("auth") + "\n---\n" + _service_manifest("auth")},
        {"path": "k8s/api.yaml", "before": _manifest("api"),
         "after": _manifest("api", env={"AUTH_URL": "http://auth:8080"})},
    ])
    edges = {(e["source"], e["target"]) for e in projected["edges"]}

    assert ("prod/Deployment/api", "prod/Deployment/auth") in edges


def test_untouched_workloads_are_carried_through(flat_cluster):
    """
    The projection is the cluster with the diff applied, not a graph built
    from the repository alone -- a repo rarely contains every workload.
    """
    projected, _ = project_snapshot(
        flat_cluster,
        [{"path": "k8s/auth.yaml", "before": None,
         "after": _manifest("auth") + "\n---\n" + _service_manifest("auth")}],
    )
    keys = {w["key"] for w in projected["workloads"]}

    assert "prod/Deployment/billing" in keys
    assert len(keys) == 5


def test_deleted_workload_is_removed(flat_cluster):
    projected, changes = project_snapshot(
        flat_cluster,
        [{"path": "k8s/billing.yaml", "before": _manifest("billing"), "after": None}],
    )

    assert changes["prod/Deployment/billing"] == "removed"
    assert all(w["key"] != "prod/Deployment/billing" for w in projected["workloads"])


def test_removed_dependency_disappears_from_the_projection(layered_cluster):
    """A modified workload's edges are re-derived, not merged with the old set."""
    projected, _ = project_snapshot(layered_cluster, [
        {"path": "k8s/api.yaml",
         "before": _manifest("api", env={"CORE_URL": "http://core:8080"}),
         "after": _manifest("api")},
    ])
    edges = {(e["source"], e["target"]) for e in projected["edges"]}

    assert ("prod/Deployment/api", "prod/Deployment/core") not in edges
    # Other callers are untouched.
    assert ("prod/Deployment/worker", "prod/Deployment/core") in edges


def test_env_values_never_reach_the_projection(flat_cluster):
    """Manifests carry credentials; the projection must not."""
    projected, _ = project_snapshot(flat_cluster, [
        {"path": "k8s/api.yaml", "before": _manifest("api"),
         "after": _manifest("api", env={"DB_PASSWORD": "hunter2",
                                        "AUTH_URL": "http://auth:8080"})},
    ])
    blob = repr(projected)

    assert "hunter2" not in blob
    assert "_env_values" not in blob


def test_unresolvable_env_reference_creates_no_edge(flat_cluster):
    """A value that matches no Service in the projected cluster is not an edge."""
    projected, _ = project_snapshot(flat_cluster, [
        {"path": "k8s/api.yaml", "before": _manifest("api"),
         "after": _manifest("api", env={"EXTERNAL": "https://api.stripe.com"})},
    ])

    assert projected["edges"] == []


def test_helm_templates_are_skipped(flat_cluster):
    projected, changes = project_snapshot(flat_cluster, [
        {"path": "chart/templates/x.yaml", "before": None,
         "after": "kind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\n"},
    ])

    assert changes == {}
    assert len(projected["workloads"]) == 4


# --- comparison -------------------------------------------------------------


def test_new_shared_dependency_raises_concentration(flat_cluster):
    """
    Three independent services all pointed at one new auth service. Nothing in
    any single manifest says "this concentrates the system"; the graph does.
    """
    files = [
        {"path": "k8s/auth.yaml", "before": None,
         "after": _manifest("auth") + "\n---\n" + _service_manifest("auth")},
    ] + [
        {"path": f"k8s/{n}.yaml", "before": _manifest(n),
         "after": _manifest(n, env={"AUTH_URL": "http://auth:8080"})}
        for n in ("api", "worker", "reporting")
    ]
    result = compare(flat_cluster, files)

    assert result["concentration"]["after"] > result["concentration"]["before"]
    finding = _find(result, "concentration_increased")
    # Nothing depended on anything before, so a relative change is undefined
    # and the finding says what actually happened instead of dividing by zero.
    assert finding["severity"] == "critical"
    assert "introduces structural concentration" in finding["title"]
    assert "first shared point of failure" in finding["detail"]


def test_relative_concentration_change_is_reported_when_defined(layered_cluster):
    """With an existing baseline, the headline is the percentage."""
    files = [
        {"path": "k8s/billing.yaml", "before": None,
         "after": _manifest("billing", env={"CORE_URL": "http://core:8080"})},
    ]
    result = compare(layered_cluster, files)
    concentration_findings = [
        f for f in result["findings"] if f["kind"].startswith("concentration_")
    ]

    for finding in concentration_findings:
        assert "%" in finding["title"]


def test_new_service_with_existing_dependents_is_flagged(flat_cluster):
    files = [
        {"path": "k8s/auth.yaml", "before": None,
         "after": _manifest("auth") + "\n---\n" + _service_manifest("auth")},
    ] + [
        {"path": f"k8s/{n}.yaml", "before": _manifest(n),
         "after": _manifest(n, env={"AUTH_URL": "http://auth:8080"})}
        for n in ("api", "worker")
    ]
    finding = _find(compare(flat_cluster, files), "new_shared_dependency")

    assert finding["workload_key"] == "prod/Deployment/auth"
    assert len(finding["evidence"]["dependents"]) == 2
    assert "no operational history" in finding["detail"]


def test_adding_a_leaf_service_is_not_a_structural_event(flat_cluster):
    """A service nothing depends on must not fire the concentration alarm."""
    result = compare(
        flat_cluster,
        [{"path": "k8s/isolated.yaml", "before": None, "after": _manifest("isolated")}],
    )

    assert "concentration_increased" not in _kinds(result)
    assert "new_shared_dependency" not in _kinds(result)


def test_centrality_movement_is_reported(layered_cluster):
    """An extra caller makes `core` more central; the delta names the cause."""
    result = compare(layered_cluster, [
        {"path": "k8s/billing.yaml", "before": None,
         "after": _manifest("billing", env={"CORE_URL": "http://core:8080"})},
    ])
    core = next(m for m in result["movements"] if m["workload_key"] == "prod/Deployment/core")

    assert core["after"] >= core["before"]


def test_graph_counts_are_reported(flat_cluster):
    result = compare(
        flat_cluster,
        [{"path": "k8s/auth.yaml", "before": None,
         "after": _manifest("auth") + "\n---\n" + _service_manifest("auth")}],
    )

    assert result["graph"]["workloads_before"] == 4
    assert result["graph"]["workloads_after"] == 5
    assert result["graph"]["added"] == ["prod/Deployment/auth"]


def test_removing_a_shared_dependency_reduces_concentration(layered_cluster):
    files = [
        {"path": f"k8s/{n}.yaml",
         "before": _manifest(n, env={"CORE_URL": "http://core:8080"}),
         "after": _manifest(n)}
        for n in ("api", "worker", "reporting")
    ]
    result = compare(layered_cluster, files)

    assert result["concentration"]["after"] < result["concentration"]["before"]
    assert "concentration_reduced" in _kinds(result)


def test_no_changes_produces_no_movement(flat_cluster):
    result = compare(flat_cluster, [])

    assert result["concentration"]["delta"] == 0.0
    assert result["findings"] == []


def test_result_states_the_entropy_limitation(flat_cluster):
    """
    A workload that does not exist has no utilization history. Estimating
    entropy would invent the same third of the score live_cei refuses to.
    """
    result = compare(flat_cluster, [])

    assert "Structural centrality only" in result["note"]
    assert "does not exist yet" in result["note"]


def test_empty_cluster_does_not_crash():
    result = compare({"workloads": [], "services": [], "edges": []}, [])

    assert result["concentration"]["before"] == 0.0
    assert result["findings"] == []
