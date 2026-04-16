"""
CloudOptimizer Core Engine — FastAPI Application
Implements USPTO Patent App. No. 19/641,446 (priority 63/999,378): System and Method for Dynamic Resource
Allocation in Distributed Computing Environments Using Adaptive Centrality-Entropy
Index with Oscillation Suppression and Fault Propagation Control.
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import json
import traceback

from .cei.data_collector import DataCollector
from .cei.cei_calculator import CEICalculator
from .cei.stability_monitor import StabilityMonitor
from .cei.adaptive_weights import AdaptiveWeightRecalibrator
from .graph.dependency_graph import DependencyGraphConstructor
from .governance.policy_store import GovernancePolicyStore
from .oscillation.detector import OscillationDetector
from .fault.propagation import FaultPropagationSimulator
from .simulation.validator import PreModificationValidator
from .recommendation.actuator import RecommendationActuator
from .rollback.manager import RollbackManager
from .scenarios.loader import ScenarioLoader, ScenarioLoadError
from .pricing import (
    INSTANCE_PRICES,
    list_supported_providers,
    monthly_cost,
    compute_savings,
    run_hpa_vs_cei,
)

app = FastAPI(
    title="CloudOptimizer Core Engine",
    description="Governance-Aware Dynamic Resource Allocation using Adaptive CEI",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize patent component modules (refs 101-112)
data_collector = DataCollector()              # Module 101
graph_constructor = DependencyGraphConstructor()  # Module 103
governance_store = GovernancePolicyStore()     # Module 104
stability_monitor = StabilityMonitor()        # Module 105
cei_calculator = CEICalculator()              # Module 106
weight_recalibrator = AdaptiveWeightRecalibrator()  # Module 107
oscillation_detector = OscillationDetector()  # Module 108
fault_simulator = FaultPropagationSimulator() # Module 109
pre_mod_validator = PreModificationValidator()  # Module 110
actuator = RecommendationActuator()           # Module 111
rollback_manager = RollbackManager()          # Module 112
scenario_loader = ScenarioLoader()


# --- Request/Response Models ---

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


# --- API Endpoints ---

@app.get("/health")
async def health_check():
    return {"status": "healthy", "engine": "CloudOptimizer CEI Core", "version": "1.0.0"}


@app.post("/analyze", response_model=AnalysisResponse)
async def run_full_analysis(request: AnalysisRequest):
    """
    Execute the complete CEI analysis pipeline:
    1. Collect and validate telemetry (Module 101)
    2. Construct dependency graph (Module 103)
    3. Apply governance policies (Module 104)
    4. Compute stability scores (Module 105)
    5. Calculate CEI with adaptive weights (Modules 106, 107)
    6. Detect oscillations (Module 108)
    7. Model fault propagation (Module 109)
    8. Validate modifications via k-hop simulation (Module 110)
    9. Generate recommendations (Module 111)
    """
    try:
        # Step 1: Data Collection (Patent Module 101)
        telemetry_data = data_collector.collect(request.telemetry.nodes)

        # Step 2: Graph Construction (Patent Module 103)
        graph = graph_constructor.build(
            telemetry_data,
            request.telemetry.edges
        )

        # Step 3: Governance Policy Application (Patent Module 104)
        governance_store.load_policies(request.telemetry.governance_policies)
        risk_factors = governance_store.compute_risk_factors(telemetry_data)

        # Step 4: Stability Monitoring (Patent Module 105)
        stability_scores = stability_monitor.compute(
            telemetry_data,
            window_days=request.analysis_window_days
        )

        # Step 5: Adaptive Weight Recalibration (Patent Module 107)
        weights = weight_recalibrator.recalibrate(
            stability_scores=stability_scores,
            oscillation_detected=False,
            topology_changed=False
        )

        # Step 6: CEI Calculation (Patent Module 106)
        cei_results = cei_calculator.compute(
            graph=graph,
            telemetry=telemetry_data,
            risk_factors=risk_factors,
            weights=weights
        )

        # Step 7: Oscillation Detection (Patent Module 108)
        oscillation_status = oscillation_detector.detect(
            telemetry_data,
            threshold=request.oscillation_threshold
        )

        # Update weights if oscillation detected
        if oscillation_status["suppression_active"]:
            weights = weight_recalibrator.recalibrate(
                stability_scores=stability_scores,
                oscillation_detected=True,
                topology_changed=False
            )
            cei_results = cei_calculator.compute(
                graph=graph,
                telemetry=telemetry_data,
                risk_factors=risk_factors,
                weights=weights
            )

        # Step 8: Fault Propagation Modeling (Patent Module 109)
        fault_risks = fault_simulator.simulate(graph, cei_results, risk_factors)

        # Step 9: Pre-Modification Validation (Patent Module 110)
        validated_results = pre_mod_validator.validate(
            graph=graph,
            cei_results=cei_results,
            fault_risks=fault_risks,
            governance_policies=governance_store.get_policies(),
            safety_threshold=request.safety_threshold,
            k_hop=request.k_hop
        )

        # Step 10: Generate Recommendations (Patent Module 111)
        recommendations = actuator.generate_recommendations(validated_results)

        # Compute graph metrics
        graph_metrics = graph_constructor.get_metrics(graph)

        # Build response
        node_results = []
        total_savings = 0.0
        for node_id, data in recommendations.items():
            node_results.append(NodeCEIResult(
                node_id=node_id,
                cei_score=data["cei_score"],
                centrality=data["centrality"],
                entropy=data["entropy"],
                risk_factor=data["risk_factor"],
                classification=data["classification"],
                recommendation=data["recommendation"],
                action_type=data.get("action_type"),
                action_details=data.get("action_details", ""),
                estimated_savings=data.get("estimated_savings", 0.0),
                monthly_cost=data.get("monthly_cost", 0.0),
                is_safe=data.get("is_safe", False),
                blocked_reason=data.get("blocked_reason"),
                validation=data.get("validation", {}),
            ))
            total_savings += data.get("estimated_savings", 0.0)

        return AnalysisResponse(
            nodes=node_results,
            weights=weights,
            oscillation_status=oscillation_status,
            total_potential_savings=total_savings,
            graph_metrics=graph_metrics
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cei/compute")
async def compute_cei(request: TelemetryInput):
    """Standalone CEI computation endpoint."""
    telemetry_data = data_collector.collect(request.nodes)
    graph = graph_constructor.build(telemetry_data, request.edges)
    governance_store.load_policies(request.governance_policies)
    risk_factors = governance_store.compute_risk_factors(telemetry_data)
    weights = weight_recalibrator.get_current_weights()
    results = cei_calculator.compute(graph, telemetry_data, risk_factors, weights)
    return {"cei_results": results, "weights": weights}


@app.post("/oscillation/detect")
async def detect_oscillation(request: TelemetryInput):
    """Standalone oscillation detection endpoint."""
    telemetry_data = data_collector.collect(request.nodes)
    status = oscillation_detector.detect(telemetry_data)
    return status


@app.post("/governance/validate")
async def validate_governance(request: TelemetryInput):
    """Validate nodes against governance policies."""
    governance_store.load_policies(request.governance_policies)
    telemetry_data = data_collector.collect(request.nodes)
    risk_factors = governance_store.compute_risk_factors(telemetry_data)
    compliance = governance_store.check_compliance(telemetry_data)
    return {"risk_factors": risk_factors, "compliance": compliance}


@app.post("/rollback/snapshot")
async def create_snapshot(config: Dict):
    """Create a pre-modification snapshot (Patent Module 112)."""
    snapshot_id = rollback_manager.create_snapshot(config)
    return {"snapshot_id": snapshot_id, "status": "created"}


@app.post("/rollback/revert/{snapshot_id}")
async def revert_to_snapshot(snapshot_id: str):
    """Revert to a previous snapshot upon anomaly detection."""
    result = rollback_manager.revert(snapshot_id)
    return result


# --- Scenario Demonstration Endpoints ---

@app.get("/scenarios/list")
async def list_scenarios():
    """List all available demonstration scenarios."""
    try:
        scenarios = scenario_loader.list_scenarios()
        return {"scenarios": scenarios, "count": len(scenarios)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/scenarios/{scenario_id}")
async def get_scenario(scenario_id: str):
    """Retrieve a scenario's full dataset."""
    try:
        return scenario_loader.load(scenario_id)
    except ScenarioLoadError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/scenarios/{scenario_id}/analyze")
