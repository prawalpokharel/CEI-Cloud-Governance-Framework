"""
Recovery-induced failure risk, from structure alone.

The research name for the failure mode is *metastable failure*: a system that
stays down after the trigger clears, because recovery behaviour -- retries,
reconnections, cold caches -- generates more load than steady state, and that
load sustains the outage. Over half of studied metastable incidents are retry
storms. The defining property is that fixing the trigger does not fix the
failure.

Whether a system is *prone* to this is substantially a property of its
topology, and topology is what this codebase has. This module scores the
static half of the question. The dynamic half -- measuring actual recovery
curves under controlled failure -- lives in the chaos harness, and the two
are designed to check each other: this predicts where recovery will be
expensive, the harness measures whether it was.

## The static signals

**Reconnection fan-in.** When a workload recovers, every direct dependent
re-establishes its connections at the same moment. Steady-state load arrives
spread over time; recovery load arrives as one synchronized spike of
handshakes, auth flows, and cache misses. The spike scales with the number of
connecting *pods*, not dependent workloads -- fifty replicas across five
dependents is fifty simultaneous reconnections.

**Stateful targets amplify.** A stateless service absorbs a reconnect spike
by scaling. A database has connection slots, buffer pools, and a cold cache;
its capacity to absorb a synchronized reconnection is fixed at exactly the
moment demand for it peaks. StatefulSets and database-category externals get
a higher amplification factor for the same fan-in.

**The scaling signal watches the wrong thing.** An HPA scaling a dependent on
its own CPU cannot see upstream recovery pressure coming: queued work during
the outage produces a demand spike that arrives *before* CPU rises, and the
autoscaler reacts after the queue is already deep. This is the elasticity-lag
problem observed at its sharpest moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blast_radius import build_dependency_graph, compute_blast_radius, is_load_bearing

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# Simultaneous reconnecting pods above which recovery load is treated as a
# storm risk rather than noise.
RECONNECT_STORM_THRESHOLD = 10

# Amplification multiplier for stateful targets: fixed capacity meeting a
# synchronized spike.
STATEFUL_FACTOR = 2.0


@dataclass
class Finding:
    kind: str
    severity: str
    workload_key: str
    title: str
    detail: str
    evidence: dict = field(default_factory=dict)
    amplification: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "workload_key": self.workload_key,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "amplification": round(self.amplification, 2),
        }


def _hpa_watches_only_self_resources(hpa: dict) -> bool:
    """
    Whether an HPA's every signal is the target's own CPU/memory.

    An empty metrics list is Kubernetes' default -- CPU at 80% -- so it counts.
    Any External, Pods, or Object metric means somebody wired in an outside
    signal (queue depth, request rate) and the lag critique does not apply.
    """
    metrics = hpa.get("metrics")
    if not metrics:
        return True
    return all((m or {}).get("type") == "Resource" for m in metrics)


def reconnect_amplification(
    snapshot: dict, workload_key: str, graph=None
) -> dict[str, Any]:
    """
    The synchronized load that arrives when this workload comes back.

    amplification = (sum of dependents' ready-or-desired pods) x stateful
    factor. Pods rather than workloads: each pod holds its own connections and
    re-establishes them independently.
    """
    graph = graph if graph is not None else build_dependency_graph(snapshot)
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}
    workload = workloads.get(workload_key)
    if workload is None or workload_key not in graph:
        return {"reconnecting_pods": 0, "amplification": 0.0, "dependents": []}

    dependents = [
        key for key in graph.predecessors(workload_key)
        if graph.nodes[key].get("is_workload") and key in workloads
    ]
    reconnecting = 0
    for key in dependents:
        dependent = workloads[key]
        # Desired, not ready: during the target's outage dependents may be
        # unready themselves, and all of them reconnect on recovery.
        reconnecting += dependent.get("replicas_desired") or 1

    stateful = workload.get("kind") == "StatefulSet"
    factor = STATEFUL_FACTOR if stateful else 1.0
    return {
        "reconnecting_pods": reconnecting,
        "stateful_target": stateful,
        "amplification": reconnecting * factor,
        "dependents": sorted(dependents),
    }


def analyze(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Rank workloads by how badly their recovery could go.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = [w for w in (snapshot.get("workloads") or []) if w.get("key")]
    if not workloads:
        return {"summary": {"total": 0}, "findings": [], "amplification_ranking": []}

    graph = build_dependency_graph(snapshot)
    hpas_by_target = {
        f"{h.get('namespace')}/{h.get('target_kind')}/{h.get('target_name')}": h
        for h in (snapshot.get("autoscalers") or [])
        if h.get("target_kind") and h.get("target_name")
    }

    findings: list[Finding] = []
    ranking: list[dict] = []

    for workload in workloads:
        key = workload["key"]
        amp = reconnect_amplification(snapshot, key, graph=graph)
        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)

        if amp["reconnecting_pods"] > 0:
            ranking.append({
                "workload_key": key,
                **{k: v for k, v in amp.items() if k != "dependents"},
                "dependent_workloads": len(amp["dependents"]),
                "cei_score": (cei_by_workload.get(key) or {}).get("cei_score"),
            })

        # --- reconnect storm ---------------------------------------------
        if amp["amplification"] >= RECONNECT_STORM_THRESHOLD and is_load_bearing(radius):
            stateful_note = (
                " The target is stateful: its connection slots and cold cache "
                "meet the full spike at fixed capacity, at exactly the moment "
                "demand for it peaks."
                if amp["stateful_target"] else ""
            )
            findings.append(Finding(
                kind="reconnect_storm_risk",
                severity="warning" if amp["amplification"] < 3 * RECONNECT_STORM_THRESHOLD else "critical",
                workload_key=key,
                title=(
                    f"Recovery of {workload.get('name')} triggers "
                    f"{amp['reconnecting_pods']} simultaneous reconnections"
                ),
                detail=(
                    "Steady-state load arrives spread over time; recovery load "
                    "arrives all at once. When this workload comes back, every "
                    "dependent pod re-establishes connections, re-authenticates, "
                    "and refills caches in the same instant -- a load profile "
                    "the workload never sees in normal operation and was "
                    "probably never tested against. This is the mechanism "
                    "behind metastable failures: the outage ends and the "
                    "recovery keeps it down." + stateful_note
                ),
                evidence={
                    "reconnecting_pods": amp["reconnecting_pods"],
                    "dependent_workloads": amp["dependents"],
                    "stateful_target": amp["stateful_target"],
                },
                amplification=amp["amplification"],
            ))

        # --- scaling signal lag -------------------------------------------
        hpa = hpas_by_target.get(key)
        upstream = [
            t for t in graph.successors(key)
            if graph.nodes[t].get("is_workload")
        ]
        if hpa and upstream and _hpa_watches_only_self_resources(hpa):
            findings.append(Finding(
                kind="hpa_lagging_signal",
                severity="info" if not is_load_bearing(radius) else "warning",
                workload_key=key,
                title=(
                    f"{workload.get('name')} autoscales on its own CPU while "
                    f"depending on {len(upstream)} upstream service(s)"
                ),
                detail=(
                    "This autoscaler reacts to the workload's own resource "
                    "usage, which rises after queued demand has already "
                    "arrived. When an upstream degrades and recovers, the "
                    "backlog lands here before CPU moves, so scaling begins "
                    "exactly one step behind the spike it exists to absorb. "
                    "An external or queue-depth metric leads the load; a CPU "
                    "metric trails it."
                ),
                evidence={
                    "hpa": hpa.get("name"),
                    "metrics": hpa.get("metrics") or [{"type": "Resource", "resource": "cpu (default)"}],
                    "upstream": sorted(upstream),
                },
                amplification=0.0,
            ))

    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 9), -f.amplification, f.workload_key,
    ))
    ranking.sort(key=lambda r: -r["amplification"])

    return {
        "summary": {
            "total": len(findings),
            "by_severity": {
                s: sum(1 for f in findings if f.severity == s)
                for s in ("critical", "warning", "info")
                if any(f.severity == s for f in findings)
            },
            "note": (
                "Static analysis of recovery topology. It predicts where "
                "recovery load concentrates; the chaos harness's recovery "
                "measurement is the check on whether the prediction holds."
            ),
        },
        "amplification_ranking": ranking[:15],
        "findings": [f.to_dict() for f in findings],
    }


# --------------------------------------------------------------------------
# Phase C: metastability detection and the pre-scale playbook
# --------------------------------------------------------------------------

# Post-recovery load this far above the pre-incident baseline, sustained,
# is the metastable signature. 1.5x rather than anything subtler: the
# pattern being caught is a system running visibly hot after the trigger
# cleared, not ordinary variance.
METASTABLE_LOAD_RATIO = 1.5

# Minimum samples on each side of the dip for the comparison to mean
# anything.
MIN_WINDOW_SAMPLES = 3


def detect_metastability(history: list[dict]) -> dict[str, Any]:
    """
    The signature health checks cannot see: recovered, but load did not.

    ``history`` is a workload's sample series, oldest first, each with
    ``cpu_cores_used`` (may be None). The shape searched for:

        baseline -> dip (the incident) -> readiness restored, but sustained
        CPU well above the pre-incident baseline.

    Every pod reports Ready in that state -- readiness probes measure "can
    serve", not "serving at 2x baseline burning retry work" -- which is
    exactly why metastable failures persist: the dashboards say recovered.

    Windows are compared by median, not mean: a single retry spike in the
    baseline window would otherwise raise the bar the after-window is
    measured against and hide the pattern.
    """
    values = [
        float(s["cpu_cores_used"]) for s in history
        if s.get("cpu_cores_used") is not None
    ]
    if len(values) < MIN_WINDOW_SAMPLES * 3:
        return {
            "detectable": False,
            "reason": (
                f"Needs at least {MIN_WINDOW_SAMPLES * 3} usage samples "
                f"({len(values)} present)."
            ),
        }

    def median(xs):
        xs = sorted(xs)
        mid = len(xs) // 2
        return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2

    # Find the deepest dip; the incident is wherever load fell hardest.
    dip_index = min(range(len(values)), key=lambda i: values[i])
    before = values[:dip_index]
    after = values[dip_index + 1:]
    if len(before) < MIN_WINDOW_SAMPLES or len(after) < MIN_WINDOW_SAMPLES:
        return {"detectable": False,
                "reason": "The dip sits at the edge of the window."}

    baseline = median(before[-MIN_WINDOW_SAMPLES * 2:])
    recovered = median(after[-MIN_WINDOW_SAMPLES:])
    if baseline <= 0:
        return {"detectable": False, "reason": "No measurable baseline load."}

    ratio = recovered / baseline
    return {
        "detectable": True,
        "metastable_suspected": ratio >= METASTABLE_LOAD_RATIO,
        "baseline_cpu": round(baseline, 4),
        "post_recovery_cpu": round(recovered, 4),
        "load_ratio": round(ratio, 3),
        "dip_sample_index": dip_index,
        "note": (
            "Post-recovery load is sustained at "
            f"{ratio:.1f}x the pre-incident baseline. Readiness probes "
            "report healthy in this state; the elevated load is retry and "
            "reconnection work, and it is what keeps metastable failures "
            "alive after their trigger is gone."
            if ratio >= METASTABLE_LOAD_RATIO else
            "Load returned to baseline after the dip; recovery was clean."
        ),
    }


def pre_scale_playbook(
    snapshot: dict,
    upstream_key: str,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    When ``upstream_key`` degrades, who to scale, in what order.

    The elasticity-lag answer in playbook form: the dependents' backlog
    arrives before their CPU moves, so the time to scale them is when the
    upstream degrades, not when their own metrics notice. Ordered by blast
    radius -- the dependent that the most other things depend on is the one
    whose queue must not be allowed to deepen.

    Advisory output, deliberately: this is the runbook an operator (or an
    automation THEY choose to wire up) executes. Feeding it into a fast
    control loop uninspected is the oscillation risk the stability monitor
    exists to prevent.
    """
    cei_by_workload = cei_by_workload or {}
    graph = build_dependency_graph(snapshot)
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}

    if upstream_key not in graph:
        return {"upstream": upstream_key, "found": False, "steps": []}

    dependents = [
        key for key in graph.predecessors(upstream_key)
        if graph.nodes[key].get("is_workload") and key in workloads
    ]

    steps = []
    for key in dependents:
        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        workload = workloads[key]
        replicas = workload.get("replicas_desired") or 1
        steps.append({
            "workload_key": key,
            "current_replicas": replicas,
            # +50% rounded up: absorbs a queue that built during the outage
            # without doubling spend on a hunch.
            "suggested_replicas": replicas + max(1, replicas // 2),
            "own_dependents": radius.total_affected,
            "user_facing": bool(radius.entry_points),
            "reason": (
                "Backlog from the degraded upstream lands here before this "
                "workload's own metrics move; scaling now absorbs the spike "
                "that CPU-based autoscaling will react to one step late."
            ),
        })

    steps.sort(key=lambda s: (-int(s["user_facing"]), -s["own_dependents"]))
    return {
        "upstream": upstream_key,
        "found": True,
        "steps": steps,
        "note": (
            "Advisory playbook. Execute on upstream degradation, unwind "
            "when its recovery completes -- see the recovery curve "
            "measurement for when that actually is."
        ),
    }
