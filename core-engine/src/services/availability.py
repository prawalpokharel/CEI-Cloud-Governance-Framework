"""
Effective availability under correlated failure.

The standard availability calculation multiplies independent component
numbers: three nines here, four nines there, redundancy doubles the nines.
Every term of it assumes failures are independent, and the dependency graph
is a catalogue of the ways they are not. A service at 99.95% behind a
database at 99.9% is not at 99.95%, and fourteen services sharing an identity
provider do not fail independently no matter how many replicas each runs.

This module computes what the naive arithmetic computes, then computes what
the graph implies, and reports both. The gap between them is the cost of the
correlation -- the availability the architecture actually buys versus the
availability the component SLAs imply.

## Method: Monte Carlo over the graph

Per trial: every node fails independently with its own probability (that part
of the naive model is kept -- component failures ARE roughly independent;
it is their *consequences* that correlate). Failure then propagates along
dependency edges: a workload is effectively down if it failed itself, or if
any dependency it relies on is down and the edge transmits. Edge transmission
uses the edge's confidence, which after chaos calibration is a measured
propagation rate rather than an assumption.

Sampled rather than solved in closed form because the graph has diamonds --
two paths from A to D through B and C are not independent events, and the
inclusion-exclusion over real topologies is exactly the arithmetic humans get
wrong. Sampling with a seeded RNG is exact enough (half-width reported) and
audits in one read.

## Inputs, honestly

Per-component availability priors default to conservative published figures
by category and can be overridden per workload. The output is therefore a
*model*, and it says so: its value is the comparison and the ranking (which
dependency costs the most nines), which are robust to the priors, not the
fourth decimal of the absolute number, which is not.
"""

from __future__ import annotations

import math
import random
from typing import Any

from .blast_radius import build_dependency_graph

# Default annual availability priors. Deliberately conservative and
# deliberately round: these are model inputs to be overridden with real SLO
# data, not measurements.
DEFAULT_WORKLOAD_AVAILABILITY = 0.999
CATEGORY_AVAILABILITY = {
    "identity": 0.9995,
    "dns": 0.9999,
    "database": 0.9995,
    "payments": 0.9995,
    "cache": 0.999,
    "storage": 0.9999,
    "messaging": 0.999,
    "email": 0.999,
    "observability": 0.999,
    "registry": 0.999,
    "unknown": 0.999,
}

DEFAULT_TRIALS = 20_000
# Fixed seed: two runs on the same cluster must give the same answer, or the
# number cannot be quoted anywhere.
DEFAULT_SEED = 20260817


def _nines(availability: float) -> float:
    """99.9 -> 3.0. The unit people actually reason in."""
    if availability >= 1.0:
        return float("inf")
    if availability <= 0.0:
        return 0.0
    return -math.log10(1.0 - availability)


def naive_availability(
    graph, priors: dict[str, float]
) -> dict[str, float]:
    """
    The standard serial calculation: a workload's availability is its own
    times the product of its transitive dependencies'.

    This is what an architecture review whiteboard produces. Computed here so
    the correlated result has the right thing to be compared against --
    including the naive model's own blindness to shared paths, which is the
    point of the comparison.
    """
    import networkx as nx

    result: dict[str, float] = {}
    workloads = [n for n, d in graph.nodes(data=True) if not d.get("is_external_root")]
    for node in workloads:
        value = priors.get(node, DEFAULT_WORKLOAD_AVAILABILITY)
        for dep in nx.descendants(graph, node):
            value *= priors.get(dep, DEFAULT_WORKLOAD_AVAILABILITY)
        result[node] = value
    return result


