"""
Prescriptive resilience optimization: which intervention buys the most
risk reduction per dollar.

Everything before this module *measures*. The DCI says how concentrated the
cluster's fate is, the Monte Carlo says what the architecture's effective
availability is, drift says when either moved. The question an owner actually
has is the next one: *what should I do about it, and is it worth the money?*

This module answers it by putting risk and cost in the same currency.

## Risk in dollars

Expected user-facing downtime, priced. The availability model already
produces effective availability per workload under correlated failure;
multiply the user-facing shortfall by hours-per-year and by an
operator-supplied cost of downtime, and structural risk becomes an annual
dollar figure that can sit in the same column as a cloud bill. The default
rate is deliberately conservative and loudly labelled -- the *ranking* of
interventions is robust to it, the absolute figures are not.

This is also the answer to the cost-optimization-creates-fragility problem.
A FinOps tool reports "removing 30 idle replicas saves $70k/year" and stops.
This module prices what those replicas were buying: if the added expected
downtime costs more than $70k, the optimization was a loss wearing a savings
report. Every removal candidate carries both numbers and a NET.

## The candidate moves

Each intervention is a deterministic transform of the model -- priors, graph,
or both -- so its effect is computable before anyone acts:

* **add_replica**       -- lowers a workload's own-failure prior (partial
                           correlation model; replicas are not independent).
* **remove_replicas**   -- the FinOps move, priced honestly in both columns.
* **split_dependency**  -- a second instance of a hub, dependents divided;
                           attacks concentration itself.
* **diversify_external**-- a second provider for a shared external
                           dependency; modelled as independent failover.
* **do_nothing**        -- always a candidate. A framework that cannot
                           recommend inaction will invent work.

Evaluation uses common random numbers: every candidate re-runs the Monte
Carlo with the *same seed*, so candidates face the same sampled failures and
the deltas between them are differences in architecture, not in luck.

## What the numbers are

A model, stated as one. Priors are conservative defaults unless overridden
with measured SLOs; intervention costs come from the cluster's own cost
allocation where they can (replicas of a priced workload) and are marked
``pricing_required`` where they cannot (a second identity provider's contract
price is not knowable from a cluster snapshot). The deliverable is the
*ranking* and the *reasoning*, both of which survive prior error far better
than the fourth decimal does.
"""

from __future__ import annotations

import copy
from typing import Any

from . import availability, cost as cost_service, recovery
from .blast_radius import build_dependency_graph, compute_blast_radius, is_load_bearing
from .external_deps import (
    CATEGORY_WEIGHT,
    build_external_nodes,
    dependency_concentration_index,
)
from .graph_simulation import concentration, structural_centrality
from .safe_to_delete import assess as safety_assess

HOURS_PER_YEAR = 8760.0

# Conservative default; every serious deployment should override it. Chosen
# low so the framework under-claims: prescriptions that clear a low bar
# survive a higher one.
DEFAULT_DOWNTIME_COST_PER_HOUR = 5_000.0

# Own-failure model per replica count. Replicas are NOT independent -- they
# share the deploy pipeline, the config, and often the node pool -- so a
# correlated share of the failure probability does not divide away with
# replica count. c is that share.
BASE_UNAVAILABILITY = 0.001   # a single replica is 99.9% on its own
CORRELATED_SHARE = 0.3        # fraction of failures that hit all replicas at once

# Evaluation budget. Common random numbers make small trial counts usable:
# candidates are compared against the same sampled failures, so the deltas
# are architecture, not sampling noise.
DEFAULT_TRIALS = 6_000
MAX_CANDIDATES = 12


def replica_availability(replicas: int) -> float:
    """
    Own availability as a function of replica count, with partial correlation.

    unavailability = c*u + (1-c)*u^r: the correlated share never divides
    away, so a second replica is a large win and a fourth is nearly none --
    which matches operational reality and keeps the optimizer from
    recommending replicas forever.
    """
    r = max(1, int(replicas or 1))
    u = CORRELATED_SHARE * BASE_UNAVAILABILITY + (
        (1 - CORRELATED_SHARE) * (BASE_UNAVAILABILITY ** r)
    )
    return 1.0 - u


def _replica_priors(snapshot: dict) -> dict[str, float]:
    return {
        w["key"]: replica_availability(w.get("replicas_desired") or 1)
        for w in (snapshot.get("workloads") or [])
        if w.get("key")
    }


