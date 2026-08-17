"""
Compatibility routes for the frontend's legacy-backend paths.

The marketing/demo/connect pages call a hosted Node backend
(`NEXT_PUBLIC_API_URL`) at paths like /api/demo/* and /api/cloud/*. That
service is not in this repository, so on any local or self-hosted deployment
those pages were dead. This router implements the same contract inside the
core engine, which makes NEXT_PUBLIC_API_URL=http://localhost:8000 a
complete local stack.

Two different kinds of endpoint, handled two different ways:

* **/api/demo/*** are pure aliases. The hosted backend was a proxy in front
  of this service's own /scenarios and /analyze routes, so the compat routes
  simply call the same handlers -- one implementation, two paths, and the
  NIW demonstration numbers cannot diverge between them.

* **/api/cloud/*** re-implements the hosted backend's DEMO cloud connect
  (its connections were always mock -- the frontend renders a MOCK badge
  from the metadata). Connections are process-local, topologies are seeded
  samples labelled as such, and the analysis over them is the real pipeline.

Production is unaffected: deployed frontends keep whatever
NEXT_PUBLIC_API_URL they are configured with, and these routes simply sit
unused unless someone points at them.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ..schemas import AnalysisRequest
from ..services import compat_cloud
from ..services.analysis import run_analysis
from .analysis import run_full_analysis
from .scenarios import analyze_scenario, get_scenario, list_scenarios

router = APIRouter(prefix="/api", tags=["compat"])


def _frontend_url() -> str:
    return (os.environ.get("FRONTEND_URL") or "http://localhost:3000").rstrip("/")


# ---------------------------------------------------------------------------
# /api/demo — aliases over the scenario and analysis routes
# ---------------------------------------------------------------------------

@router.get("/demo/scenarios")
async def demo_scenarios():
    return await list_scenarios()


@router.get("/demo/scenarios/{scenario_id}")
async def demo_scenario(scenario_id: str):
    return await get_scenario(scenario_id)


@router.post("/demo/scenarios/{scenario_id}/analyze")
async def demo_scenario_analyze(scenario_id: str):
    return await analyze_scenario(scenario_id)


@router.post("/demo/analyze")
async def demo_analyze(request: AnalysisRequest):
    return await run_full_analysis(request)


# ---------------------------------------------------------------------------
# /api/cloud — the demo cloud-connect flow
# ---------------------------------------------------------------------------

@router.get("/cloud/providers")
async def cloud_providers():
    return compat_cloud.providers()


@router.get("/cloud/status")
async def cloud_status():
    return compat_cloud.status()


@router.get("/cloud/auth/{provider}")
async def cloud_auth(provider: str):
    """
    The connect button navigates the browser here. The hosted backend runs
    an OAuth redirect chain; the demo implementation records the mock
    connection and sends the browser straight back to the connect page,
    which then shows the provider as connected.
    """
    if not compat_cloud.connect(provider):
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}")
    return RedirectResponse(url=f"{_frontend_url()}/connect", status_code=302)


@router.post("/cloud/disconnect/{provider}")
async def cloud_disconnect(provider: str):
    compat_cloud.disconnect(provider)
    return {"ok": True}


@router.get("/cloud/topology/{provider}")
async def cloud_topology(provider: str):
    topology = compat_cloud.build_topology(provider)
    if topology is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}")
    return topology


@router.post("/cloud/analyze/{provider}")
async def cloud_analyze(provider: str):
    """
    Discovery plus analysis in one call: {provider, topology, analysis}.

    The topology is the labelled sample; the analysis over it is the real
    pipeline -- the same run_analysis every other entry point uses, so the
    weights, oscillation status, and rankings on the connect page are
    genuine pipeline output over sample data, never sample output.
    """
    discovered = compat_cloud.build_topology(provider)
    if discovered is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}")

    request = AnalysisRequest(telemetry={
        "nodes": discovered["topology"]["nodes"],
        "edges": discovered["topology"]["edges"],
        "governance_policies": {
            "compliance_framework": "standard",
            "mission_criticality": "operational",
        },
    })
    analysis = run_analysis(request)
    return {
        "provider": provider,
        "topology": discovered,
        "analysis": analysis.dict() if hasattr(analysis, "dict") else analysis,
    }
