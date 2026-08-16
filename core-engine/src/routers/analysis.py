"""Core CEI analysis endpoints (Patent Modules 101-112)."""

from typing import Dict

from fastapi import APIRouter, HTTPException

from ..engine import build_pipeline
from ..rollback.manager import RollbackManager
from ..schemas import AnalysisRequest, AnalysisResponse, TelemetryInput
from ..services.analysis import run_analysis

router = APIRouter(tags=["analysis"])

# Module 112 is genuinely long-lived: /rollback/revert/{id} must find the
# snapshot a previous request created, so this instance is shared.
#
# NOTE: process-local, so it dies on restart and does not work across
# multiple Railway replicas. Moves to Postgres as part of the Phase 1
# persistence work.
rollback_manager = RollbackManager()


@router.post("/analyze", response_model=AnalysisResponse)
async def run_full_analysis(request: AnalysisRequest):
    """Execute the complete CEI analysis pipeline."""
    try:
        return run_analysis(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cei/compute")
async def compute_cei(request: TelemetryInput):
    """Standalone CEI computation endpoint."""
    p = build_pipeline()
    telemetry_data = p.data_collector.collect(request.nodes)
    graph = p.graph_constructor.build(telemetry_data, request.edges)
    p.governance_store.load_policies(request.governance_policies)
    risk_factors = p.governance_store.compute_risk_factors(telemetry_data)
    weights = p.weight_recalibrator.get_current_weights()
    results = p.cei_calculator.compute(graph, telemetry_data, risk_factors, weights)
    return {"cei_results": results, "weights": weights}


@router.post("/oscillation/detect")
async def detect_oscillation(request: TelemetryInput):
    """Standalone oscillation detection endpoint."""
    p = build_pipeline()
    telemetry_data = p.data_collector.collect(request.nodes)
    return p.oscillation_detector.detect(telemetry_data)


@router.post("/governance/validate")
async def validate_governance(request: TelemetryInput):
    """Validate nodes against governance policies."""
    p = build_pipeline()
    p.governance_store.load_policies(request.governance_policies)
    telemetry_data = p.data_collector.collect(request.nodes)
    risk_factors = p.governance_store.compute_risk_factors(telemetry_data)
    compliance = p.governance_store.check_compliance(telemetry_data)
    return {"risk_factors": risk_factors, "compliance": compliance}


@router.post("/rollback/snapshot")
async def create_snapshot(config: Dict):
    """Create a pre-modification snapshot (Patent Module 112)."""
    snapshot_id = rollback_manager.create_snapshot(config)
    return {"snapshot_id": snapshot_id, "status": "created"}


@router.post("/rollback/revert/{snapshot_id}")
async def revert_to_snapshot(snapshot_id: str):
    """Revert to a previous snapshot upon anomaly detection."""
    return rollback_manager.revert(snapshot_id)