async def analyze_scenario(scenario_id: str):
    """Run the full CEI pipeline on a scenario."""
    try:
        scenario = scenario_loader.load(scenario_id)
        engine_input = scenario_loader.to_core_engine_format(scenario)
        analysis_request = AnalysisRequest(
            telemetry=TelemetryInput(
                nodes=engine_input["nodes"],
                edges=engine_input["edges"],
                governance_policies=engine_input["governance_policies"],
            )
        )
        analysis_result = await run_full_analysis(analysis_request)
        return {
            "scenario_id": scenario_id,
            "metadata": scenario["metadata"],
            "analysis": analysis_result,
        }
    except ScenarioLoadError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")
@app.get("/scenarios/_debug")
async def scenarios_debug():
    """Temporary diagnostic — shows what path the loader resolved."""
    from .scenarios.loader import SCENARIOS_DIR
    import os
    return {
        "scenarios_dir": str(SCENARIOS_DIR),
        "exists": SCENARIOS_DIR.exists(),
        "contents": os.listdir(str(SCENARIOS_DIR)) if SCENARIOS_DIR.exists() else [],
        "cwd": os.getcwd(),
        "cwd_contents": os.listdir(os.getcwd()),
    }


# ----------------------------------------------------------------------
# Pricing & benchmark endpoints (PR 7 — feature/cost-savings-engine)
# ----------------------------------------------------------------------