def risk_dollars(
    simulation: dict,
    *,
    downtime_cost_per_hour: float,
) -> dict[str, Any]:
    """
    Annualised structural risk in dollars, from a simulation result.

    User-facing workloads are the priced surface: their downtime is what a
    customer experiences. When nothing is ingress-backed, the mean across all
    workloads is used and the result says so -- pricing internal-only
    downtime as if it were customer-facing would overstate risk exactly where
    the model is least sure.
    """
    if not simulation.get("available"):
        return {"annual_usd": 0.0, "priced_workloads": 0, "basis": "unavailable"}

    user_facing = (simulation.get("summary") or {}).get("user_facing") or []
    rows = user_facing or simulation.get("per_workload") or []
    basis = "user_facing" if user_facing else "all_workloads_mean"

    if basis == "all_workloads_mean":
        mean_unavail = (
            sum(1 - r["effective"] for r in rows) / len(rows) if rows else 0.0
        )
        annual = mean_unavail * HOURS_PER_YEAR * downtime_cost_per_hour
    else:
        annual = sum(
            (1 - r["effective"]) * HOURS_PER_YEAR * downtime_cost_per_hour
            for r in rows
        )

    return {
        "annual_usd": round(annual, 2),
        "priced_workloads": len(rows),
        "basis": basis,
        "downtime_cost_per_hour": downtime_cost_per_hour,
    }


def structural_health(snapshot: dict, external: dict) -> dict[str, Any]:
    """
    The problem-12 answer: one structural read while every metric is green.

    Composed from the validated parts rather than invented fresh: DCI over
    the combined graph, internal concentration, the largest single blast
    radius, and the top recovery amplification. Subscores are reported --
    a single opaque number would be astrology with extra steps.
    """
    graph = build_dependency_graph(snapshot)
    scores = structural_centrality(graph)
    dci = dependency_concentration_index(snapshot, external)

    # Ranked by affected count first: centrality_fraction is a share of CEI
    # mass and is uniformly zero when no CEI scores are supplied, which would
    # leave "largest blast radius" empty on exactly the clusters this summary
    # is for.
    worst = (0, 0.0, None)
    for key in scores:
        radius = compute_blast_radius(snapshot, key, {}, graph=graph)
        candidate = (radius.total_affected, radius.centrality_fraction, key)
        if candidate[:2] > worst[:2]:
            worst = candidate
    worst_key, worst_fraction = worst[2], worst[1]
    worst_affected = worst[0]

    amplification = recovery.analyze(snapshot).get("amplification_ranking") or []
    top_amp = amplification[0] if amplification else None

    return {
        "dci": dci["dci"],
        "dci_scope": dci["scope"],
        "internal_concentration": round(concentration(scores), 4) if scores else 0.0,
        "largest_blast_radius": {
            "workload_key": worst_key,
            "affected_workloads": worst_affected,
            "centrality_fraction": round(worst_fraction, 4),
        },
        "top_recovery_amplification": top_amp,
        "note": (
            "Structural state, independent of utilisation and error rates. "
            "Every conventional metric can be green while these numbers say a "
            "small disturbance would be a large outage."
        ),
    }


# --------------------------------------------------------------------------
# Candidate interventions
# --------------------------------------------------------------------------


def _monthly_cost_by_workload(snapshot: dict) -> dict[str, float]:
    try:
        result = cost_service.analyze_cluster_cost(snapshot)
    except Exception:
        return {}
    return {
        row.get("workload_key"): float(row.get("monthly_usd") or 0.0)
        for row in (result.get("workloads") or [])
        if row.get("workload_key")
    }


