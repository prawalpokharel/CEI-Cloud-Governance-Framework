"""
The graph a pull request implies, scored before it exists.

The blast-radius check in `pr_review` answers "what does this change reach",
which it computes against the graph that exists **now**. That is the right
question for an edit to something already running, and it is blind to the
change that matters most architecturally: a pull request that adds a service,
or adds a dependency, alters the topology itself. There is nothing in the
current graph to look up, so the current check reports "not found in cluster"
and moves on.

This module builds the graph the merge would produce, scores it, and reports
what changed structurally.

## Structural centrality, not the full CEI

CEI blends centrality, entropy, and governance risk. Only the first is
knowable here. Entropy is measured over a workload's utilization history, and
a service that does not exist yet has none -- estimating it would be inventing
the same third of the score that `live_cei` deliberately refuses to invent.

So this reports the **centrality term** on both graphs and the delta between
them. That is the honest quantity, and it is also the one an architecture
review is actually about: whether the change concentrates the system.

## Concentration

A single node's centrality rising is not automatically bad -- an API gateway
is supposed to be central. What matters is whether the *distribution* is
getting more concentrated: whether more of the system's structural importance
is collecting in fewer places.

Measured with a normalised Herfindahl-Hirschman index over the centrality
distribution: 0 when every workload is equally important, 1 when one workload
holds all of it. Chosen over "look at the top node" because it responds to the
whole shape, and over variance because it is scale-free -- comparable between
a 40-workload cluster and a 4000-workload one, and between the before and
after graphs even when the after graph has more nodes.

## Environment values never leave this process

Edges are inferred from environment variables naming services, which means
reading env values out of the pull request's manifests. Those values routinely
contain credentials. They are parsed in memory, reduced to service references,
and discarded -- exactly the contract `redact.py` enforces in the agent. No
value reaches a finding, a comment, or a response.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import networkx as nx

from . import manifest_diff

# Damping factor for PageRank. 0.85 is the conventional value and matches the
# pipeline's centrality calculation, so the numbers here are comparable with
# the ones reported elsewhere in the product.
PAGERANK_DAMPING = 0.85

# A concentration rise beyond this is reported as a structural event rather
# than noise. Calibrated so that adding an ordinary leaf service does not fire
# while introducing a new shared dependency does.
CONCENTRATION_DELTA_THRESHOLD = 0.10

# Same idea for a single workload's share of structural importance.
CENTRALITY_DELTA_THRESHOLD = 0.25

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# An in-cluster address as it appears in a manifest: `cartservice:7070`,
# `http://api.prod.svc.cluster.local:8080`. Deliberately the same shape the
# agent matches, so the projected graph is built the way the observed one is.
_ADDRESS = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)"
    r"(?:\.(?P<namespace>[a-z0-9](?:[-a-z0-9]*[a-z0-9])?))?"
    r"(?:\.svc(?:\.cluster\.local)?)?"
    r"(?::(?P<port>\d{1,5}))?"
    r"(?:/[^\s]*)?$",
    re.IGNORECASE,
)

_IGNORED_HOSTS = {
    "localhost", "127", "0", "kubernetes", "kubernetes.default",
    "metadata", "169", "true", "false", "null", "none",
}


@dataclass
class StructuralFinding:
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


def concentration(scores: dict[str, float]) -> float:
    """
    Normalised HHI over a centrality distribution.

    0 = every workload equally important; 1 = one workload holds everything.
    Normalisation by ``1/n`` is what makes before and after comparable when
    the pull request changes the node count -- a raw HHI falls automatically
    as n grows, which would report every added service as a structural
    improvement.
    """
    values = [v for v in scores.values() if v > 0]
    n = len(values)
    if n <= 1:
        return 1.0 if n == 1 else 0.0
    total = sum(values)
    if total <= 0:
        return 0.0
    hhi = sum((v / total) ** 2 for v in values)
    return max(0.0, min(1.0, (hhi - 1.0 / n) / (1.0 - 1.0 / n)))


def dependent_fraction(graph: nx.DiGraph) -> dict[str, float]:
    """
    Share of the cluster that depends on each workload, directly or not.

    Reported alongside PageRank because the two answer different questions and
    only this one is comparable across graphs of different size.

    PageRank is a *share* of a fixed total: it sums to 1 across the graph, so
    adding any node dilutes every existing score. A service gaining a fourth
    caller in a five-node cluster can score lower than it did with three
    callers in a four-node cluster, which is arithmetically correct and reads
    as "this became less important" -- the opposite of what happened. That is
    fine for measuring concentration, where the normalisation is the point,
    and wrong for "did this workload become more central".

    Reachability has no such problem: three of four workloads depend on it
    before, four of five after, so the number goes up because the fact went up.
    """
    workloads = [n for n, d in graph.nodes(data=True) if d.get("is_workload")]
    if len(workloads) <= 1:
        return {n: 0.0 for n in workloads}
    subgraph = graph.subgraph(workloads)
    denominator = len(workloads) - 1
    return {
        node: len(nx.ancestors(subgraph, node)) / denominator
        for node in workloads
    }


def structural_centrality(graph: nx.DiGraph) -> dict[str, float]:
    """
    Centrality emphasising dependents: what breaks if this fails.

    PageRank over the dependency graph. Edges run source -> target meaning
    "source depends on target", so rank flows toward the things depended upon
    and a workload half the cluster calls scores highly -- which is the
    reading `live_cei` documents as `blast_radius` mode and the one the
    product's claim rests on.
    """
    workloads = [n for n, d in graph.nodes(data=True) if d.get("is_workload")]
    if not workloads:
        return {}
    subgraph = graph.subgraph(workloads)
    if subgraph.number_of_edges() == 0:
        # No edges: every workload is equally (un)important. Uniform rather
        # than zero, so concentration reads 0 instead of being undefined.
        return {n: 1.0 / len(workloads) for n in workloads}
    try:
        return nx.pagerank(subgraph, alpha=PAGERANK_DAMPING, weight="confidence")
    except nx.PowerIterationFailedConvergence:
        # Degenerate topologies (perfect cycles) can fail to converge. Degree
        # centrality is a weaker answer than none.
        degrees = dict(subgraph.in_degree())
        total = sum(degrees.values()) or 1
        return {n: d / total for n, d in degrees.items()}


def _service_references(text_values: list[str], known_services: set[str]) -> set[str]:
    """
    Parse env values into referenced service names.

    Only values resolving to a service the projected cluster actually contains
    are returned. A credential that happens to look like a hostname cannot
    produce an edge unless a Service by that exact name exists, which is the
    same guard the agent applies -- and the reason no value needs to be
    retained afterwards.
    """
    found: set[str] = set()
    for value in text_values:
        if not value or len(value) > 512:
            continue
        for token in re.split(r"[,\s;|]+", str(value).strip()):
            if not token or len(token) > 253:
                continue
            match = _ADDRESS.match(token)
            if not match:
                continue
            host = (match.group("host") or "").lower()
            if not host or host in _IGNORED_HOSTS or host.isdigit():
                continue
            if host in known_services:
                found.add(host)
    return found


def _workload_from_manifest(obj: dict, namespace: str) -> dict | None:
    """Reduce a Deployment/StatefulSet/DaemonSet manifest to a workload record."""
    kind = obj.get("kind")
    metadata = obj.get("metadata") or {}
    name = metadata.get("name")
    if kind not in manifest_diff.WORKLOAD_KINDS or not name:
        return None

    spec = obj.get("spec") or {}
    template = spec.get("template") or {}
    pod_spec = template.get("spec") or {}
    containers = pod_spec.get("containers") or []

    env_values: list[str] = []
    images: list[str] = []
    for container in containers:
        if container.get("image"):
            images.append(container["image"])
        for env in container.get("env") or []:
            if isinstance(env.get("value"), (str, int, float)):
                env_values.append(str(env["value"]))

    selector = spec.get("selector") or {}
    pod_labels = (
        (template.get("metadata") or {}).get("labels")
        or (selector.get("matchLabels") if isinstance(selector, dict) else None)
        or {}
    )

    return {
        "key": f"{namespace}/{kind}/{name}",
        "name": name,
        "namespace": namespace,
        "kind": kind,
        "replicas_desired": spec.get("replicas"),
        "pod_labels": pod_labels,
        "labels": metadata.get("labels") or {},
        "images": images,
        # In-memory only: stripped by project_snapshot once edges are built.
        "_env_values": env_values,
        "config_refs": {"config_maps": [], "secrets": []},
    }


def project_snapshot(
    snapshot: dict,
    files: list[dict],
    *,
    default_namespace: str = "default",
    kustomize: dict[str, str] | None = None,
) -> tuple[dict, dict[str, str]]:
    """
    Build the snapshot the cluster would have after this pull request merges.

    Returns (projected_snapshot, changes) where ``changes`` maps workload key
    to "added" | "modified" | "removed".

    Deletions are applied, additions inserted, and modifications overwrite the
    observed record. Everything the pull request does not touch is carried
    through unchanged -- the projection is the current cluster with the diff
    applied, not a graph built from the repository alone. A repository rarely
    contains every workload running in a cluster, and scoring only what it
    contains would compare a partial graph against a complete one.
    """
    from .pr_review import resolve_namespace

    workloads = {w["key"]: dict(w) for w in (snapshot.get("workloads") or []) if w.get("key")}
    services = {
        f"{s.get('namespace')}/{s.get('name')}": dict(s)
        for s in (snapshot.get("services") or [])
    }
    changes: dict[str, str] = {}

    for entry in files:
        path = entry.get("path") or ""
        if not manifest_diff.is_manifest_path(path):
            continue
        objects, error = manifest_diff.parse_manifests(entry.get("after") or "")
        if error:
            continue

        removed, _ = manifest_diff.parse_manifests(entry.get("before") or "")
        after_ids = {
            (o.get("kind"), (o.get("metadata") or {}).get("name")) for o in objects
        }
        for obj in removed:
            identity = (obj.get("kind"), (obj.get("metadata") or {}).get("name"))
            if identity in after_ids or obj.get("kind") not in manifest_diff.WORKLOAD_KINDS:
                continue
            change = manifest_diff.ObjectChange(
                kind=obj["kind"], name=identity[1],
                namespace=(obj.get("metadata") or {}).get("namespace"), path=path,
            )
            namespace, _, _ = resolve_namespace(
                change, snapshot, kustomize=kustomize,
                default_namespace=default_namespace,
            )
            key = f"{namespace}/{obj['kind']}/{identity[1]}"
            if workloads.pop(key, None) is not None:
                changes[key] = "removed"

        for obj in objects:
            kind = obj.get("kind")
            name = (obj.get("metadata") or {}).get("name")
            if not name:
                continue
            change = manifest_diff.ObjectChange(
                kind=kind, name=name,
                namespace=(obj.get("metadata") or {}).get("namespace"), path=path,
            )
            namespace, _, _ = resolve_namespace(
                change, snapshot, kustomize=kustomize,
                default_namespace=default_namespace,
            )

            if kind == "Service":
                spec = obj.get("spec") or {}
                services[f"{namespace}/{name}"] = {
                    "name": name, "namespace": namespace,
                    "selector": spec.get("selector") or {},
                }
                continue

            projected = _workload_from_manifest(obj, namespace)
            if projected is None:
                continue
            changes[projected["key"]] = (
                "modified" if projected["key"] in workloads else "added"
            )
            workloads[projected["key"]] = projected

    # --- edges -------------------------------------------------------------
    # Rebuilt for the whole projected cluster rather than patched, so an
    # existing workload that the PR points at a new service gets its new edge.
    service_to_workload: dict[str, str] = {}
    bare: dict[str, set[str]] = {}
    for service in services.values():
        selector = service.get("selector") or {}
        if not selector:
            continue
        for workload in workloads.values():
            if workload.get("namespace") != service.get("namespace"):
                continue
            labels = workload.get("pod_labels") or {}
            if all(labels.get(k) == v for k, v in selector.items()):
                service_to_workload[f"{service['namespace']}/{service['name']}"] = workload["key"]
                bare.setdefault(str(service["name"]).lower(), set()).add(workload["key"])
                break
    for name, targets in bare.items():
        if len(targets) == 1:
            service_to_workload[name] = next(iter(targets))

    known = {n for n in service_to_workload if "/" not in n}
    edges: dict[tuple[str, str], dict] = {}

    # Observed edges survive for workloads the PR did not touch.
    for edge in snapshot.get("edges") or []:
        source, target = edge.get("source"), edge.get("target")
        if not source or not target:
            continue
        if changes.get(source) in ("removed",) or changes.get(target) in ("removed",):
            continue
        if source not in workloads and "/Ingress/" not in str(source):
            continue
        if target not in workloads:
            continue
        # A modified workload's outbound edges are re-derived below from its
        # new manifest; keeping the old ones would show a removed dependency
        # as still present.
        if changes.get(source) in ("added", "modified"):
            continue
        edges[(source, target)] = dict(edge)

    for key, workload in workloads.items():
        if changes.get(key) not in ("added", "modified"):
            continue
        for host in _service_references(workload.get("_env_values") or [], known):
            target = (
                service_to_workload.get(f"{workload['namespace']}/{host}")
                or service_to_workload.get(host)
            )
            if target and target != key:
                edges[(key, target)] = {
                    "source": key, "target": target,
                    "confidence": 0.9, "source_kind": "env_reference",
                }

    # Environment values are discarded here. They exist only long enough to
    # resolve references and must not reach a response.
    for workload in workloads.values():
        workload.pop("_env_values", None)

    projected = dict(snapshot)
    projected["workloads"] = list(workloads.values())
    projected["services"] = list(services.values())
    projected["edges"] = sorted(edges.values(), key=lambda e: (e["source"], e["target"]))
    return projected, changes


def compare(
    snapshot: dict,
    files: list[dict],
    *,
    default_namespace: str = "default",
    kustomize: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Score the current graph and the projected one, and report the difference.
    """
    from .blast_radius import build_dependency_graph

    projected, changes = project_snapshot(
        snapshot, files,
        default_namespace=default_namespace, kustomize=kustomize,
    )

    before_graph = build_dependency_graph(snapshot)
    after_graph = build_dependency_graph(projected)
    # Two measures, deliberately. PageRank shares feed concentration, where
    # normalising to a fixed total is exactly what makes the index meaningful.
    # Reachability feeds per-workload movement, where that same normalisation
    # would report a workload gaining a caller as having become less central.
    before_rank = structural_centrality(before_graph)
    after_rank = structural_centrality(after_graph)
    before = dependent_fraction(before_graph)
    after = dependent_fraction(after_graph)

    before_conc = concentration(before_rank)
    after_conc = concentration(after_rank)
    conc_delta = after_conc - before_conc

    findings: list[StructuralFinding] = []

    # --- concentration ------------------------------------------------------
    #
    # Relative change is the headline, but it is undefined on a flat cluster
    # where nothing depends on anything -- and that is the single most
    # important case, because the change is introducing concentration where
    # there was none. Guarding on `before > 0` alone silently skipped it.
    relative = conc_delta / before_conc if before_conc > 0 else None
    introduces = before_conc <= 1e-9 and after_conc >= 0.05
    increases = relative is not None and relative >= CONCENTRATION_DELTA_THRESHOLD

    if introduces or increases:
        findings.append(StructuralFinding(
            kind="concentration_increased",
            severity=(
                "critical" if (relative is not None and relative >= 0.4) or introduces
                else "warning"
            ),
            title=(
                f"This change increases structural concentration by "
                f"{relative:.0%}"
                if relative is not None else
                "This change introduces structural concentration where the "
                "cluster had none"
            ),
            detail=(
                f"Concentration moves from {before_conc:.3f} to "
                f"{after_conc:.3f}. More of the system's structural importance "
                "is collecting in fewer workloads, which means more of it "
                "fails together. This is a property of the topology, not of "
                "any single workload, and no per-object review surfaces it."
                + (
                    " Nothing depended on anything before this change, so it "
                    "is creating the cluster's first shared point of failure."
                    if before_conc <= 1e-9 else ""
                )
            ),
            evidence={
                "before": round(before_conc, 4),
                "after": round(after_conc, 4),
                "relative_change": None if relative is None else round(relative, 4),
                "absolute_change": round(conc_delta, 4),
            },
        ))
    elif before_conc > 0 and conc_delta < 0 and abs(conc_delta) / before_conc >= 0.10:
        findings.append(StructuralFinding(
            kind="concentration_reduced",
            severity="info",
            title=(
                f"This change reduces structural concentration by "
                f"{abs(conc_delta) / before_conc:.0%}"
            ),
            detail=(
                "Structural importance is more evenly spread after this "
                "change than before it."
            ),
            evidence={"before": round(before_conc, 4), "after": round(after_conc, 4)},
        ))

    # --- per-workload movement ---------------------------------------------
    movements = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key, 0.0), after.get(key, 0.0)
        if was == 0 and now == 0:
            continue
        delta = now - was
        relative = delta / was if was > 0 else float("inf")
        movements.append({
            "workload_key": key,
            "before": round(was, 5),
            "after": round(now, 5),
            "delta": round(delta, 5),
            "relative_change": None if was == 0 else round(relative, 4),
            "status": changes.get(key, "unchanged"),
        })

        if was > 0 and relative >= CENTRALITY_DELTA_THRESHOLD and now >= 0.05:
            findings.append(StructuralFinding(
                kind="centrality_increased",
                severity="warning" if relative >= 0.5 else "info",
                title=(
                    f"{key.split('/')[-1]} becomes "
                    f"{relative:.0%} more structurally central"
                ),
                detail=(
                    f"{was:.0%} of the cluster depended on this workload "
                    f"before the change and {now:.0%} does after. That may be "
                    "intended -- a shared service is supposed to be depended "
                    "on -- but it raises what a failure here costs, and the "
                    "cause is this pull request rather than anything in the "
                    "workload's own manifest."
                ),
                workload_key=key,
                evidence={
                    "dependent_fraction_before": round(was, 5),
                    "dependent_fraction_after": round(now, 5),
                    "pagerank_before": round(before_rank.get(key, 0.0), 5),
                    "pagerank_after": round(after_rank.get(key, 0.0), 5),
                },
            ))

    movements.sort(key=lambda m: -abs(m["delta"]))

    # --- new nodes ----------------------------------------------------------
    added = [k for k, v in changes.items() if v == "added"]
    for key in sorted(added):
        score = after.get(key, 0.0)
        dependents = [
            s for s, t in after_graph.edges() if t == key
            and after_graph.nodes[s].get("is_workload")
        ]
        if dependents:
            findings.append(StructuralFinding(
                kind="new_shared_dependency",
                severity="warning" if len(dependents) >= 2 else "info",
                title=(
                    f"New workload {key.split('/')[-1]} is depended on by "
                    f"{len(dependents)} existing workload(s)"
                ),
                detail=(
                    "This pull request introduces a service that existing "
                    "workloads immediately depend on. It has no operational "
                    "history, no measured reliability, and is on their "
                    "critical path from the moment it merges."
                ),
                workload_key=key,
                evidence={
                    "dependents": sorted(dependents),
                    "structural_centrality": round(score, 5),
                },
            ))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.title))

    return {
        "concentration": {
            "before": round(before_conc, 4),
            "after": round(after_conc, 4),
            "delta": round(conc_delta, 4),
            "relative_change": (
                round(conc_delta / before_conc, 4) if before_conc > 0 else None
            ),
            "measure": "normalised Herfindahl-Hirschman index over structural centrality",
        },
        "graph": {
            "workloads_before": sum(
                1 for _, d in before_graph.nodes(data=True) if d.get("is_workload")
            ),
            "workloads_after": sum(
                1 for _, d in after_graph.nodes(data=True) if d.get("is_workload")
            ),
            "edges_before": before_graph.number_of_edges(),
            "edges_after": after_graph.number_of_edges(),
            "added": sorted(added),
            "removed": sorted(k for k, v in changes.items() if v == "removed"),
            "modified": sorted(k for k, v in changes.items() if v == "modified"),
        },
        "movements": movements[:20],
        "measures": {
            "concentration": (
                "normalised Herfindahl-Hirschman index over PageRank shares"
            ),
            "movement": (
                "fraction of the cluster that depends on the workload, "
                "directly or transitively. Used instead of PageRank because "
                "PageRank is a share of a fixed total and dilutes when the "
                "pull request adds nodes."
            ),
        },
        "findings": [f.to_dict() for f in findings],
        "note": (
            "Structural centrality only. Entropy is measured over utilization "
            "history, which a workload that does not exist yet has none of, so "
            "the full CEI cannot be computed for a proposed change and is not "
            "estimated here."
        ),
    }
