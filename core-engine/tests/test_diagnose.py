"""
Incident diagnosis scenarios.

Each test is a 3am shape: the cascade, the double incident, the shared
external cause, the release that broke it, the cycle, the red herring. The
property under test is always the same one -- the engine must point at the
first broken dependency path and refuse to blame collateral.
"""

import pytest

from src.services.diagnose import classify_health, diagnose, incident_brief


def _workload(name, *, ns="prod", desired=2, ready=2, ownership=None):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": desired, "replicas_ready": ready,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "images": ["repo/app@sha256:abc"],
        "ownership": ownership or {},
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _pod(workload, name, *, ns="prod", waiting=(), terminated=(), restarts=0,
         phase="Running"):
    return {"name": name, "namespace": ns, "labels": {"app": workload},
            "phase": phase, "restart_count": restarts,
            "waiting_reasons": list(waiting),
            "last_terminated_reasons": list(terminated), "node_name": "n1"}


def _edge(src, dst, ns="prod"):
    return {"source": f"{ns}/Deployment/{src}", "target": f"{ns}/Deployment/{dst}",
            "confidence": 0.9, "source_kind": "env_reference"}


def _snapshot(workloads, edges=(), pods=(), egress=None):
    snap = {"workloads": list(workloads), "edges": list(edges),
            "pods": list(pods), "services": [], "disruption_budgets": [],
            "autoscalers": []}
    if egress:
        snap["egress"] = egress
    return snap


def _key(name, ns="prod"):
    return f"{ns}/Deployment/{name}"


def _roots(result):
    return [r["workload_key"] for r in result["roots"]]


# ═══ the basic cascade ═══════════════════════════════════════════════════


@pytest.fixture
def cascade():
    """db is down; api, web, worker are down BECAUSE of it. One incident."""
    return _snapshot(
        workloads=[
            _workload("db", desired=2, ready=0),
            _workload("api", desired=2, ready=0),
            _workload("web", desired=2, ready=0),
            _workload("worker", desired=2, ready=0),
            _workload("untouched"),
        ],
        edges=[_edge("api", "db"), _edge("web", "api"), _edge("worker", "db")],
    )


def test_cascade_yields_one_root_not_four(cascade):
    result = diagnose(cascade)

    assert result["incident"] is True
    assert _roots(result) == [_key("db")]
    root = result["roots"][0]
    assert set(root["collateral"]) == {_key("api"), _key("web"), _key("worker")}
    assert root["explains_workloads"] == 3


def test_collateral_is_never_a_separate_investigation(cascade):
    result = diagnose(cascade)

    assert set(result["collateral_map"]) == {_key("api"), _key("web"), _key("worker")}
    assert result["unexplained"] == []


def test_transitive_collateral_attributes_to_the_frontier(cascade):
    """web -> api -> db: web's root is db, not api."""
    result = diagnose(cascade)

    assert result["collateral_map"][_key("web")] == _key("db")


def test_the_calm_number_is_reported(cascade):
    result = diagnose(cascade)

    assert result["healthy_workloads"] == 1
    assert "1" not in ""  # placeholder clarity; the real assertion:
    assert result["unhealthy_workloads"] == 4


def test_no_restart_advice_for_collateral(cascade):
    actions = " ".join(diagnose(cascade)["roots"][0]["next_actions"])

    assert "Do not restart" in actions
    assert "reconnect storm" in actions


# ═══ health classification evidence grades ═══════════════════════════════


def test_oom_is_self_caused_even_with_a_broken_dependency():
    """OOM originates locally; a dead database cannot exceed your memory limit."""
    snap = _snapshot(
        workloads=[_workload("db", ready=0), _workload("api", ready=0)],
        edges=[_edge("api", "db")],
        pods=[_pod("api", "api-1", terminated=["OOMKilled"], restarts=4)],
    )
    result = diagnose(snap)

    assert set(_roots(result)) == {_key("db"), _key("api")}
    api = next(r for r in result["roots"] if r["workload_key"] == _key("api"))
    assert api["confidence"] == "high"
    assert "OOM" in api["confidence_reason"] or "self-caused" in api["confidence_reason"]