@app.get("/pricing/providers")
async def pricing_providers():
    """List supported cloud providers in the embedded pricing tables."""
    return {
        "providers": list_supported_providers(),
        "instance_counts": {p: len(INSTANCE_PRICES[p]) for p in INSTANCE_PRICES},
    }


@app.get("/pricing/instance/{provider}/{instance_type}")
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


@app.post("/pricing/savings")
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
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/benchmark/hpa-vs-cei")
async def benchmark_hpa_vs_cei(payload: Dict):
    """
    Side-by-side metrics comparing a naive HPA control loop against the
    full CEI pipeline on the same scenario.

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
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.post("/scenarios/{scenario_id}/benchmark")
async def scenario_benchmark(scenario_id: str):
    """
    Convenience wrapper: load a built-in scenario, run analysis, then
    benchmark HPA vs CEI on the result. Returns the full benchmark
    payload plus the savings calculation.
    """
    try:
        scenario = scenario_loader.load(scenario_id)
        engine_input = scenario_loader.to_core_engine_format(scenario)
        analysis = await run_full_analysis(
            AnalysisRequest(
                telemetry=TelemetryInput(
                    nodes=engine_input["nodes"],
                    edges=engine_input["edges"],
                    governance_policies=engine_input["governance_policies"],
                )
            )
        )
        # Re-shape topology nodes for the cost calculator. The seeded
        # scenarios use the loader's normalized format (id, tier, type),
        # so we map provider/instance_type/replicas with safe defaults.
        topo_nodes = []
        for n in scenario["topology"]["nodes"]:
            topo_nodes.append(
                {
                    "id": n["id"],
                    "provider": n.get("provider", "aws"),
                    "instance_type": n.get("instance_type"),
                    "replicas": n.get("replicas", 1),
                    "tier": n.get("tier", "supporting"),
                }
            )
        analysis_nodes = [na.dict() if hasattr(na, "dict") else na for na in analysis.nodes]
        savings = compute_savings(
            nodes=topo_nodes,
            analysis_nodes=analysis_nodes,
            governance=scenario.get("governance"),
        )
        bench = run_hpa_vs_cei(
            nodes=topo_nodes,
            edges=scenario["topology"].get("edges", []),
            analysis_nodes=analysis_nodes,
            oscillation_status=analysis.oscillation_status,
            governance=scenario.get("governance"),
        )
        return {
            "scenario_id": scenario_id,
            "metadata": scenario["metadata"],
            "savings": savings,
            "benchmark": bench.to_dict(),
        }
    except ScenarioLoadError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
