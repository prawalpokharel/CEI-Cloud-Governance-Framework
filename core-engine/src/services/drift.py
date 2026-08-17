"""
Structural drift: what a release did to the shape of the system.

CEI is a planning-time score. Its absolute value answers "where should
attention go", which is a question asked deliberately, in a review, not
continuously. Alerting on the score itself would be alerting on a standing
property -- `payments` was critical yesterday and is critical today, and
paging somebody about that is how a signal gets muted.

The *change* is different. A workload becoming central overnight is an event,
and it is an event nobody observes: no deploy fails, no pod restarts, no
metric moves. Someone added an environment variable pointing four services at
a fifth, and the fifth is now load-bearing for the whole estate. That is worth
knowing on the day it happens, while the commit that caused it is still on the
first page of the log.

## Why this is a legitimate runtime signal for a planning-time score

The distinction matters and is easy to get wrong. This does not use CEI to
make an allocation decision, throttle traffic, or drive an autoscaler -- the
score is not stable enough at that timescale to be a control input, and
feeding a lagging score into a fast loop is what the oscillation work exists
to prevent.

What it does is detect a *structural event* and report it. The output is "your
topology changed in this specific way, here is the workload and here is the
window", which a human then evaluates. Detection at runtime, decision at
planning time.

## What is compared

Snapshots are full cluster state, so any two are directly comparable. The
comparison is deliberately structural rather than score-wide: utilization
moves constantly and would drown the signal, whereas an edge appearing is
discrete and almost always deliberate.

* **Concentration**, the same normalised HHI the pre-merge check uses, so a
  prediction made before a merge and the measurement made after are in the
  same units and can be checked against each other.
* **Per-workload reach**, the fraction of the cluster depending on a workload.
* **Topology events**: dependencies added or removed, workloads appearing or
  disappearing, and a workload crossing into load-bearing for the first time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blast_radius import (
    LOAD_BEARING_MIN_DEPENDENTS,
    build_dependency_graph,
    compute_blast_radius,
    is_load_bearing,
)
from .graph_simulation import concentration, dependent_fraction, structural_centrality

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Relative concentration change worth reporting. Below this, ordinary churn --
# a batch job appearing, a canary scaling to zero -- produces movement that
# means nothing.
CONCENTRATION_ALERT = 0.15

# Absolute change in the fraction of the cluster depending on a workload.
# Absolute rather than relative because a workload going from 2% to 4% has
# doubled and does not matter, while 40% to 55% has not doubled and does.
REACH_ALERT = 0.10


@dataclass
class DriftEvent:
    kind: str
    severity: str
    title: str
    detail: str
    workload_key: str | None = None
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "workload_key": self.workload_key,
            "evidence": self.evidence,
        }


def _edge_set(snapshot: dict) -> set[tuple[str, str]]:
    return {
        (e["source"], e["target"])
        for e in (snapshot.get("edges") or [])
        if e.get("source") and e.get("target")
    }


def _release_marker(before: dict, after: dict) -> dict[str, list[str]]:
    """
    Image changes between the two snapshots.

    What makes drift actionable rather than merely observed: "concentration
    rose 40%" is a fact, and "concentration rose 40% and these three workloads
    changed image in the same window" is a lead.
    """
    before_images = {
        w["key"]: sorted(w.get("images") or [])
        for w in (before.get("workloads") or []) if w.get("key")
    }
    changed = {}
    for workload in after.get("workloads") or []:
        key = workload.get("key")
        if not key or key not in before_images:
            continue
        now = sorted(workload.get("images") or [])
        if now != before_images[key]:
            changed[key] = now
    return changed


def compare_snapshots(
    before: dict,
    after: dict,
    *,
    before_at: str | None = None,
    after_at: str | None = None,
) -> dict[str, Any]:
    """
    Detect structural events between two cluster snapshots.
    """
    before_graph = build_dependency_graph(before)
    after_graph = build_dependency_graph(after)

    before_reach = dependent_fraction(before_graph)
    after_reach = dependent_fraction(after_graph)
    before_conc = concentration(structural_centrality(before_graph))
    after_conc = concentration(structural_centrality(after_graph))
    conc_delta = after_conc - before_conc

    before_keys = {w["key"] for w in (before.get("workloads") or []) if w.get("key")}
    after_keys = {w["key"] for w in (after.get("workloads") or []) if w.get("key")}
    before_edges, after_edges = _edge_set(before), _edge_set(after)
    releases = _release_marker(before, after)

    events: list[DriftEvent] = []

    # --- concentration --------------------------------------------------
    relative = conc_delta / before_conc if before_conc > 1e-9 else None
    introduced = before_conc <= 1e-9 and after_conc >= 0.05
    if introduced or (relative is not None and relative >= CONCENTRATION_ALERT):
        suspects = sorted(releases)[:5]
        events.append(DriftEvent(
            kind="concentration_rose",
            severity="critical" if introduced or (relative or 0) >= 0.4 else "warning",
            title=(
                f"Structural concentration rose {relative:.0%}"
                if relative is not None else
                "Structural concentration appeared where there was none"
            ),
            detail=(
                f"Concentration moved from {before_conc:.3f} to {after_conc:.3f} "
                "between these snapshots. More of the system's structural "
                "importance now sits in fewer workloads, so more of it fails "
                "together. Nothing failed to make this happen and no metric "
                "moved -- the topology changed."
                + (
                    f" {len(releases)} workload(s) changed image in the same "
                    f"window: {', '.join(s.split('/')[-1] for s in suspects)}"
                    + ("…" if len(releases) > 5 else "")
                    if releases else
                    " No image changed in this window, so the cause is a "
                    "configuration or scaling change rather than a release."
                )
            ),
            evidence={
                "before": round(before_conc, 4),
                "after": round(after_conc, 4),
                "relative_change": None if relative is None else round(relative, 4),
                "released_workloads": sorted(releases),
                "before_at": before_at,
                "after_at": after_at,
            },
        ))
    elif relative is not None and relative <= -CONCENTRATION_ALERT:
        events.append(DriftEvent(
            kind="concentration_fell",
            severity="info",
            title=f"Structural concentration fell {abs(relative):.0%}",
            detail=(
                f"Concentration moved from {before_conc:.3f} to "
                f"{after_conc:.3f}. Structural importance is more evenly "
                "spread than it was."
            ),
            evidence={"before": round(before_conc, 4), "after": round(after_conc, 4)},
        ))

    # --- workloads crossing into load-bearing ---------------------------
    for key in sorted(after_keys):
        was = before_reach.get(key, 0.0)
        now = after_reach.get(key, 0.0)
        if now - was < REACH_ALERT:
            continue

        radius = compute_blast_radius(after, key, {}, graph=after_graph)
        became = is_load_bearing(radius) and key in before_keys and not is_load_bearing(
            compute_blast_radius(before, key, {}, graph=before_graph)
        )

        new_callers = sorted(
            source for source, target in after_edges - before_edges if target == key
        )
        events.append(DriftEvent(
            kind="became_load_bearing" if became else "reach_increased",
            severity="critical" if became else "warning",
            title=(
                f"{key.split('/')[-1]} became load-bearing"
                if became else
                f"{key.split('/')[-1]} is depended on by "
                f"{now:.0%} of the cluster, up from {was:.0%}"
            ),
            detail=(
                (
                    "This workload was not structurally critical in the "
                    "previous snapshot and is now. It crossed the threshold "
                    "without failing, restarting, or changing its own "
                    "resource usage -- something started depending on it."
                    if became else
                    "More of the cluster depends on this workload than did "
                    "before."
                )
                + (
                    f" New dependencies: "
                    f"{', '.join(s.split('/')[-1] for s in new_callers[:5])}"
                    + ("…" if len(new_callers) > 5 else "")
                    if new_callers else
                    " No new direct dependency appeared, so the change came "
                    "from further up the graph."
                )
                + (
                    "\n\nIt has no PodDisruptionBudget and no redundancy "
                    "review has happened since it became critical, because "
                    "nothing announced that it had."
                    if became else ""
                )
            ),
            workload_key=key,
            evidence={
                "reach_before": round(was, 4),
                "reach_after": round(now, 4),
                "dependents_after": radius.total_affected,
                "new_callers": new_callers,
                "user_facing": bool(radius.entry_points),
                "released_in_window": key in releases,
            },
        ))

    # --- topology churn ---------------------------------------------------
    added_edges = sorted(after_edges - before_edges)
    removed_edges = sorted(before_edges - after_edges)
    appeared = sorted(after_keys - before_keys)
    disappeared = sorted(before_keys - after_keys)

    for key in disappeared:
        radius = compute_blast_radius(before, key, {}, graph=before_graph)
        if radius.total_affected >= LOAD_BEARING_MIN_DEPENDENTS:
            events.append(DriftEvent(
                kind="load_bearing_workload_disappeared",
                severity="critical",
                title=f"{key.split('/')[-1]} is gone and {radius.total_affected} workloads depended on it",
                detail=(
                    "A workload that other workloads depend on is no longer "
                    "in the cluster. Either it was renamed, moved namespace, "
                    "or removed -- and if the last, its dependents are now "
                    "calling something that does not answer."
                ),
                workload_key=key,
                evidence={"dependents": sorted(a.key for a in radius.affected)},
            ))

    events.sort(key=lambda e: (SEVERITY_ORDER.get(e.severity, 9), e.title))

    return {
        "window": {"before_at": before_at, "after_at": after_at},
        "concentration": {
            "before": round(before_conc, 4),
            "after": round(after_conc, 4),
            "delta": round(conc_delta, 4),
            "relative_change": None if relative is None else round(relative, 4),
        },
        "topology": {
            "workloads_before": len(before_keys),
            "workloads_after": len(after_keys),
            "edges_before": len(before_edges),
            "edges_after": len(after_edges),
            "workloads_appeared": appeared,
            "workloads_disappeared": disappeared,
            "dependencies_added": [list(e) for e in added_edges],
            "dependencies_removed": [list(e) for e in removed_edges],
            "workloads_released": sorted(releases),
        },
        "events": [e.to_dict() for e in events],
        "summary": {
            "total": len(events),
            "by_severity": {
                s: sum(1 for e in events if e.severity == s)
                for s in ("critical", "warning", "info")
                if any(e.severity == s for e in events)
            },
            "note": (
                "Structural events only. This reports that the topology "
                "changed and what changed it; it is not a control signal and "
                "does not drive any automated action."
            ),
        },
    }