def test_image_pull_failure_is_always_a_root():
    snap = _snapshot(
        workloads=[_workload("db", ready=0), _workload("api", ready=0)],
        edges=[_edge("api", "db")],
        pods=[_pod("api", "api-1", waiting=["ImagePullBackOff"])],
    )
    api = next(r for r in diagnose(snap)["roots"]
               if r["workload_key"] == _key("api"))

    assert api["confidence"] == "high"
    assert any("image reference" in a for a in api["next_actions"])


def test_crash_loop_with_dead_dependency_is_collateral_not_root():
    """
    Services crash on startup when their database is away. A crash loop only
    counts as self-evidence when the dependencies are fine.
    """
    snap = _snapshot(
        workloads=[_workload("db", ready=0), _workload("api", ready=0)],
        edges=[_edge("api", "db")],
        pods=[_pod("api", "api-1", waiting=["CrashLoopBackOff"],
                   terminated=["Error"], restarts=7)],
    )
    result = diagnose(snap)

    assert _roots(result) == [_key("db")]
    assert result["collateral_map"][_key("api")] == _key("db")


def test_crash_loop_with_healthy_dependencies_is_a_high_confidence_root():
    snap = _snapshot(
        workloads=[_workload("db"), _workload("api", ready=0)],
        edges=[_edge("api", "db")],
        pods=[_pod("api", "api-1", waiting=["CrashLoopBackOff"],
                   terminated=["Error"], restarts=7)],
    )
    api = diagnose(snap)["roots"][0]

    assert api["workload_key"] == _key("api")
    assert api["confidence"] == "high"
    assert any("logs" in a for a in api["next_actions"])


def test_transient_crash_loop_detected_from_restarts():
    """The waiting reason is a coin flip; restart evidence is durable."""
    health = classify_health(_snapshot(
        workloads=[_workload("api", ready=1)],
        pods=[_pod("api", "api-1", terminated=["Error"], restarts=5)],
    ))
    assert "crash_loop" in health[_key("api")].states


# ═══ two independent incidents ═══════════════════════════════════════════


def test_two_disjoint_failures_are_two_roots():
    snap = _snapshot(
        workloads=[
            _workload("db", ready=0), _workload("api", ready=0),
            _workload("cache", ready=0), _workload("feed", ready=0),
        ],
        edges=[_edge("api", "db"), _edge("feed", "cache")],
    )
    result = diagnose(snap)

    assert set(_roots(result)) == {_key("db"), _key("cache")}
    by_key = {r["workload_key"]: r for r in result["roots"]}
    assert by_key[_key("db")]["collateral"] == [_key("api")]
    assert by_key[_key("cache")]["collateral"] == [_key("feed")]


def test_shared_collateral_is_claimed_by_both_roots():
    """gateway depends on both failing subsystems; both explain it."""
    snap = _snapshot(
        workloads=[_workload("db", ready=0), _workload("auth", ready=0),
                   _workload("gateway", ready=0)],
        edges=[_edge("gateway", "db"), _edge("gateway", "auth")],
    )
    result = diagnose(snap)

    assert set(_roots(result)) == {_key("db"), _key("auth")}
    for root in result["roots"]:
        assert root["collateral"] == [_key("gateway")]


# ═══ common external cause ═══════════════════════════════════════════════


