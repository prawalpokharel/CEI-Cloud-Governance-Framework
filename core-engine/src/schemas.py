"""
Shared request/response models.

Extracted from main.py so routers and services can share them without
importing the application module, which would create an import cycle once
main.py imports the routers.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel


class TelemetryInput(BaseModel):
    nodes: List[Dict]
    edges: Optional[List[Dict]] = []
    governance_policies: Optional[Dict] = {}


class AnalysisRequest(BaseModel):
    telemetry: TelemetryInput
    analysis_window_days: int = 90
    oscillation_threshold: float = 0.3
    safety_threshold: float = 0.7
    k_hop: int = 2


class NodeCEIResult(BaseModel):
    node_id: str
    cei_score: float
    centrality: float
    entropy: float
    risk_factor: float
    classification: str
    recommendation: str
    # Extended fields surfaced so the UI can render Quick Wins + pre-mod
    # simulation detail without a second round-trip. All optional so the
    # response stays backward-compatible.
    action_type: Optional[str] = None
    action_details: Optional[str] = ""
    estimated_savings: Optional[float] = 0.0
    monthly_cost: Optional[float] = 0.0
    is_safe: Optional[bool] = False
    blocked_reason: Optional[str] = None
    validation: Optional[Dict] = {}


class AnalysisResponse(BaseModel):
    nodes: List[NodeCEIResult]
    weights: Dict[str, float]
    oscillation_status: Dict
    total_potential_savings: float
    graph_metrics: Dict
