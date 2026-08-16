"""
CEI over live cluster snapshots.

Two properties matter most here and neither is obvious from reading the code:

* Entropy is withheld, not invented, when history is thin. The scenario path
  fabricated ninety days of Gaussian noise whenever history was missing, which
  is how a third of the CEI score came to be computed from random numbers.
* The two centrality modes genuinely rank workloads differently, so choosing
  between them is a real product decision rather than a formatting preference.
"""

from __future__ import annotations

import pytest

from src.services.live_cei import (
    MIN_ENTROPY_SAMPLES,
    CentralityMode,
    _history_for,
    _tier_for,
    build_analysis_request,
    compute_live_cei,
)


def _workload(name, *, cpu_req=0.5, cpu_used=0.1, namespace="default"):
    return {
        "key": f"{namespace}/Deployment/{name}",
        "name": name,
        "namespace": namespace,
        "kind": "Deployment",
        "cpu_cores_requested": cpu_req,
        "cpu_cores_used": cpu_used,
        "memory_bytes_requested": 1024 ** 3,
        "memory_bytes_used": 256 * 1024 ** 2,
        "replicas_desired": 2,
        "replicas_ready": 2,
    }


def _edge(src, dst, namespace="default"):
    return {
        "source": f"{namespace}/Deployment/{src}",
        "target": f"{namespace}/Deployment/{dst}",
        "confidence": 0.9,
        "source_kind": "env_reference",
    }


# Shaped after Online Boutique, because the distinction under test only
# appears on a graph with a real fan-out: frontend reaches many services
# (high degree and betweenness, low in-degree) while catalog is reached by
# several (high in-degree, low out-degree). A smaller graph collapses the two
# modes onto the same answer and would make the test assert nothing.
SNAPSHOT = {
    "workloads": [
        _workload(n)
        for n in (
            "frontend", "checkout", "cart", "catalog", "currency",
            "shipping", "email", "payment", "ads", "loadgen",
        )
    ],
    "edges": [
        _edge("loadgen", "frontend"),
        _edge("frontend", "checkout"),
        _edge("frontend", "cart"),
        _edge("frontend", "catalog"),
        _edge("frontend", "currency"),
        _edge("frontend", "shipping"),
        _edge("frontend", "ads"),
        _edge("checkout", "catalog"),
        _edge("checkout", "cart"),
        _edge("checkout", "currency"),
        _edge("checkout", "shipping"),
        _edge("checkout", "email"),
        _edge("checkout", "payment"),
        _edge("cart", "catalog"),
    ],
}


def _ranking(result) -> list[str]:
    return [
        n["node_id"].split("/")[-1]
        for n in sorted(result.nodes, key=lambda n: -n["cei_score"])
    ]


def _history(n, varying=True):
    """n sample rows; varying=False produces a perfectly flat series."""
    return [
        {
            "cpu_cores_used": (0.1 + (i % 5) * 0.05) if varying else 0.1,
            "cpu_cores_requested": 0.5,
            "mem_bytes_used": 256 * 1024 ** 2,
            "mem_bytes_requested": 1024 ** 3,
        }
        for i in range(n)
    ]


# --------------------------------------------------------------------------
# Entropy is withheld rather than fabricated
# --------------------------------------------------------------------------

def test_entropy_is_withheld_when_history_is_thin():
    result = compute_live_cei(SNAPSHOT, {})

    assert result.entropy_ready is False
    assert result.weights["beta"] == 0.0
    # Redistributed, not simply dropped -- scores must stay on a [0, 1] scale
    # and remain comparable to scores computed once history exists.
    assert result.weights["alpha"] + result.weights["gamma"] == pytest.approx(
        1.0, abs=1e-3
    )


def test_entropy_is_used_once_enough_history_exists():
    history = {
        w["key"]: _history(MIN_ENTROPY_SAMPLES) for w in SNAPSHOT["workloads"]
    }
    result = compute_live_cei(SNAPSHOT, history)

    assert result.entropy_ready is True
    assert result.weights["beta"] > 0
    assert any(n["entropy"] > 0 for n in result.nodes)