def simulate(
    snapshot: dict,
    *,
    external: dict | None = None,
    availability_overrides: dict[str, float] | None = None,
    trials: int = DEFAULT_TRIALS,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """
    Monte Carlo effective availability for every workload.

    ``external`` is the ExternalNode mapping from external_deps, attached to
    the graph when present so shared SaaS and managed services participate in
    the correlation -- which is where most of it lives.
    """
    from .external_deps import build_combined_graph

    availability_overrides = availability_overrides or {}
    external = external or {}

    graph = build_combined_graph(snapshot, external) if external else (
        build_dependency_graph(snapshot)
    )
    nodes = list(graph.nodes())
    workload_nodes = [n for n in nodes if graph.nodes[n].get("is_workload")]
    if not workload_nodes:
        return {"available": False, "reason": "No workloads in snapshot."}

    priors: dict[str, float] = {}
    for node in nodes:
        if node in availability_overrides:
            priors[node] = availability_overrides[node]
        elif graph.nodes[node].get("is_external"):
            category = graph.nodes[node].get("category") or "unknown"
            priors[node] = CATEGORY_AVAILABILITY.get(category, 0.999)
        else:
            priors[node] = DEFAULT_WORKLOAD_AVAILABILITY

    # Precompute adjacency once; the trial loop is the hot path.
    dependencies = {
        node: [
            (target, float(graph.edges[node, target].get("confidence", 1.0)))
            for target in graph.successors(node)
        ]
        for node in nodes
    }
    # Topological-ish order for propagation: process dependencies before
    # dependents where possible; cycles fall back to iteration.
    import networkx as nx
    try:
        order = list(reversed(list(nx.topological_sort(graph))))
        cyclic = False
    except nx.NetworkXUnfeasible:
        order = nodes
        cyclic = True

    rng = random.Random(seed)
    down_counts = {node: 0 for node in workload_nodes}
    self_failures = {node: 0 for node in workload_nodes}

    for _ in range(trials):
        failed = {
            node: rng.random() > priors[node] for node in nodes
        }
        effective = dict(failed)
        # One pass in dependency order settles acyclic graphs; cycles get a
        # bounded fixpoint.
        passes = 1 if not cyclic else 3
        for _ in range(passes):
            for node in order:
                if effective[node]:
                    continue
                for target, transmission in dependencies[node]:
                    if effective[target] and rng.random() < transmission:
                        effective[node] = True
                        break
        for node in workload_nodes:
            if effective[node]:
                down_counts[node] += 1
                if failed[node]:
                    self_failures[node] += 1

    naive = naive_availability(graph, priors)

    per_workload = []
    for node in workload_nodes:
        effective_availability = 1.0 - down_counts[node] / trials
        induced = down_counts[node] - self_failures[node]
        per_workload.append({
            "workload_key": node,
            "own_prior": priors[node],
            "naive": round(naive.get(node, priors[node]), 6),
            "effective": round(effective_availability, 6),
            "effective_nines": round(_nines(effective_availability), 2),
            # Share of observed downtime caused by dependencies rather than
            # the workload itself -- the correlation cost, per workload.
            "downtime_from_dependencies": (
                round(induced / down_counts[node], 4) if down_counts[node] else 0.0
            ),
        })
    per_workload.sort(key=lambda w: w["effective"])

    # The headline: availability of user-facing paths.
    entry_backed = [
        n for n in workload_nodes
        if any(
            graph.nodes[p].get("kind") == "Ingress"
            for p in graph.predecessors(n)
        )
    ]
    user_facing = [w for w in per_workload if w["workload_key"] in entry_backed]

    # Standard error half-width at the mean effective availability, so the
    # precision of the estimate travels with it.
    mean_effective = sum(w["effective"] for w in per_workload) / len(per_workload)
    half_width = 1.96 * math.sqrt(
        max(mean_effective * (1 - mean_effective), 1e-12) / trials
    )

    return {
        "available": True,
        "trials": trials,
        "seed": seed,
        "confidence_half_width": round(half_width, 6),
        "summary": {
            "worst_workload": per_workload[0]["workload_key"] if per_workload else None,
            "mean_effective": round(mean_effective, 6),
            "user_facing": user_facing,
            "note": (
                "A model, not a measurement: priors are conservative defaults "
                "unless overridden with real SLO data. The comparison and the "
                "ranking are robust to the priors; the absolute number is "
                "only as good as they are."
            ),
        },
        "per_workload": per_workload,
    }


def correlation_cost(result: dict) -> list[dict[str, Any]]:
    """
    Where the nines went: naive minus effective, worst first.

    The deliverable sentence is "your architecture's effective availability is
    X, not the Y your redundancy implies" -- this is X and Y per workload,
    with the gap in nines.
    """
    if not result.get("available"):
        return []
    rows = []
    for workload in result.get("per_workload") or []:
        gap = _nines(workload["naive"]) - _nines(workload["effective"])
        rows.append({
            "workload_key": workload["workload_key"],
            "naive": workload["naive"],
            "effective": workload["effective"],
            "nines_lost_to_correlation": round(max(0.0, gap), 2),
            "downtime_from_dependencies": workload["downtime_from_dependencies"],
        })
    rows.sort(key=lambda r: -r["nines_lost_to_correlation"])
    return rows
