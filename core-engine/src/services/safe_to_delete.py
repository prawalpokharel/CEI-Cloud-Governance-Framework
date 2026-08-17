"""
Whether a cost recommendation is safe to execute.

Every FinOps tool in the market answers the same question -- *what is not
being used* -- and answers it from utilization alone. Utilization cannot
distinguish the two things that look identical from the outside:

    a workload that is idle because nobody needs it
    a workload that is idle because nothing has failed yet

The second is a failover path, a standby replica, a circuit breaker, a
disaster-recovery deployment, a batch job between runs. It is idle by design.
Deleting it produces a saving on the next invoice and an outage on the next
bad day, and nothing connects the two events.

This module does not find waste. It sits in front of a recommendation that
already exists -- from `cost.py`, from Kubecost, from a spreadsheet -- and
decides whether acting on it is safe, using the one thing utilization data
does not contain: what else depends on this.

## The verdicts

* **safe** -- nothing depends on it, nothing protects it, no evidence it
  matters. Act.
* **review** -- something here needs a human. Reported with what specifically
  to check, never as a vague warning.
* **unsafe** -- acting on this will break something identifiable.

Absence of evidence produces `review`, never `safe`. A workload that cannot be
assessed is not a workload that has been cleared, and getting that backwards
is how a cost tool causes an incident.

## What this cannot see

Without flow data, "nothing depends on it" means nothing *declares* a
dependency on it. A client that hardcodes a DNS name in a config file is
invisible here. That limitation is stated in every verdict rather than
buried, because the cost of a false `safe` is an outage and the cost of a
false `review` is five minutes of someone's time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blast_radius import build_dependency_graph, compute_blast_radius

# Actions this module can assess, ordered by how irreversible they are.
DELETE = "delete"
SCALE_TO_ZERO = "scale_to_zero"
REDUCE_REQUESTS = "reduce_requests"

VERDICT_ORDER = {"unsafe": 0, "review": 1, "safe": 2}

# Kinds whose purpose is to run everywhere rather than to serve requests.
# Utilization is the wrong lens for them entirely.
PER_NODE_KINDS = {"DaemonSet"}

# A workload whose usage is below this, but which something depends on, is the
# canonical failover shape: provisioned for a load it is not currently taking.
IDLE_UTILIZATION = 0.05

# Above this variability the workload's usage is bursty, so a sample showing
# it idle is not evidence that it is idle in general.
BURSTY_ENTROPY = 0.6

# Standard labels people use to mark deliberately-idle infrastructure.
INTENT_LABEL_HINTS = (
    "standby", "failover", "backup", "dr", "disaster-recovery", "canary",
    "fallback", "warm", "passive", "replica",
)


@dataclass
class Verdict:
    workload_key: str
    action: str
    verdict: str
    headline: str
    blocking: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_key": self.workload_key,
            "action": self.action,
            "verdict": self.verdict,
            "headline": self.headline,
            "blocking": self.blocking,
            "cautions": self.cautions,
            "evidence": self.evidence,
        }


def _intent_hint(workload: dict) -> str | None:
    """Whether the workload's own labels say it is meant to sit idle."""
    haystack = " ".join([
        workload.get("name") or "",
        *(f"{k}={v}" for k, v in (workload.get("labels") or {}).items()),
        *(f"{k}={v}" for k, v in (workload.get("ownership") or {}).items()),
    ]).lower()
    for hint in INTENT_LABEL_HINTS:
        if hint in haystack:
            return hint
    return None


def _has_disruption_budget(snapshot: dict, workload: dict) -> bool:
    """
    Whether someone deliberately protected this workload from disruption.

    A PDB is an explicit statement that losing these pods matters. Deleting
    something a colleague went out of their way to protect should never be
    automatic, whatever its CPU graph says.
    """
    pod_labels = workload.get("pod_labels") or {}
    for pdb in snapshot.get("disruption_budgets") or []:
        if pdb.get("namespace") != workload.get("namespace"):
            continue
        selector = pdb.get("selector") or {}
        if selector and all(pod_labels.get(k) == v for k, v in selector.items()):
            return True
    return False


def _service_exposed(snapshot: dict, workload: dict) -> list[str]:
    """Services whose selector resolves to this workload."""
    pod_labels = workload.get("pod_labels") or {}
    exposed = []
    for service in snapshot.get("services") or []:
        if service.get("namespace") != workload.get("namespace"):
            continue
        selector = service.get("selector") or {}
        if selector and all(pod_labels.get(k) == v for k, v in selector.items()):
            exposed.append(f"{service['namespace']}/{service['name']}")
    return sorted(exposed)


