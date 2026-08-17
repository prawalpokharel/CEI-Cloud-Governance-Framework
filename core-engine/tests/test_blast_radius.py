"""
Blast radius tests, built on Google's Online Boutique.

Its call graph is publicly documented, which means the expected answers here
are checkable against something outside this repository rather than against
whatever the implementation happens to produce.
"""

import pytest

from src.services.blast_radius import (
    WEAK_PATH_CONFIDENCE,
    build_dependency_graph,
    compute_blast_radius,
    compute_many,
    rank_by_blast_radius,
)

BOUTIQUE_CALLS = {
    "frontend": [
        "cartservice", "productcatalogservice", "currencyservice",
        "recommendationservice", "shippingservice", "checkoutservice",
        "adservice",
    ],
    "checkoutservice": [
        "cartservice", "productcatalogservice", "currencyservice",
        "shippingservice", "paymentservice", "emailservice",
    ],
    "recommendationservice": ["productcatalogservice"],
    "cartservice": ["rediscart"],
}

# A batch job nothing calls and no ingress reaches. Every service in the
# published Boutique graph has at least one caller, so the "genuinely no
# dependents" case needs a workload the topology does not supply.
ORPHAN = "nightlyreconciler"

ALL_SERVICES = sorted(
    set(BOUTIQUE_CALLS)
    | {target for targets in BOUTIQUE_CALLS.values() for target in targets}
    | {ORPHAN}
)


def _key(name: str) -> str:
    return f"boutique/Deployment/{name}"


@pytest.fixture
def snapshot():
    workloads = [
        {
            "key": _key(name),
            "name": name,
            "namespace": "boutique",
            "kind": "Deployment",
        }
        for name in ALL_SERVICES
    ]
    edges = [
        {
            "source": _key(source),
            "target": _key(target),
            "confidence": 0.9,
            "source_kind": "env_reference",
        }
        for source, targets in BOUTIQUE_CALLS.items()
        for target in targets
    ]
    edges.append({
        "source": "boutique/Ingress/boutique-ingress",
        "target": _key("frontend"),
        "confidence": 1.0,
        "source_kind": "ingress",
    })
    return {"workloads": workloads, "edges": edges}


@pytest.fixture
def cei(snapshot):
    # Deliberately uneven so centrality_fraction is not a proxy for count.
    scores = {
        "frontend": 0.88, "checkoutservice": 0.81, "cartservice": 0.74,
        "productcatalogservice": 0.69, "rediscart": 0.66,
    }
    return {
        _key(name): {
            "cei_score": scores.get(name, 0.30),
            "classification": "critical" if scores.get(name, 0) > 0.8 else "moderate",
        }
        for name in ALL_SERVICES
    }


# --- direction -------------------------------------------------------------


def test_blast_radius_walks_dependents_not_dependencies(snapshot, cei):
    """
    rediscart is called by cartservice, which is called by frontend and
    checkoutservice. Those three are affected -- and nothing else, even though
    every other service is one or two hops away in an undirected walk.
    """
    radius = compute_blast_radius(snapshot, _key("rediscart"), cei)

    assert {a.key for a in radius.affected} == {
        _key("cartservice"), _key("frontend"), _key("checkoutservice"),
    }


def test_dependencies_are_not_reported_as_affected(snapshot, cei):
    """
    checkoutservice calls six services. If checkoutservice changes, none of
    those six break -- they do not know it exists. Only frontend does.
    """
    radius = compute_blast_radius(snapshot, _key("checkoutservice"), cei)

    assert [a.key for a in radius.affected] == [_key("frontend")]
    assert _key("paymentservice") not in {a.key for a in radius.affected}


def test_leaf_dependency_affects_the_most(snapshot, cei):
    """productcatalogservice is called by three services directly."""
    radius = compute_blast_radius(snapshot, _key("productcatalogservice"), cei)

    assert set(radius.direct_dependents) == {
        _key("frontend"), _key("checkoutservice"), _key("recommendationservice"),
    }


def test_workload_nothing_depends_on(snapshot, cei):
    radius = compute_blast_radius(snapshot, _key(ORPHAN), cei)

    assert radius.total_affected == 0
    assert radius.to_dict()["user_facing"] is False
    assert radius.severity == "none"
    assert "Nothing depends on" in radius.headline