def test_two_roots_sharing_an_external_dependency_promote_it():
    """
    checkout and profile fail "independently" -- but both call the same
    managed database. The engine's conclusion must be the one that takes a
    human longest at 3am: the suspect is outside the cluster.
    """
    egress = {"available": True, "workloads": {
        _key("checkout"): [{"destination": "prod-db.abc.us-east-1.rds.amazonaws.com",
                            "ports": [5432], "dns_resolved": True,
                            "flow_count": 9, "dropped_count": 0, "ips": []}],
        _key("profile"): [{"destination": "prod-db.abc.us-east-1.rds.amazonaws.com",
                           "ports": [5432], "dns_resolved": True,
                           "flow_count": 9, "dropped_count": 0, "ips": []}],
    }}
    snap = _snapshot(
        workloads=[_workload("checkout", ready=0), _workload("profile", ready=0),
                   _workload("web", ready=0)],
        edges=[_edge("web", "checkout")],
        egress=egress,
    )
    result = diagnose(snap)

    suspect = result["external_suspects"][0]
    assert suspect["assessment"] == "prime_suspect"
    assert suspect["endpoint"] == "prod-db.abc.us-east-1.rds.amazonaws.com"
    assert set(suspect["roots_depending_on_it"]) == {_key("checkout"), _key("profile")}
    assert "status page" in suspect["detail"]
    # The in-cluster roots drop to low confidence with the reason stated.
    for root in result["roots"]:
        if root["workload_key"] in suspect["roots_depending_on_it"]:
            assert root["confidence"] == "low"
            assert "external" in root["confidence_reason"]
    assert "OUTSIDE the cluster" in result["summary"]


def test_single_root_with_heavyweight_external_is_flagged_possible():
    egress = {"available": True, "workloads": {
        _key("api"): [{"destination": "tenant.auth0.com", "ports": [443],
                       "dns_resolved": True, "flow_count": 5,
                       "dropped_count": 0, "ips": []}],
    }}
    snap = _snapshot(
        workloads=[_workload("api", ready=0)], egress=egress,
    )
    suspects = diagnose(snap)["external_suspects"]

    assert suspects and suspects[0]["assessment"] == "possible"


def test_no_egress_data_means_no_external_claims():
    result = diagnose(_snapshot(workloads=[_workload("api", ready=0)]))

    assert result["external_suspects"] == []


# ═══ change correlation ══════════════════════════════════════════════════


def test_release_on_the_root_is_the_lead():
    previous = _snapshot(workloads=[
        dict(_workload("api"), images=["repo/api@sha256:old"]),
        _workload("web"),
    ], edges=[_edge("web", "api")])
    current = _snapshot(workloads=[
        dict(_workload("api", ready=0), images=["repo/api@sha256:new"]),
        _workload("web", ready=0),
    ], edges=[_edge("web", "api")])

    root = diagnose(current, previous)["roots"][0]

    assert root["workload_key"] == _key("api")
    assert root["recent_changes"][0]["kind"] == "image_changed"
    assert any("rolling it back" in a for a in root["next_actions"])


def test_scale_down_on_the_root_is_reported():
    previous = _snapshot(workloads=[_workload("api", desired=4, ready=4)])
    current = _snapshot(workloads=[_workload("api", desired=1, ready=0)])
    root = diagnose(current, previous)["roots"][0]

    assert any(c["kind"] == "replicas_changed" for c in root["recent_changes"])


def test_changes_on_collateral_are_not_reported_as_leads(cascade):
    """A release on collateral is noise until the root is explained."""
    previous = _snapshot(workloads=[
        _workload("db"), dict(_workload("api"), images=["repo/api@sha256:old"]),
        _workload("web"), _workload("worker"), _workload("untouched"),
    ], edges=[_edge("api", "db"), _edge("web", "api"), _edge("worker", "db")])

    result = diagnose(cascade, previous)

    assert result["roots"][0]["recent_changes"] == []


# ═══ onset ordering ══════════════════════════════════════════════════════


def _history(samples):
    """samples: list of (ready, desired)."""
    return [{"replicas_ready": r, "replicas_desired": d} for r, d in samples]


def test_root_that_failed_after_its_dependents_is_marked_suspect():
    """
    The graph says db is the frontier, but the timeline says api died first.
    A true root does not go down after its collateral -- confidence drops
    with the reason stated.
    """
    snap = _snapshot(
        workloads=[_workload("db", ready=0), _workload("api", ready=0)],
        edges=[_edge("api", "db")],
    )
    history = {
        _key("api"): _history([(2, 2), (0, 2), (0, 2), (0, 2)]),  # died at t1
        _key("db"): _history([(2, 2), (2, 2), (2, 2), (0, 2)]),   # died at t3
    }
    root = diagnose(snap, history_by_workload=history)["roots"][0]

    assert root["workload_key"] == _key("db")
    assert root["confidence"] == "low"
    assert "AFTER" in root["confidence_reason"]


