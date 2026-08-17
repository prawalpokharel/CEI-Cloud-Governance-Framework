"""
Directed blast radius: what breaks if this workload changes.

Every other analysis in this product asks "how important is this thing".
This one asks the question an operator actually has in front of them at the
moment they are about to act: *if I touch this, what comes down with it.*

## Direction is the whole thing

Edges are emitted by the agent as ``source depends on target`` -- a workload
with ``CART_SERVICE_ADDR`` in its environment produces ``frontend ->
cartservice``. So if ``cartservice`` degrades, the affected set is everything
that can reach it by following edges forward: its **ancestors**.

The existing k-hop neighborhood in the pre-modification validator walks
successors *and* predecessors, which is correct for "what is near this" and
wrong for "what breaks if I change this". A workload's dependencies are not
harmed when it fails; its dependents are. Mixing the two inflates the blast
radius with things that are, in fact, fine -- and the number an operator is
asked to trust has to mean one thing.

## Confidence travels along the path

Edges carry a confidence: a Service selector match is exact (1.0), an
environment-variable reference is idiomatic but inferential (0.9). A dependent
three hops away through three inferred edges is a weaker claim than a direct
dependent through a declared one, and reporting them as equally affected would
be dishonest in the direction that costs trust fastest -- the false alarm.

Path confidence is the product of the edge confidences along it, and a node's
confidence is the best path to it. Best rather than worst: if there are two
ways the failure propagates, the workload is affected by the more certain one.

## What makes a blast radius severe

Not the count. Ten dependents in a batch namespace matter less than one
ingress-backed API. Three things are weighed:

* **Reach into user-facing paths.** If any Ingress can reach the changed
  workload, the failure is visible to somebody outside the cluster. This is
  the single strongest signal and the one operators react to.
* **Share of system criticality.** The fraction of total CEI mass sitting in
  the affected set, which is scale-free -- it means the same thing on a
  40-workload cluster and a 4000-workload one.
* **Absolute reach.** Retained because "31 workloads" is what a human
  remembers, even though it is the weakest of the three.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any, Iterable

import networkx as nx

# A change that reaches an Ingress-backed path is user-visible. Ingress
# vertices are synthesised by the agent as "<namespace>/Ingress/<name>" and are
# not workloads, so they are carried in the graph but never counted as
# affected workloads.
INGRESS_KIND = "Ingress"

# Confidence below which a dependent is reported but explicitly marked as a
# weak inference. Three chained env-var edges (0.9^3 = 0.729) still clears
# this; four (0.656) does not, which is the intended cut -- inference that has
# passed through four unverified hops is a lead, not a finding.
WEAK_PATH_CONFIDENCE = 0.7

SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "none": 4}

# Share of total scored criticality that makes a workload load-bearing.
LOAD_BEARING_CENTRALITY_FRACTION = 0.05

# Dependent count that makes a workload load-bearing regardless of scores.
#
# This floor is what keeps the definition working when CEI is unavailable --
# a fresh install, or entropy still accumulating. Without it every
# centrality-weighted test collapses to zero and nothing is ever considered
# central, which fails silently in the worst direction: a clean report.
LOAD_BEARING_MIN_DEPENDENTS = 3


@dataclass
class AffectedWorkload:
    key: str
    hops: int
    confidence: float
    cei_score: float | None = None
    classification: str | None = None
    via: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "hops": self.hops,
            "confidence": round(self.confidence, 3),
            "cei_score": self.cei_score,
            "classification": self.classification,
            "via": self.via,
            "weak_inference": self.confidence < WEAK_PATH_CONFIDENCE,
        }


@dataclass
class BlastRadius:
    workload_key: str
    exists: bool
    direct_dependents: list[str] = field(default_factory=list)
    affected: list[AffectedWorkload] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    centrality_fraction: float = 0.0
    workload_fraction: float = 0.0
    severity: str = "none"
    headline: str = ""

    @property
    def total_affected(self) -> int:
        return len(self.affected)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_key": self.workload_key,
            "exists": self.exists,
            "severity": self.severity,
            "headline": self.headline,
            "total_affected": self.total_affected,
            "direct_dependents": self.direct_dependents,
            "affected": [a.to_dict() for a in self.affected],
            "user_facing": bool(self.entry_points),
            "entry_points": self.entry_points,
            "centrality_fraction": round(self.centrality_fraction, 4),
            "workload_fraction": round(self.workload_fraction, 4),
        }


def build_dependency_graph(snapshot: dict) -> nx.DiGraph:
    """
    Build the directed graph, keeping Ingress vertices.

    ``live_cei`` drops Ingress vertices because the pipeline cannot score a
    thing with no telemetry. Here they are exactly what makes a blast radius
    legible: an operator reads "reaches the public API" far faster than they
    read a workload count, so the vertices that mark external entry are worth
    carrying even though they will never have a CEI score.
    """
    graph = nx.DiGraph()

    for workload in snapshot.get("workloads") or []:
        key = workload.get("key")
        if not key:
            continue
        graph.add_node(
            key,
            kind=workload.get("kind"),
            namespace=workload.get("namespace"),
            name=workload.get("name"),
            is_workload=True,
        )

    for edge in snapshot.get("edges") or []:
        source, target = edge.get("source"), edge.get("target")
        if not source or not target:
            continue
        # Ingress sources are not in the workload list; add them as
        # non-workload vertices so paths through them are traversable.
        if source not in graph:
            if f"/{INGRESS_KIND}/" not in source:
                continue
            graph.add_node(source, kind=INGRESS_KIND, is_workload=False)
        if target not in graph:
            continue
        graph.add_edge(
            source,
            target,
            confidence=float(edge.get("confidence", 1.0)),
            source_kind=edge.get("source_kind", "dependency"),
        )

    return graph


def _propagate(graph: nx.DiGraph, target: str) -> dict[str, tuple[int, float, str]]:
    """
    Find every vertex that can reach ``target``, with its best path confidence.

    Best-first over the reversed graph. Confidence multiplies along a path, so
    maximising the product is minimising the sum of ``-log(confidence)`` -- an
    ordinary shortest path, which means the first time a vertex is settled it
    has its best confidence and never needs revisiting.

    Hop count is carried alongside rather than optimised: it is reported for
    human context ("two hops upstream"), and the hop count of the most
    confident path is more useful than the shortest path's, which may run
    through edges the system is much less sure of.

    Returns ``{key: (hops, confidence, via)}`` where ``via`` names the edge
    kind of the first step away from the target.
    """
    best: dict[str, tuple[int, float, str]] = {}
    # (negated confidence for a min-heap, tie-break on hops, key, ...)
    queue: list[tuple[float, int, str, str]] = [(-1.0, 0, target, "")]

    while queue:
        neg_confidence, hops, node, via = heapq.heappop(queue)
        confidence = -neg_confidence

        if node in best:
            continue
        if node != target:
            best[node] = (hops, confidence, via)

        for dependent in graph.predecessors(node):
            if dependent in best or dependent == target:
                continue
            edge = graph.edges[dependent, node]
            step_via = via or edge.get("source_kind", "dependency")
            heapq.heappush(
                queue,
                (
                    -(confidence * float(edge.get("confidence", 1.0))),
                    hops + 1,
                    dependent,
                    step_via,
                ),
            )

    return best


def _classify(
    *,
    user_facing: bool,
    centrality_fraction: float,
    total_affected: int,
    target_cei: float | None,
) -> str:
    """
    Turn the three signals into one word an operator can act on.

    Thresholds are deliberately not a formula. A blended score would let a
    large workload count wash out the user-facing signal, and "does a customer
    see this" is not a thing that should be averaged away.
    """
    if total_affected == 0:
        # An entrypoint with no dependents is the API gateway case: nothing in
        # the cluster breaks, and every user is affected. Counting dependents
        # alone calls this harmless, which is exactly backwards -- it is the
        # one workload whose failure the customer notices first.
        return "high" if user_facing else "none"

    if user_facing and (centrality_fraction >= 0.15 or total_affected >= 5):
        return "critical"
    if centrality_fraction >= 0.30 or total_affected >= 15:
        return "critical"
    if user_facing or centrality_fraction >= 0.10 or total_affected >= 5:
        return "high"
    if (target_cei or 0.0) >= 0.65 or total_affected >= 2:
        return "moderate"
    return "low"


def _headline(radius: BlastRadius, graph: nx.DiGraph) -> str:
    if not radius.exists:
        return f"{radius.workload_key} is not in the dependency graph."
    if not radius.affected:
        if radius.entry_points:
            names = ", ".join(e.split("/")[-1] for e in radius.entry_points[:2])
            return (
                f"No workload depends on {radius.workload_key}, but it serves "
                f"users directly through ingress {names}. A failure here is "
                "invisible to the dependency graph and immediately visible to "
                "customers."
            )
        return (
            f"Nothing depends on {radius.workload_key}. No workload in the "
            "cluster is known to break if it changes."
        )

    count = radius.total_affected
    subject = "1 workload depends" if count == 1 else f"{count} workloads depend"
    parts = [f"{subject} on {radius.workload_key}"]

    if radius.entry_points:
        names = [e.split("/")[-1] for e in radius.entry_points[:2]]
        suffix = "" if len(radius.entry_points) <= 2 else f" +{len(radius.entry_points) - 2} more"
        parts.append(f"reachable from ingress {', '.join(names)}{suffix}")

    if radius.centrality_fraction >= 0.05:
        parts.append(f"{radius.centrality_fraction:.0%} of system criticality")

    return "; ".join(parts) + "."


def compute_blast_radius(
    snapshot: dict,
    workload_key: str,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    graph: nx.DiGraph | None = None,
) -> BlastRadius:
    """
    Compute what is affected if ``workload_key`` degrades or changes.

    ``graph`` may be supplied to avoid rebuilding it when scoring many
    workloads against the same snapshot.
    """
    cei_by_workload = cei_by_workload or {}
    graph = graph if graph is not None else build_dependency_graph(snapshot)

    if workload_key not in graph:
        return BlastRadius(
            workload_key=workload_key,
            exists=False,
            headline=f"{workload_key} is not in the dependency graph.",
        )

    reached = _propagate(graph, workload_key)

    affected: list[AffectedWorkload] = []
    entry_points: list[str] = []
    for key, (hops, confidence, via) in reached.items():
        if not graph.nodes[key].get("is_workload", False):
            if graph.nodes[key].get("kind") == INGRESS_KIND:
                entry_points.append(key)
            continue
        cei = cei_by_workload.get(key) or {}
        affected.append(AffectedWorkload(
            key=key,
            hops=hops,
            confidence=confidence,
            cei_score=cei.get("cei_score"),
            classification=cei.get("classification"),
            via=via or None,
        ))

    # Most certain first, then nearest, then by criticality. Confidence leads
    # because a reader scanning the top of the list should be seeing the
    # claims the system is most sure of.
    affected.sort(key=lambda a: (-a.confidence, a.hops, -(a.cei_score or 0.0), a.key))
    entry_points.sort()

    total_centrality = sum(
        (data.get("cei_score") or 0.0) for data in cei_by_workload.values()
    )
    affected_centrality = sum((a.cei_score or 0.0) for a in affected)
    centrality_fraction = (
        affected_centrality / total_centrality if total_centrality > 0 else 0.0
    )

    workload_total = sum(
        1 for _, d in graph.nodes(data=True) if d.get("is_workload")
    )
    workload_fraction = (
        len(affected) / (workload_total - 1) if workload_total > 1 else 0.0
    )

    radius = BlastRadius(
        workload_key=workload_key,
        exists=True,
        direct_dependents=sorted(
            key for key in graph.predecessors(workload_key)
            if graph.nodes[key].get("is_workload")
        ),
        affected=affected,
        entry_points=entry_points,
        centrality_fraction=centrality_fraction,
        workload_fraction=workload_fraction,
    )
    radius.severity = _classify(
        user_facing=bool(entry_points),
        centrality_fraction=centrality_fraction,
        total_affected=len(affected),
        target_cei=(cei_by_workload.get(workload_key) or {}).get("cei_score"),
    )
    radius.headline = _headline(radius, graph)
    return radius


def is_load_bearing(radius: BlastRadius) -> bool:
    """
    Whether enough depends on this workload for a latent weakness to matter.

    One definition, used by every analysis that needs to separate "worth
    reporting" from "true but not worth anyone's morning". It lived in two
    modules before this and they had already drifted -- one gated on scored
    criticality alone, so on a cluster with no CEI data yet it silently
    considered nothing central and reported all clear.

    Any one of four conditions qualifies, because each covers a case the
    others miss: ingress reach catches the small service behind the front
    door, criticality share is scale-free, the severity grade folds in the
    combined judgement, and the raw dependent count keeps working when there
    are no scores at all.
    """
    return (
        bool(radius.entry_points)
        or radius.severity in ("critical", "high")
        or radius.centrality_fraction >= LOAD_BEARING_CENTRALITY_FRACTION
        or radius.total_affected >= LOAD_BEARING_MIN_DEPENDENTS
    )


def compute_many(
    snapshot: dict,
    workload_keys: Iterable[str],
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, BlastRadius]:
    """Blast radius for several workloads, sharing one graph build."""
    graph = build_dependency_graph(snapshot)
    return {
        key: compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        for key in workload_keys
    }


def rank_by_blast_radius(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    The workloads whose failure costs the most, worst first.

    Distinct from ranking by CEI: CEI blends criticality, variability and
    governance risk into a judgement about the workload itself, while this
    ranks purely by what it takes down with it. A stable, well-governed,
    low-CEI workload that half the cluster calls belongs at the top of this
    list and will not be at the top of that one.
    """
    graph = build_dependency_graph(snapshot)
    keys = [k for k, d in graph.nodes(data=True) if d.get("is_workload")]

    results = [
        compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        for key in keys
    ]
    results.sort(
        key=lambda r: (
            SEVERITY_ORDER.get(r.severity, 9),
            -r.centrality_fraction,
            -r.total_affected,
            r.workload_key,
        )
    )
    return [r.to_dict() for r in results[:limit] if r.total_affected > 0]