def test_leaf_called_by_one_service_is_not_orphaned(snapshot, cei):
    """adservice has exactly one caller, and that caller is user-facing."""
    radius = compute_blast_radius(snapshot, _key("adservice"), cei)

    assert [a.key for a in radius.affected] == [_key("frontend")]
    assert radius.to_dict()["user_facing"] is True


# --- user-facing reach -----------------------------------------------------


def test_ingress_reach_is_detected(snapshot, cei):
    radius = compute_blast_radius(snapshot, _key("rediscart"), cei)

    assert radius.to_dict()["user_facing"] is True
    assert radius.entry_points == ["boutique/Ingress/boutique-ingress"]


def test_ingress_vertex_is_not_counted_as_an_affected_workload(snapshot, cei):
    radius = compute_blast_radius(snapshot, _key("frontend"), cei)

    assert all("Ingress" not in a.key for a in radius.affected)


def test_entrypoint_with_no_dependents_is_still_severe(snapshot, cei):
    """
    Nothing in the cluster depends on frontend, so a naive count says the
    blast radius is zero. Every user is affected. The severity must not read
    as harmless just because the cascade is empty.
    """
    radius = compute_blast_radius(snapshot, _key("frontend"), cei)

    assert radius.total_affected == 0
    assert radius.to_dict()["user_facing"] is True
    assert radius.severity == "high"
    assert "users" in radius.headline.lower()


def test_severity_rises_with_user_facing_reach(snapshot, cei):
    reaches_users = compute_blast_radius(snapshot, _key("cartservice"), cei)
    assert reaches_users.severity == "critical"


# --- confidence ------------------------------------------------------------


def test_confidence_multiplies_along_the_path(snapshot, cei):
    """
    frontend -> cartservice -> rediscart, both env_reference edges at 0.9.
    frontend's confidence in being affected by rediscart is 0.81, not 0.9.
    """
    radius = compute_blast_radius(snapshot, _key("rediscart"), cei)
    by_key = {a.key: a for a in radius.affected}

    assert by_key[_key("cartservice")].confidence == pytest.approx(0.9)
    assert by_key[_key("frontend")].confidence == pytest.approx(0.81)


def test_best_path_wins_when_several_exist(snapshot, cei):
    """
    frontend reaches productcatalogservice directly (0.9) and also via
    recommendationservice (0.81). The direct path is the more certain claim.
    """
    radius = compute_blast_radius(snapshot, _key("productcatalogservice"), cei)
    frontend = next(a for a in radius.affected if a.key == _key("frontend"))

    assert frontend.confidence == pytest.approx(0.9)
    assert frontend.hops == 1


def test_declared_edges_do_not_erode_confidence():
    """A chain of Service-selector edges stays at 1.0 however long it is."""
    keys = [f"ns/Deployment/s{i}" for i in range(5)]
    snapshot = {
        "workloads": [{"key": k, "name": k, "namespace": "ns", "kind": "Deployment"} for k in keys],
        "edges": [
            {"source": keys[i], "target": keys[i + 1], "confidence": 1.0,
             "source_kind": "service_selector"}
            for i in range(4)
        ],
    }
    radius = compute_blast_radius(snapshot, keys[4], {})

    assert all(a.confidence == pytest.approx(1.0) for a in radius.affected)
    assert radius.total_affected == 4


def test_long_inferred_chain_is_flagged_as_weak():
    keys = [f"ns/Deployment/s{i}" for i in range(6)]
    snapshot = {
        "workloads": [{"key": k, "name": k, "namespace": "ns", "kind": "Deployment"} for k in keys],
        "edges": [
            {"source": keys[i], "target": keys[i + 1], "confidence": 0.9,
             "source_kind": "env_reference"}
            for i in range(5)
        ],
    }
    affected = {
        a["key"]: a for a in compute_blast_radius(snapshot, keys[5], {}).to_dict()["affected"]
    }

    # 0.9^3 = 0.729 clears the bar; 0.9^4 = 0.656 does not.
    assert affected[keys[2]]["weak_inference"] is False
    assert affected[keys[1]]["weak_inference"] is True
    assert WEAK_PATH_CONFIDENCE == 0.7


