"""
Public sandbox endpoints.

Unauthenticated, and mounted regardless of whether a database is configured,
so the product can be demonstrated with nothing installed and nothing signed
up for.

Every response is computed by the same services that serve real clusters, from
a snapshot in the same shape the agent emits. Nothing here is a fixture: if
the analysis changes, the demo changes with it, which is the only way a demo
stays honest over time.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from ..services.cost import analyze_cluster_cost
from ..services.health import diagnose
from ..services.live_cei import CentralityMode, compute_live_cei
from ..services.sandbox import build_sandbox_history, build_sandbox_snapshot

router = APIRouter(prefix="/v1/sandbox", tags=["sandbox"])


# The snapshot and its history are deterministic, so they are built once and
# reused. Analysis is recomputed per request -- it is sub-second even at 1000
# workloads, and caching it would hide a regression from exactly the surface
# most likely to be looked at.
@lru_cache(maxsize=1)
def _snapshot():
    return build_sandbox_snapshot()


@lru_cache(maxsize=1)
def _history():
    return build_sandbox_history(_snapshot())


@router.get("/cluster")
async def sandbox_cluster():
    """Topology, in the same shape /v1/clusters/{id}/topology returns."""
    snapshot = _snapshot()
    return {
        "cluster": {
            "id": "sandbox",
            "name": "acme-production (sample data)",
            "provider": snapshot["cluster"]["provider"],
            "k8s_version": snapshot["cluster"]["kubernetes_version"],
            "metrics_available": True,
            "metrics_reason": None,
            "connected": True,
            "state": "connected",
            "sandbox": True,
        },
        "captured_at": snapshot["captured_at"],
        "seq": snapshot["seq"],
        "nodes": snapshot["nodes"],
        "workloads": snapshot["workloads"],
        "services": snapshot["services"],
        "pods": snapshot["pods"],
        "edges": snapshot["edges"],
        "summary": snapshot["summary"],
    }


@router.get("/cei")
async def sandbox_cei(mode: str = "blast_radius"):
    try:
        centrality_mode = CentralityMode(mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown centrality mode {mode!r}. Valid values: "
                f"{', '.join(m.value for m in CentralityMode)}"
            ),
        )
    result = compute_live_cei(
        _snapshot(), _history(), centrality_mode=centrality_mode
    )
    payload = result.to_dict()
    payload["cluster"] = {"id": "sandbox", "name": "acme-production (sample data)"}
    payload["captured_at"] = _snapshot()["captured_at"]
    return payload


@router.get("/cost")
async def sandbox_cost():
    result = analyze_cluster_cost(_snapshot())
    result["cluster"] = {"id": "sandbox", "name": "acme-production (sample data)"}
    result["captured_at"] = _snapshot()["captured_at"]
    return result


@router.get("/health")
async def sandbox_health():
    snapshot = _snapshot()
    cei = compute_live_cei(snapshot, _history())
    result = diagnose(snapshot, {n["node_id"]: n for n in cei.nodes})
    result["cluster"] = {"id": "sandbox", "name": "acme-production (sample data)"}
    result["captured_at"] = snapshot["captured_at"]
    return result


@router.get("/history")
async def sandbox_history():
    """Mirrors the real history endpoint so the UI needs no special case."""
    return {
        "snapshot_count": 40,
        "first_seen_at": _snapshot()["captured_at"],
        "entropy_ready": True,
        "entropy_samples_required": 30,
    }