def generate_candidates(
    snapshot: dict,
    external: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> list[dict[str, Any]]:
    """
    Enumerate the moves worth evaluating for this cluster.

    Bounded and targeted rather than exhaustive: candidates come from the
    findings (low-replica load-bearers, high fan-in hubs, heavyweight shared
    externals, idle capacity), because "evaluate every possible change" is a
    search problem and this is a prescription problem.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}
    graph = build_dependency_graph(snapshot)
    monthly = _monthly_cost_by_workload(snapshot)

    candidates: list[dict[str, Any]] = [{
        "id": "do_nothing",
        "kind": "do_nothing",
        "target": None,
        "description": (
            "Baseline. Always a candidate: when nothing clears the bar, the "
            "honest prescription is this one."
        ),
        "annual_cost_delta_usd": 0.0,
    }]

    for key, workload in workloads.items():
        replicas = workload.get("replicas_desired") or 1
        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        load_bearing = is_load_bearing(radius)
        per_replica_month = (monthly.get(key) or 0.0) / max(1, replicas)

        # Add a replica where it still buys something.
        if load_bearing and replicas < 3:
            candidates.append({
                "id": f"add_replica:{key}",
                "kind": "add_replica",
                "target": key,
                "description": (
                    f"Add one replica to {key.split('/')[-1]} "
                    f"({replicas} -> {replicas + 1})."
                ),
                "annual_cost_delta_usd": round(per_replica_month * 12, 2) or None,
                "replicas_after": replicas + 1,
            })

        # The FinOps move, evaluated honestly: only where the safety gate
        # already clears it, and priced in both columns.
        if replicas > 2:
            verdict = safety_assess(
                snapshot, key, cei_by_workload, action="reduce_requests"
            )
            utilization = verdict.evidence.get("cpu_utilization")
            if utilization is not None and utilization < 0.3:
                removable = replicas - 2
                candidates.append({
                    "id": f"remove_replicas:{key}",
                    "kind": "remove_replicas",
                    "target": key,
                    "description": (
                        f"Remove {removable} idle replica(s) from "
                        f"{key.split('/')[-1]} ({replicas} -> 2). Utilisation "
                        f"{utilization:.0%}."
                    ),
                    "annual_cost_delta_usd": round(-per_replica_month * removable * 12, 2),
                    "replicas_after": 2,
                })

        # Split a hub: the move that attacks concentration itself.
        direct = [
            d for d in graph.predecessors(key)
            if graph.nodes[d].get("is_workload")
        ]
        if len(direct) >= 4:
            candidates.append({
                "id": f"split_dependency:{key}",
                "kind": "split_dependency",
                "target": key,
                "description": (
                    f"Deploy a second instance of {key.split('/')[-1]} and "
                    f"divide its {len(direct)} dependents between the two."
                ),
                "annual_cost_delta_usd": round((monthly.get(key) or 0.0) * 12, 2) or None,
                "direct_dependents": len(direct),
            })

    # Diversify the heavyweight shared externals.
    for node in (external or {}).values():
        if node.weight >= 0.8 and len(node.dependents) >= 3:
            candidates.append({
                "id": f"diversify_external:{node.endpoint}",
                "kind": "diversify_external",
                "target": node.key,
                "description": (
                    f"Add an independent second provider for {node.endpoint} "
                    f"({node.category}, {len(node.dependents)} dependents) "
                    "with failover."
                ),
                # A second identity provider's contract is not knowable from
                # a cluster snapshot. Reported without a cost rather than
                # with an invented one.
                "annual_cost_delta_usd": None,
                "category": node.category,
            })

    return candidates[:MAX_CANDIDATES]


def _apply(
    candidate: dict,
    snapshot: dict,
    priors: dict[str, float],
) -> tuple[dict, dict[str, float]]:
    """
    The model after the candidate is applied: (snapshot', priors').

    Pure transforms. The input snapshot is never mutated -- candidates are
    evaluated against copies, and the baseline stays the baseline.
    """
    kind = candidate["kind"]
    target = candidate.get("target")

    if kind == "do_nothing":
        return snapshot, priors

    if kind in ("add_replica", "remove_replicas"):
        new_priors = dict(priors)
        new_priors[target] = replica_availability(candidate["replicas_after"])
        return snapshot, new_priors

    if kind == "diversify_external":
        # Independent failover: both providers must be down for the
        # dependency to be down.
        new_priors = dict(priors)
        base = priors.get(target)
        if base is None:
            category = candidate.get("category") or "unknown"
            base = availability.CATEGORY_AVAILABILITY.get(category, 0.999)
        new_priors[target] = 1.0 - (1.0 - base) ** 2
        return snapshot, new_priors

    if kind == "split_dependency":
        modified = copy.deepcopy(snapshot)
        original = next(
            w for w in modified["workloads"] if w["key"] == target
        )
        twin = copy.deepcopy(original)
        twin_key = f"{target}-b"
        twin["key"] = twin_key
        twin["name"] = f"{original.get('name')}-b"
        modified["workloads"].append(twin)

        # Alternate dependents between the instances; the twin inherits the
        # original's outbound dependencies.
        moved = 0
        new_edges = []
        for edge in modified.get("edges") or []:
            edge = dict(edge)
            if edge.get("target") == target:
                if moved % 2 == 1:
                    edge["target"] = twin_key
                moved += 1
            if edge.get("source") == target:
                new_edges.append({**edge, "source": twin_key})
            new_edges.append(edge)
        modified["edges"] = new_edges

        new_priors = dict(priors)
        new_priors[twin_key] = priors.get(target, replica_availability(2))
        return modified, new_priors

    return snapshot, priors


def prescribe(
    snapshot: dict,
    egress_summary: dict | None = None,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    downtime_cost_per_hour: float = DEFAULT_DOWNTIME_COST_PER_HOUR,
    trials: int = DEFAULT_TRIALS,
) -> dict[str, Any]:
    """
    Rank interventions by risk reduced per dollar spent.
    """
    external = (
        build_external_nodes(snapshot, egress_summary)
        if egress_summary and egress_summary.get("available")
        else {}
    )
    priors = _replica_priors(snapshot)

    baseline_sim = availability.simulate(
        snapshot, external=external,
        availability_overrides=priors, trials=trials,
    )
    if not baseline_sim.get("available"):
        return {
            "available": False,
            "reason": baseline_sim.get("reason", "No workloads to optimise."),
            "prescriptions": [],
        }
    baseline_risk = risk_dollars(
        baseline_sim, downtime_cost_per_hour=downtime_cost_per_hour
    )

    candidates = generate_candidates(snapshot, external, cei_by_workload)
    prescriptions = []

    for candidate in candidates:
        modified_snapshot, modified_priors = _apply(candidate, snapshot, priors)
        # Same seed for every candidate: common random numbers, so deltas are
        # architecture rather than luck.
        simulation = availability.simulate(
            modified_snapshot, external=external,
            availability_overrides=modified_priors, trials=trials,
        )
        candidate_risk = risk_dollars(
            simulation, downtime_cost_per_hour=downtime_cost_per_hour
        )

        risk_reduction = baseline_risk["annual_usd"] - candidate_risk["annual_usd"]
        cost_delta = candidate.get("annual_cost_delta_usd")

        entry = {
            **candidate,
            "annual_risk_before_usd": baseline_risk["annual_usd"],
            "annual_risk_after_usd": candidate_risk["annual_usd"],
            "annual_risk_reduction_usd": round(risk_reduction, 2),
        }
        if candidate["kind"] == "do_nothing":
            entry["net_annual_benefit_usd"] = 0.0
            entry["verdict"] = "baseline"
        elif cost_delta is None:
            entry["net_annual_benefit_usd"] = None
            entry["verdict"] = "pricing_required"
            entry["note"] = (
                "Risk reduction is computed; the intervention's price is not "
                "knowable from a cluster snapshot. Supply it to complete the "
                "comparison."
            )
        else:
            net = risk_reduction - cost_delta
            entry["net_annual_benefit_usd"] = round(net, 2)
            entry["verdict"] = "recommended" if net > 0 else "not_worth_it"
            if cost_delta < 0 and risk_reduction < 0:
                # The problem-8 case, named: savings bought with fragility.
                entry["verdict"] = (
                    "recommended" if net > 0 else "savings_cost_more_than_they_save"
                )
        prescriptions.append(entry)

    # Recommended first by net benefit; pricing-required by raw risk
    # reduction; the rest last.
    def sort_key(entry):
        order = {
            "recommended": 0, "pricing_required": 1, "baseline": 2,
            "not_worth_it": 3, "savings_cost_more_than_they_save": 3,
        }
        return (
            order.get(entry["verdict"], 4),
            -(entry["net_annual_benefit_usd"]
              if entry["net_annual_benefit_usd"] is not None
              else entry["annual_risk_reduction_usd"]),
        )

    prescriptions.sort(key=sort_key)

    return {
        "available": True,
        "structural_health": structural_health(snapshot, external),
        "baseline_risk": baseline_risk,
        "assumptions": {
            "downtime_cost_per_hour": downtime_cost_per_hour,
            "downtime_cost_note": (
                "Deliberately conservative default; override with your real "
                "cost of downtime. The ranking is robust to this number, the "
                "absolute figures are not."
            ),
            "replica_model": (
                f"own availability = 1 - ({CORRELATED_SHARE}*u + "
                f"(1-{CORRELATED_SHARE})*u^replicas), u={BASE_UNAVAILABILITY}. "
                "The correlated share never divides away, so added replicas "
                "have diminishing returns."
            ),
            "trials": trials,
            "common_random_numbers": True,
        },
        "prescriptions": prescriptions,
    }
