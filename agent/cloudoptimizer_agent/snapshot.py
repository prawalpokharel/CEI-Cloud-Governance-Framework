"""
Snapshot assembly.

A snapshot is the complete observed state of a cluster at one instant. Full
state rather than deltas: idempotent, restart-safe, and immune to
event-ordering bugs. The server replaces whatever it held.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .inference import edge_summary, infer_edges
from .version import AGENT_VERSION

# 2 adds resilience state: PodDisruptionBudgets, HorizontalPodAutoscalers, and
# per-workload probe/spread/ownership/config-reference fields.
#
# Purely additive, and the server treats every one of them as optional. Agents
# roll out on the operator's schedule, not ours -- a v1 agent reporting to a
# server that understands v2 has to keep working, and a v2 agent whose
# ClusterRole has not been updated yet simply sends empty lists.
SNAPSHOT_SCHEMA_VERSION = 2


def build_snapshot(
    *,
    seq: int,
    cluster_uid: str,
    provider: str,
    kubernetes_version: str | None,
    nodes: list[dict],
    workloads: list[dict],
    services: list[dict],
    pods: list[dict],
    ingresses: list[dict],
    network_policies: list[dict],
    metrics_available: bool,
    metrics_reason: str | None,
    disruption_budgets: list[dict] | None = None,
    autoscalers: list[dict] | None = None,
) -> dict[str, Any]:
    edges = infer_edges(workloads, services, ingresses)

    # Belt and braces. attach_service_references already removes this, but a
    # snapshot is the thing that leaves the cluster, so the guarantee is
    # re-asserted at the boundary rather than trusted from upstream.
    for workload in workloads:
        workload.pop("_env_values", None)

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "agent_version": AGENT_VERSION,
        "seq": seq,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cluster": {
            "uid": cluster_uid,
            "provider": provider,
            "kubernetes_version": kubernetes_version,
            "node_count": len(nodes),
            "metrics_available": metrics_available,
            "metrics_reason": metrics_reason,
        },
        "nodes": nodes,
        "workloads": workloads,
        "services": services,
        "pods": pods,
        "ingresses": ingresses,
        "network_policies": network_policies,
        "disruption_budgets": disruption_budgets or [],
        "autoscalers": autoscalers or [],
        "edges": edges,
        "summary": {
            "nodes": len(nodes),
            "workloads": len(workloads),
            "services": len(services),
            "pods": len(pods),
            "disruption_budgets": len(disruption_budgets or []),
            "autoscalers": len(autoscalers or []),
            "edges": edge_summary(edges),
        },
    }


def serialize(snapshot: dict) -> bytes:
    """Compact JSON. Separators matter at 1000-pod scale."""
    return json.dumps(snapshot, separators=(",", ":"), default=str).encode("utf-8")


def assert_no_secrets(payload: bytes) -> None:
    """
    Fail closed if an env var value ever reaches the payload.

    A cheap structural check that the redaction contract held. It cannot
    detect every possible leak, but it catches the realistic regression --
    someone adding a field that carries container env values through.
    """
    text = payload.decode("utf-8", errors="ignore")
    if '"_env_values"' in text or '"env_values"' in text:
        raise RuntimeError(
            "Refusing to transmit snapshot: it contains raw environment "
            "variable values. This is a bug in the agent -- please report it."
        )
