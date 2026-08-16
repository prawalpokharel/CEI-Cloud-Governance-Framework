"""
CEI over a live Kubernetes cluster.

Bridges an agent snapshot into the patent pipeline (Modules 101-111), with
two departures from the scenario path that the live setting demands.

**Entropy uses real accumulated history.** The Shannon entropy term is ~35% of
the score and needs a distribution over time. A freshly installed agent has
none, and the pipeline's fallback fabricates ninety days of Gaussian noise --
which is how the scenario numbers came to be built on random data. Here the
history comes from `workload_samples`, accumulated one row per workload per
ingest, and when there are too few samples the term is withheld and the
weights renormalized rather than invented. A product that sells prioritization
cannot compute a third of its score from noise.

**Centrality semantics are explicit.** See CentralityMode.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from ..engine import build_pipeline
from ..schemas import AnalysisRequest, TelemetryInput
from ..services.analysis import run_analysis

# Samples required before the entropy term is trusted. At a 60s interval this
# is roughly half an hour of observation -- enough for a histogram over ten
# bins to mean something, without making the first-run experience useless.
MIN_ENTROPY_SAMPLES = 30


class CentralityMode(str, enum.Enum):
    """
    What "central" means, made explicit rather than emergent.

    The product claim is "we tell you the 5 that can take your system down",
    which is a statement about blast radius: if this workload fails, how much
    else fails? In a caller -> callee graph that is about DEPENDENTS, so
    PageRank (which flows toward callees) answers it directly and
    degree/betweenness dilute it.

    The alternative reading is user-facing importance. An API gateway has many
    outbound edges and few inbound ones: nothing downstream breaks when it
    dies, but every user is affected. On Online Boutique the two readings put
    `frontend` at opposite ends of the ranking, so this cannot be left to
    whichever weighting happens to be in place.
    """

    # Emphasizes dependents. Answers "what breaks if this fails?"
    blast_radius = "blast_radius"

    # The original composite: PageRank + betweenness + degree + closeness.
    # Rewards being a hub in either direction, so it ranks entrypoints highly.
    structural = "structural"


@dataclass
class LiveCEIResult:
    nodes: list[dict]
    weights: dict[str, float]
    graph_metrics: dict
    oscillation_status: dict
    entropy_ready: bool
    entropy_sample_count: int
    centrality_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "weights": self.weights,
            "graph_metrics": self.graph_metrics,
            "oscillation_status": self.oscillation_status,
            "entropy": {
                "ready": self.entropy_ready,
                "samples": self.entropy_sample_count,
                "samples_required": MIN_ENTROPY_SAMPLES,
                "note": (
                    None
                    if self.entropy_ready
                    else (
                        "Workload variability cannot be measured yet. The "
                        "entropy term is withheld and its weight redistributed "
                        "across centrality and governance risk, rather than "
                        "estimated from too little history."
                    )
                ),
            },
            "centrality_mode": self.centrality_mode,
        }


def _history_for(samples: list[dict]) -> list[dict]:
    """
    Convert stored workload samples into pipeline history points.

    Uses measured usage where metrics-server supplied it. Falls back to
    requested capacity only to keep the series continuous; a workload with no
    measurements at all yields no history, so entropy is withheld for it
    rather than derived from its request, which is a constant and would read
    as perfectly stable.
    """
    points = []
    for sample in samples:
        cpu_used = sample.get("cpu_cores_used")
        cpu_req = sample.get("cpu_cores_requested")
        mem_used = sample.get("mem_bytes_used")
        mem_req = sample.get("mem_bytes_requested")
        if cpu_used is None and mem_used is None:
            continue
        cpu = (
            float(cpu_used) / float(cpu_req)
            if cpu_used is not None and cpu_req
            else 0.0
        )
        mem = (
            float(mem_used) / float(mem_req)
            if mem_used is not None and mem_req
            else 0.0
        )
        points.append({
            "cpu": max(0.0, min(1.0, cpu)),
            "memory": max(0.0, min(1.0, mem)),
        })
    return points


def _tier_for(workload: dict) -> str:
    """
    Map a workload to a governance tier.

    Kubernetes has no notion of mission criticality, so it is inferred from
    namespace conventions until real policy exists. Deliberately coarse and
    visible in the output, so nobody mistakes it for a configured decision --
    Phase 3's governance work replaces it.
    """
    namespace = (workload.get("namespace") or "").lower()
    if namespace in ("kube-system", "kube-public", "kube-node-lease"):
        return "mission_critical"
    if namespace in ("default", "production", "prod"):
        return "operational"
    if any(token in namespace for token in ("dev", "test", "staging", "sandbox")):
        return "development"
    return "operational"


def build_analysis_request(
    snapshot: dict,
    history_by_workload: dict[str, list[dict]],
    *,
    safety_threshold: float = 0.7,
    k_hop: int = 2,
) -> tuple[AnalysisRequest, int]:
    """
    Translate an agent snapshot into the pipeline's request shape.

    Returns (request, total_history_points).
    """
    workloads = snapshot.get("workloads") or []
    edges_in = snapshot.get("edges") or []
    valid = {w["key"] for w in workloads if w.get("key")}

    total_points = 0
    nodes = []
    for workload in workloads:
        key = workload.get("key")
        if not key:
            continue

        cpu_req = workload.get("cpu_cores_requested") or 0.0
        cpu_used = workload.get("cpu_cores_used")
        mem_req = workload.get("memory_bytes_requested") or 0
        mem_used = workload.get("memory_bytes_used")

        # Utilization as a percentage of what the workload asked for. Without
        # metrics this is unknown; 0 would read as idle, so the request value
        # is used instead, which reads as fully utilized and is the
        # conservative direction -- it will not recommend shrinking something
        # whose usage nobody measured.
        cpu_pct = (
            (float(cpu_used) / cpu_req * 100) if cpu_used is not None and cpu_req
            else (100.0 if cpu_used is None else 0.0)
        )
        mem_pct = (
            (float(mem_used) / mem_req * 100) if mem_used is not None and mem_req
            else (100.0 if mem_used is None else 0.0)
        )

        history = history_by_workload.get(key, [])
        total_points += len(history)

        nodes.append({
            "node_id": key,
            "metrics": {
                "cpu_utilization": max(0.0, min(100.0, cpu_pct)),
                "memory_utilization": max(0.0, min(100.0, mem_pct)),
                "network_throughput": 0,
                "disk_io": 0,
            },
            "utilization_history": history,
            "tags": {"criticality": _tier_for(workload)},
            "metadata": {
                "namespace": workload.get("namespace"),
                "kind": workload.get("kind"),
                "name": workload.get("name"),
                "replicas": workload.get("replicas_desired"),
                "tier": _tier_for(workload),
            },
        })

    edges = [
        {
            "source": edge["source"],
            "target": edge["target"],
            "weight": edge.get("confidence", 1.0),
            "type": edge.get("source_kind", "dependency"),
        }
        for edge in edges_in
        # Ingress vertices are not workloads and have no telemetry, so they
        # are dropped rather than added as nodes the pipeline cannot score.
        if edge.get("source") in valid and edge.get("target") in valid
    ]

    request = AnalysisRequest(
        telemetry=TelemetryInput(
            nodes=nodes,
            edges=edges,
            governance_policies={
                "compliance_framework": "standard",
                "mission_criticality": "operational",
            },
        ),
        safety_threshold=safety_threshold,
        k_hop=k_hop,
    )
    return request, total_points


def compute_live_cei(
    snapshot: dict,
    history_by_workload: dict[str, list[dict]],
    *,
    centrality_mode: CentralityMode = CentralityMode.blast_radius,
    safety_threshold: float = 0.7,
    k_hop: int = 2,
) -> LiveCEIResult:
    request, total_points = build_analysis_request(
        snapshot,
        history_by_workload,
        safety_threshold=safety_threshold,
        k_hop=k_hop,
    )

    workload_count = len(request.telemetry.nodes)
    mean_samples = (
        total_points / workload_count if workload_count else 0
    )
    entropy_ready = mean_samples >= MIN_ENTROPY_SAMPLES

    pipeline = build_pipeline()
    pipeline.cei_calculator.centrality_mode = centrality_mode.value
    if not entropy_ready:
        # Withhold rather than invent. The calculator redistributes beta
        # across alpha and gamma when this is set.
        pipeline.cei_calculator.suppress_entropy = True

    analysis = run_analysis(request, pipeline=pipeline)

    # The weights the score was actually computed with. When entropy is
    # suppressed these differ from the recalibrator's output, and reporting
    # the latter would state a beta the result did not use.
    effective = getattr(
        pipeline.cei_calculator, "effective_weights", None
    ) or analysis.weights

    return LiveCEIResult(
        nodes=[n.dict() if hasattr(n, "dict") else n for n in analysis.nodes],
        weights=effective,
        graph_metrics=analysis.graph_metrics,
        oscillation_status=analysis.oscillation_status,
        entropy_ready=entropy_ready,
        entropy_sample_count=int(mean_samples),
        centrality_mode=centrality_mode.value,
    )