def test_via_records_how_the_failure_first_propagates(snapshot, cei):
    radius = compute_blast_radius(snapshot, _key("rediscart"), cei)
    cart = next(a for a in radius.affected if a.key == _key("cartservice"))

    assert cart.via == "env_reference"


# --- robustness ------------------------------------------------------------


def test_cycle_does_not_hang():
    keys = ["ns/Deployment/a", "ns/Deployment/b", "ns/Deployment/c"]
    snapshot = {
        "workloads": [{"key": k, "name": k, "namespace": "ns", "kind": "Deployment"} for k in keys],
        "edges": [
            {"source": "ns/Deployment/a", "target": "ns/Deployment/b", "confidence": 1.0},
            {"source": "ns/Deployment/b", "target": "ns/Deployment/c", "confidence": 1.0},
            {"source": "ns/Deployment/c", "target": "ns/Deployment/a", "confidence": 1.0},
        ],
    }
    radius = compute_blast_radius(snapshot, "ns/Deployment/a", {})

    # Every other node reaches a, and a is never listed as affecting itself.
    assert {a.key for a in radius.affected} == {"ns/Deployment/b", "ns/Deployment/c"}


def test_unknown_workload_is_reported_not_raised(snapshot, cei):
    radius = compute_blast_radius(snapshot, "ns/Deployment/does-not-exist", cei)

    assert radius.exists is False
    assert radius.severity == "none"
    assert radius.to_dict()["total_affected"] == 0


def test_edges_referencing_absent_workloads_are_dropped():
    snapshot = {
        "workloads": [{"key": "ns/Deployment/a", "name": "a", "namespace": "ns", "kind": "Deployment"}],
        "edges": [
            {"source": "ns/Deployment/a", "target": "ns/Deployment/gone", "confidence": 1.0},
            {"source": "ns/Deployment/ghost", "target": "ns/Deployment/a", "confidence": 1.0},
        ],
    }
    graph = build_dependency_graph(snapshot)

    assert set(graph.nodes()) == {"ns/Deployment/a"}
    assert graph.number_of_edges() == 0


def test_empty_snapshot(snapshot):
    radius = compute_blast_radius({"workloads": [], "edges": []}, "anything", {})

    assert radius.exists is False


# --- aggregate views -------------------------------------------------------


def test_centrality_fraction_is_share_of_scored_mass(snapshot, cei):
    radius = compute_blast_radius(snapshot, _key("rediscart"), cei)

    total = sum(v["cei_score"] for v in cei.values())
    expected = (0.74 + 0.88 + 0.81) / total
    assert radius.centrality_fraction == pytest.approx(expected, abs=1e-4)


def test_ranking_puts_the_costliest_failure_first(snapshot, cei):
    ranked = rank_by_blast_radius(snapshot, cei, limit=5)

    assert ranked, "expected at least one workload with dependents"
    assert ranked[0]["severity"] == "critical"
    # Every entry actually has dependents; zero-impact workloads are excluded.
    assert all(r["total_affected"] > 0 for r in ranked)


def test_ranking_differs_from_cei_ranking(snapshot, cei):
    """
    The point of this view: frontend has the highest CEI in the fixture and
    takes nothing down with it, while productcatalogservice scores lower and
    is depended on by three services. Ranking by blast radius must not simply
    reproduce the CEI order.
    """
    ranked = [r["workload_key"] for r in rank_by_blast_radius(snapshot, cei, limit=10)]

    assert _key("frontend") not in ranked
    assert _key("productcatalogservice") in ranked


def test_compute_many_matches_individual_computation(snapshot, cei):
    keys = [_key("rediscart"), _key("cartservice"), _key("adservice")]
    batch = compute_many(snapshot, keys, cei)

    for key in keys:
        assert batch[key].to_dict() == compute_blast_radius(snapshot, key, cei).to_dict()


def test_no_cei_data_still_produces_structure(snapshot):
    radius = compute_blast_radius(snapshot, _key("rediscart"), {})

    assert radius.total_affected == 3
    assert radius.centrality_fraction == 0.0
    assert all(a.cei_score is None for a in radius.affected)
