"""
Structural drift between snapshots.

The signal is the change, not the score. A workload that was critical
yesterday and is critical today is not an event; a workload that became
critical overnight is, and nothing else in the system announces it.
"""

import pytest

from src.services.drift import CONCENTRATION_ALERT, REACH_ALERT, compare_snapshots


def _workload(name, ns="prod", image="repo/app:1.0"):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": 2,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "images": [image], "config_refs": {"config_maps": [], "secrets": []},
    }


def _edge(src, dst, ns="prod"):
    return {"source": f"{ns}/Deployment/{src}", "target": f"{ns}/Deployment/{dst}",
            "confidence": 0.9, "source_kind": "env_reference"}


def _snapshot(names, edges=(), images=None):
    images = images or {}
    return {
        "workloads": [
            _workload(n, image=images.get(n, "repo/app:1.0")) for n in names
        ],
        "edges": [_edge(a, b) for a, b in edges],
        "services": [], "pods": [],
    }


def _kinds(result):
    return {e["kind"] for e in result["events"]}


def _find(result, kind):
    return next(e for e in result["events"] if e["kind"] == kind)


NAMES = ["api", "worker", "reporting", "billing", "auth"]


# --- concentration ----------------------------------------------------------


def test_new_shared_dependency_raises_concentration():
    """Four services pointed at one. Nothing failed; the topology changed."""
    before = _snapshot(NAMES)
    after = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    result = compare_snapshots(before, after)

    assert result["concentration"]["after"] > result["concentration"]["before"]
    assert "concentration_rose" in _kinds(result)


def test_stable_topology_produces_no_events():
    """
    The whole design: a system that is concentrated and stays concentrated is
    not an event. Alerting on the standing score is how a signal gets muted.
    """
    snapshot = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    result = compare_snapshots(snapshot, snapshot)

    assert result["events"] == []
    assert result["concentration"]["delta"] == 0.0


def test_concentration_falling_is_informational():
    before = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    after = _snapshot(NAMES, [("api", "auth")])
    result = compare_snapshots(before, after)

    assert _find(result, "concentration_fell")["severity"] == "info"


def test_release_in_the_window_is_named_as_a_suspect():
    """
    "Concentration rose 40%" is a fact. "...and these workloads changed image
    in the same window" is a lead.
    """
    before = _snapshot(NAMES)
    after = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]],
                      images={"api": "repo/app:2.0"})
    finding = _find(compare_snapshots(before, after), "concentration_rose")

    assert finding["evidence"]["released_workloads"] == ["prod/Deployment/api"]
    assert "changed image in the same window" in finding["detail"]


def test_no_release_says_so_explicitly():
    before = _snapshot(NAMES)
    after = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    finding = _find(compare_snapshots(before, after), "concentration_rose")

    assert "No image changed" in finding["detail"]
    assert "configuration or scaling change" in finding["detail"]


# --- becoming load-bearing --------------------------------------------------


def test_workload_becoming_load_bearing_is_critical():
    before = _snapshot(NAMES, [("api", "auth")])
    after = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    finding = _find(compare_snapshots(before, after), "became_load_bearing")

    assert finding["workload_key"] == "prod/Deployment/auth"
    assert finding["severity"] == "critical"
    assert "without failing, restarting" in finding["detail"]


def test_new_callers_are_named():
    before = _snapshot(NAMES, [("api", "auth")])
    after = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    finding = _find(compare_snapshots(before, after), "became_load_bearing")

    assert set(finding["evidence"]["new_callers"]) == {
        "prod/Deployment/worker", "prod/Deployment/reporting",
        "prod/Deployment/billing",
    }


def test_already_load_bearing_is_not_reported_as_becoming():
    """It was critical before and is critical now. Not an event."""
    before = _snapshot(NAMES, [(n, "auth") for n in NAMES[:3]])
    after = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    result = compare_snapshots(before, after)

    assert "became_load_bearing" not in _kinds(result)


def test_small_reach_change_is_below_the_threshold():
    names = [f"s{i}" for i in range(30)]
    before = _snapshot(names, [(n, "s0") for n in names[1:10]])
    after = _snapshot(names, [(n, "s0") for n in names[1:11]])
    result = compare_snapshots(before, after)

    assert REACH_ALERT == 0.10
    assert "reach_increased" not in _kinds(result)


# --- disappearance ----------------------------------------------------------


def test_load_bearing_workload_disappearing_is_critical():
    before = _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]])
    after = _snapshot([n for n in NAMES if n != "auth"])
    finding = _find(compare_snapshots(before, after), "load_bearing_workload_disappeared")

    assert finding["workload_key"] == "prod/Deployment/auth"
    assert len(finding["evidence"]["dependents"]) == 4


def test_isolated_workload_disappearing_is_not_reported():
    before = _snapshot(NAMES)
    after = _snapshot([n for n in NAMES if n != "billing"])

    assert "load_bearing_workload_disappeared" not in _kinds(compare_snapshots(before, after))


# --- topology reporting -----------------------------------------------------


def test_topology_diff_is_reported():
    before = _snapshot(["api", "auth"], [("api", "auth")])
    after = _snapshot(["api", "auth", "new"], [("api", "auth"), ("new", "auth")])
    topology = compare_snapshots(before, after)["topology"]

    assert topology["workloads_appeared"] == ["prod/Deployment/new"]
    assert topology["workloads_disappeared"] == []
    assert ["prod/Deployment/new", "prod/Deployment/auth"] in topology["dependencies_added"]
    assert topology["edges_before"] == 1 and topology["edges_after"] == 2


def test_window_timestamps_are_carried_through():
    snapshot = _snapshot(NAMES)
    result = compare_snapshots(
        snapshot, snapshot, before_at="2026-08-15T00:00:00Z",
        after_at="2026-08-16T00:00:00Z",
    )

    assert result["window"]["before_at"] == "2026-08-15T00:00:00Z"


def test_summary_states_this_is_not_a_control_signal():
    """
    The distinction that makes a planning-time score legitimate at runtime:
    it detects an event and reports it; it drives nothing.
    """
    note = compare_snapshots(_snapshot(NAMES), _snapshot(NAMES))["summary"]["note"]

    assert "not a control signal" in note


def test_empty_snapshots_do_not_crash():
    result = compare_snapshots({"workloads": [], "edges": []}, {"workloads": [], "edges": []})

    assert result["events"] == []


def test_first_snapshot_against_empty_cluster():
    result = compare_snapshots(
        {"workloads": [], "edges": []},
        _snapshot(NAMES, [(n, "auth") for n in NAMES[:4]]),
    )

    assert result["concentration"]["after"] > 0
    assert CONCENTRATION_ALERT == 0.15