def test_consistent_timeline_keeps_confidence():
    snap = _snapshot(
        workloads=[_workload("db", ready=0), _workload("api", ready=0)],
        edges=[_edge("api", "db")],
    )
    history = {
        _key("db"): _history([(2, 2), (0, 2), (0, 2), (0, 2)]),
        _key("api"): _history([(2, 2), (2, 2), (0, 2), (0, 2)]),
    }
    root = diagnose(snap, history_by_workload=history)["roots"][0]

    assert root["confidence"] == "medium"


def test_dependency_cycle_resolved_by_onset():
    """a <-> b both down: the graph has no frontier; the timeline decides."""
    snap = _snapshot(
        workloads=[_workload("a", ready=0), _workload("b", ready=0)],
        edges=[_edge("a", "b"), _edge("b", "a")],
    )
    history = {
        _key("a"): _history([(2, 2), (2, 2), (0, 2)]),
        _key("b"): _history([(2, 2), (0, 2), (0, 2)]),  # b first
    }
    result = diagnose(snap, history_by_workload=history)

    assert _key("b") in _roots(result)


# ═══ healthy cluster and edge cases ══════════════════════════════════════


def test_healthy_cluster_says_so():
    result = diagnose(_snapshot(workloads=[_workload("a"), _workload("b")]))

    assert result["incident"] is False
    assert "healthy" in result["summary"]


def test_empty_snapshot():
    result = diagnose({"workloads": [], "edges": [], "pods": []})

    assert result["incident"] is False


def test_unknown_ready_state_is_not_unhealthy():
    """A v1 agent may send None; unknown must not create phantom incidents."""
    result = diagnose(_snapshot(
        workloads=[dict(_workload("a"), replicas_ready=None)],
    ))
    assert result["incident"] is False


def test_pending_pods_mark_the_workload():
    health = classify_health(_snapshot(
        workloads=[_workload("api", ready=1)],
        pods=[_pod("api", "api-2", phase="Pending")],
    ))
    assert "pending" in health[_key("api")].states


# ═══ the page list ═══════════════════════════════════════════════════════


def test_root_owner_is_surfaced_for_paging():
    snap = _snapshot(workloads=[
        _workload("db", ready=0,
                  ownership={"team": "storage", "slack": "#storage-oncall",
                             "app.kubernetes.io/managed-by": "Helm"}),
    ])
    root = diagnose(snap)["roots"][0]

    assert root["page"] == {"team": "storage", "slack": "#storage-oncall"}


# ═══ the brief ═══════════════════════════════════════════════════════════


def test_brief_reads_like_an_incident_channel_message(cascade):
    brief = incident_brief(diagnose(cascade), cluster_name="prod-east")

    assert "Incident diagnosis — prod-east" in brief
    assert "db" in brief
    assert "collateral (leave alone)" in brief
    assert "healthy and uninvolved" in brief


def test_brief_leads_with_the_external_suspect():
    egress = {"available": True, "workloads": {
        _key("a"): [{"destination": "tenant.auth0.com", "ports": [443],
                     "dns_resolved": True, "flow_count": 5,
                     "dropped_count": 0, "ips": []}],
        _key("b"): [{"destination": "tenant.auth0.com", "ports": [443],
                     "dns_resolved": True, "flow_count": 5,
                     "dropped_count": 0, "ips": []}],
    }}
    snap = _snapshot(
        workloads=[_workload("a", ready=0), _workload("b", ready=0)],
        egress=egress,
    )
    brief = incident_brief(diagnose(snap))

    assert "Check tenant.auth0.com first" in brief


def test_brief_for_a_healthy_cluster_is_one_line():
    brief = incident_brief(diagnose(_snapshot(workloads=[_workload("a")])))

    assert "healthy" in brief
    assert len(brief.splitlines()) == 1
