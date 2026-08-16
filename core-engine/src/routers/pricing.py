"""
Pricing and HPA-vs-CEI benchmark endpoints.

Part of the NIW / USPTO evidence surface -- responses are pinned by
tests/golden/.
"""

import traceback
from typing import Dict

from fastapi import APIRouter, HTTPException

from ..pricing import (
    INSTANCE_PRICES,
    compute_savings,
    list_supported_providers,
    monthly_cost,
    run_hpa_vs_cei,
)

router = APIRouter(tags=["pricing"])


@router.get("/pricing/providers")
async def pricing_providers():
    """List supported cloud providers in the embedded pricing tables."""
    return {
        "providers": list_supported_providers(),
        "instance_counts": {p: len(INSTANCE_PRICES[p]) for p in INSTANCE_PRICES},
    }


@router.get("/pricing/instance/{provider}/{instance_type}")
async def pricing_instance(provider: str, instance_type: str, replicas: int = 1):
    """Return the monthly USD cost for a single instance type."""
    spec = INSTANCE_PRICES.get(provider, {}).get(instance_type)
    if not spec:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown instance type {instance_type!r} for provider {provider!r}",
        )
    return {
        "provider": provider,
        "instance_type": instance_type,
        "spec": spec,
        "replicas": replicas,
        "monthly_cost_usd": monthly_cost(provider, instance_type, replicas),
    }


@router.post("/pricing/savings")
async def pricing_savings(payload: Dict):
    """
    Compute per-node rightsizing recommendations and total monthly savings
    given a topology + analysis result.

    Expected payload:
      {
        "nodes":            [{id, provider, instance_type, replicas, tier?}, ...],
        "analysis_nodes":   [{node_id, cei_score, classification, recommendation}, ...],
        "governance":       {tiers: {...}}      # optional
        "tau_down": 0.25, "tau_up": 0.65        # optional thresholds
      }
    """
    try:
        return compute_savings(
            nodes=payload.get("nodes", []),
            analysis_nodes=payload.get("analysis_nodes", []),
            governance=payload.get("governance"),
            tau_down=payload.get("tau_down", 0.25),
            tau_up=payload.get("tau_up", 0.65),
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.post("/benchmark/hpa-vs-cei")
async def benchmark_hpa_vs_cei(payload: Dict):
    """
    Side-by-side metrics comparing a naive HPA control loop against the full
    CEI pipeline on the same scenario.

    Expected payload:
      {
        "nodes":               [topology nodes with provider/instance_type/replicas/tier],
        "edges":               [edge tuples or {source, target, weight} dicts],
        "analysis_nodes":      [analysis.nodes],
        "oscillation_status":  analysis.oscillation_status,
        "governance":          {tiers: {...}}    # optional
      }
    """
    try:
        result = run_hpa_vs_cei(
            nodes=payload.get("nodes", []),
            edges=payload.get("edges", []),
            analysis_nodes=payload.get("analysis_nodes", []),
            oscillation_status=payload.get("oscillation_status", {}),
            governance=payload.get("governance"),
        )
        return result.to_dict()
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
