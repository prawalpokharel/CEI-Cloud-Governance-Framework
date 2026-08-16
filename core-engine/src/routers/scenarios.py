"""
Scenario demonstration endpoints.

Part of the NIW / USPTO evidence surface. Responses are pinned by
tests/golden/ -- see tests/golden/README.md before changing anything here.
"""

import traceback

from fastapi import APIRouter, HTTPException

from ..pricing import compute_savings, run_hpa_vs_cei
from ..scenarios.loader import ScenarioLoader, ScenarioLoadError
from ..schemas import AnalysisRequest, TelemetryInput
from ..services.analysis import run_analysis

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

# Read-only; holds no mutable state between calls.
scenario_loader = ScenarioLoader()


def _topology_nodes(scenario: dict) -> list:
    """
    Reshape scenario topology nodes for the cost calculator.

    Seeded scenarios use the loader's normalized format (id, tier, type), so
    provider/instance_type/replicas are mapped with safe defaults.
    """
    return [
        {
            "id": n["id"],
            "provider": n.get("provider", "aws"),
            "instance_type": n.get("instance_type"),
            "replicas": n.get("replicas", 1),
            "tier": n.get("tier", "supporting"),
        }
        for n in scenario["topology"]["nodes"]
    ]


def _analysis_for(scenario: dict):
    engine_input = scenario_loader.to_core_engine_format(scenario)
    return run_analysis(
        AnalysisRequest(
            telemetry=TelemetryInput(
                nodes=engine_input["nodes"],
                edges=engine_input["edges"],
                governance_policies=engine_input["governance_policies"],
            )
        )
    )


@router.get("/list")
async def list_scenarios():
    """List all available demonstration scenarios."""
    try:
        scenarios = scenario_loader.list_scenarios()
        return {"scenarios": scenarios, "count": len(scenarios)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{scenario_id}")
async def get_scenario(scenario_id: str):
    """Retrieve a scenario's full dataset."""
    try:
        return scenario_loader.load(scenario_id)
    except ScenarioLoadError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{scenario_id}/analyze")
async def analyze_scenario(scenario_id: str):
    """Run the full CEI pipeline on a scenario."""
    try:
        scenario = scenario_loader.load(scenario_id)
        return {
            "scenario_id": scenario_id,
            "metadata": scenario["metadata"],
            "analysis": _analysis_for(scenario),
        }
    except ScenarioLoadError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {str(e)}")


@router.post("/{scenario_id}/benchmark")
async def scenario_benchmark(scenario_id: str):
    """
    Load a built-in scenario, run analysis, then benchmark HPA vs CEI on the
    result. Returns the full benchmark payload plus the savings calculation.
    """
    try:
        scenario = scenario_loader.load(scenario_id)
        analysis = _analysis_for(scenario)
        topo_nodes = _topology_nodes(scenario)
        analysis_nodes = [
            na.dict() if hasattr(na, "dict") else na for na in analysis.nodes
        ]
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
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