def assess(
    snapshot: dict,
    workload_key: str,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    action: str = DELETE,
    graph=None,
) -> Verdict:
    """
    Decide whether ``action`` on ``workload_key`` is safe to execute.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}
    workload = workloads.get(workload_key)

    if workload is None:
        return Verdict(
            workload_key=workload_key,
            action=action,
            verdict="review",
            headline="Workload not found in the snapshot.",
            cautions=[
                "The snapshot does not contain this workload, so nothing about "
                "it can be assessed. It may have been deleted already, or the "
                "agent may not cover its namespace."
            ],
        )

    graph = graph if graph is not None else build_dependency_graph(snapshot)
    radius = compute_blast_radius(snapshot, workload_key, cei_by_workload, graph=graph)
    node = cei_by_workload.get(workload_key) or {}

    blocking: list[str] = []
    cautions: list[str] = []
    removes_capacity = action in (DELETE, SCALE_TO_ZERO)

    # --- what depends on it ------------------------------------------------
    if removes_capacity:
        if radius.entry_points:
            names = ", ".join(e.split("/")[-1] for e in radius.entry_points)
            blocking.append(
                f"Reachable from ingress {names}. This serves external traffic; "
                "removing it is a user-visible outage regardless of its "
                "current utilization."
            )
        if radius.direct_dependents:
            shown = ", ".join(k.split("/")[-1] for k in radius.direct_dependents[:5])
            more = len(radius.direct_dependents) - 5
            blocking.append(
                f"{len(radius.direct_dependents)} workload(s) declare a "
                f"dependency on it: {shown}"
                + (f" and {more} more" if more > 0 else "")
                + ". They will fail when it goes away."
            )
        elif radius.total_affected:
            cautions.append(
                f"{radius.total_affected} workload(s) reach it indirectly. No "
                "direct reference was found, so confirm the chain before acting."
            )

    # --- idle-but-depended-on: the failover shape --------------------------
    cpu = workload.get("cpu_cores_used")
    requested = workload.get("cpu_cores_requested")
    utilization = (
        float(cpu) / float(requested)
        if cpu is not None and requested else None
    )

    if (
        removes_capacity
        and utilization is not None
        and utilization < IDLE_UTILIZATION
        and radius.total_affected > 0
    ):
        blocking.append(
            f"Idle ({utilization:.1%} of requested CPU) but "
            f"{radius.total_affected} workload(s) depend on it. Low utilization "
            "on something with dependents is the signature of a failover or "
            "standby path -- provisioned for load it is not currently taking. "
            "This is precisely the case utilization-based tooling gets wrong."
        )

    hint = _intent_hint(workload)
    if hint and removes_capacity:
        blocking.append(
            f"Named or labelled `{hint}`, which usually marks infrastructure "
            "that is idle on purpose. Confirm with the owner before removing it."
        )

    # --- deliberate protection ---------------------------------------------
    if removes_capacity and _has_disruption_budget(snapshot, workload):
        cautions.append(
            "A PodDisruptionBudget covers this workload. Somebody decided its "
            "availability mattered enough to protect it explicitly."
        )

    services = _service_exposed(snapshot, workload)
    if removes_capacity and services:
        cautions.append(
            f"Backs Service(s) {', '.join(services)}. Any client resolving "
            "those names by DNS rather than by an env var is invisible to the "
            "dependency graph."
        )

    if workload.get("kind") in PER_NODE_KINDS:
        cautions.append(
            f"This is a {workload.get('kind')}: it runs one pod per node by "
            "design. Per-pod utilization is the wrong measure for it, and it "
            "is usually cluster infrastructure rather than an application."
        )

    # --- variability --------------------------------------------------------
    entropy = node.get("entropy")
    if entropy is not None and entropy >= BURSTY_ENTROPY:
        cautions.append(
            f"Usage is highly variable (entropy {entropy:.2f}). A sample "
            "showing it idle is not evidence that it is idle in general -- "
            "check a window long enough to contain its peak."
        )

    if action == REDUCE_REQUESTS and radius.total_affected > 0 and entropy is not None:
        if entropy >= BURSTY_ENTROPY:
            blocking.append(
                "Bursty usage combined with dependents: trimming requests to "
                "recent peak removes the headroom that absorbs its spikes, and "
                "the workloads downstream feel it first."
            )

    if utilization is None and removes_capacity:
        cautions.append(
            "No usage measurements available, so the claim that this is idle "
            "cannot be checked here. Install metrics-server, or verify usage "
            "before acting."
        )

    # --- verdict -------------------------------------------------------------
    if blocking:
        verdict = "unsafe"
    elif cautions:
        verdict = "review"
    else:
        verdict = "safe"

    if verdict == "safe":
        headline = (
            f"Nothing declares a dependency on {workload.get('name')} and "
            "nothing protects it. Safe to proceed, subject to the limitation "
            "that undeclared clients are not visible without flow data."
        )
    elif verdict == "review":
        headline = (
            f"{workload.get('name')} needs a look before acting: "
            f"{len(cautions)} thing(s) to confirm."
        )
    else:
        headline = (
            f"Do not {action.replace('_', ' ')} {workload.get('name')}. "
            + blocking[0]
        )

    return Verdict(
        workload_key=workload_key,
        action=action,
        verdict=verdict,
        headline=headline,
        blocking=blocking,
        cautions=cautions,
        evidence={
            "dependents": radius.total_affected,
            "direct_dependents": radius.direct_dependents,
            "user_facing": bool(radius.entry_points),
            "entry_points": radius.entry_points,
            "cpu_utilization": round(utilization, 4) if utilization is not None else None,
            "entropy": entropy,
            "cei_score": node.get("cei_score"),
            "kind": workload.get("kind"),
            "services": services,
            "blast_radius_severity": radius.severity,
        },
    )


def review_cost_recommendations(
    snapshot: dict,
    cost_result: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Annotate an existing cost analysis with safety verdicts.

    Takes the output of ``cost.analyze_cluster_cost`` and attaches a verdict to
    every recommendation, then reports the saving twice: what the cost analysis
    claimed, and what is left once the unsafe recommendations are removed.

    Reporting both is the point. A number that quietly shrinks is not credible,
    and the gap between them is the argument for this entire module -- it is
    the money a utilization-only tool would have told someone to take, and the
    outage they would have bought with it.
    """
    graph = build_dependency_graph(snapshot)
    workloads = cost_result.get("workloads") or []

    reviewed = []
    safe_savings = 0.0
    blocked_savings = 0.0
    review_savings = 0.0

    for entry in workloads:
        key = entry.get("workload_key") or entry.get("key")
        if not key:
            continue
        wasted = float(entry.get("wasted_monthly_usd") or 0.0)
        if wasted <= 0 and entry.get("verdict") not in ("idle", "unused"):
            continue

        # cost.py recommends trimming requests; an idle verdict from any tool
        # is a deletion recommendation in practice.
        action = (
            SCALE_TO_ZERO
            if entry.get("verdict") in ("idle", "unused")
            else REDUCE_REQUESTS
        )
        verdict = assess(snapshot, key, cei_by_workload, action=action, graph=graph)

        if verdict.verdict == "safe":
            safe_savings += wasted
        elif verdict.verdict == "unsafe":
            blocked_savings += wasted
        else:
            review_savings += wasted

        reviewed.append({
            **entry,
            "safety": verdict.to_dict(),
        })

    reviewed.sort(key=lambda r: (
        VERDICT_ORDER.get(r["safety"]["verdict"], 9),
        -float(r.get("wasted_monthly_usd") or 0.0),
    ))

    claimed = safe_savings + review_savings + blocked_savings
    return {
        "summary": {
            "recommendations_reviewed": len(reviewed),
            "safe": sum(1 for r in reviewed if r["safety"]["verdict"] == "safe"),
            "needs_review": sum(1 for r in reviewed if r["safety"]["verdict"] == "review"),
            "unsafe": sum(1 for r in reviewed if r["safety"]["verdict"] == "unsafe"),
            "claimed_monthly_usd": round(claimed, 2),
            "safe_monthly_usd": round(safe_savings, 2),
            "needs_review_monthly_usd": round(review_savings, 2),
            "blocked_monthly_usd": round(blocked_savings, 2),
            "note": (
                f"${blocked_savings:,.2f}/month of the reported saving is "
                "attached to workloads that something depends on. Acting on "
                "those recommendations would reduce the bill and break "
                "callers that gave no warning."
                if blocked_savings > 0 else
                "No recommendation in this set would break a declared "
                "dependency."
            ),
        },
        "recommendations": reviewed,
    }