def test_history_is_never_fabricated_for_live_clusters():
    """
    The scenario path synthesizes ninety days of noise when history is
    absent. That must not happen here: no samples means no history, so the
    entropy term is suppressed instead.
    """
    request, total_points = build_analysis_request(SNAPSHOT, {})
    assert total_points == 0
    assert all(
        node["utilization_history"] == [] for node in request.telemetry.nodes
    )


def test_workloads_without_measurements_yield_no_history():
    """
    Requested capacity is a constant. Deriving history from it would read as
    a perfectly stable workload and produce a confidently wrong entropy of
    zero, rather than an honest "unknown".
    """
    samples = [
        {"cpu_cores_used": None, "cpu_cores_requested": 0.5,
         "mem_bytes_used": None, "mem_bytes_requested": 1024 ** 3}
    ] * 10
    assert _history_for(samples) == []


# --------------------------------------------------------------------------
# Centrality semantics
# --------------------------------------------------------------------------

def test_blast_radius_ranks_the_shared_dependency_highest():
    """catalog is reached by three services; if it fails, they all break."""
    result = compute_live_cei(
        SNAPSHOT, {}, centrality_mode=CentralityMode.blast_radius
    )
    ranked = _ranking(result)
    assert ranked[0] == "catalog"
    # loadgen has no dependents at all, so nothing breaks when it fails.
    assert ranked[-1] == "loadgen"


def test_structural_promotes_the_entrypoint_over_blast_radius():
    """
    frontend is a hub -- many outbound edges, one inbound -- so it scores well
    on degree and betweenness and poorly on dependents.

    The assertion is about its RELATIVE position rather than being first,
    because which node tops each ranking depends on the graph. What must hold
    on any graph with a real entrypoint is that the two readings disagree
    about it, which is precisely the decision this parameter exposes.
    """
    blast = _ranking(
        compute_live_cei(SNAPSHOT, {}, centrality_mode=CentralityMode.blast_radius)
    )
    structural = _ranking(
        compute_live_cei(SNAPSHOT, {}, centrality_mode=CentralityMode.structural)
    )
    assert structural.index("frontend") < blast.index("frontend")


def test_the_two_modes_produce_different_rankings():
    """
    If they agreed, the choice would not matter and the parameter would be
    noise. They must not: this is the decision the product rests on.
    """
    blast = _ranking(
        compute_live_cei(SNAPSHOT, {}, centrality_mode=CentralityMode.blast_radius)
    )
    structural = _ranking(
        compute_live_cei(SNAPSHOT, {}, centrality_mode=CentralityMode.structural)
    )
    assert blast != structural


def test_mode_is_reported_so_a_score_is_never_ambiguous():
    result = compute_live_cei(
        SNAPSHOT, {}, centrality_mode=CentralityMode.blast_radius
    )
    assert result.to_dict()["centrality_mode"] == "blast_radius"


# --------------------------------------------------------------------------
# Snapshot translation
# --------------------------------------------------------------------------

def test_edges_to_non_workload_vertices_are_dropped():
    """
    Ingress vertices carry no telemetry, so keeping them would add nodes the
    pipeline cannot score.
    """
    snapshot = {
        "workloads": [_workload("api")],
        "edges": [
            {"source": "default/Ingress/public", "target": "default/Deployment/api",
             "confidence": 1.0, "source_kind": "ingress"},
        ],
    }
    request, _ = build_analysis_request(snapshot, {})
    assert request.telemetry.edges == []
    assert len(request.telemetry.nodes) == 1


def test_missing_usage_reads_as_fully_utilized_not_idle():
    """
    Without metrics-server, usage is unknown. Treating unknown as zero would
    justify shrinking a workload nobody measured, which is the dangerous
    direction to be wrong in.
    """
    snapshot = {
        "workloads": [_workload("api", cpu_used=None)],
        "edges": [],
    }
    request, _ = build_analysis_request(snapshot, {})
    assert request.telemetry.nodes[0]["metrics"]["cpu_utilization"] == 100.0


@pytest.mark.parametrize(
    "namespace,expected",
    [
        ("kube-system", "mission_critical"),
        ("default", "operational"),
        ("production", "operational"),
        ("my-dev-ns", "development"),
        ("staging", "development"),
        ("anything-else", "operational"),
    ],
)
def test_namespace_maps_to_a_governance_tier(namespace, expected):
    assert _tier_for({"namespace": namespace}) == expected
