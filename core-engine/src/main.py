"""
CloudOptimizer Core Engine — FastAPI Application

Implements USPTO Patent App. No. 19/641,446 (priority 63/999,378): System and
Method for Dynamic Resource Allocation in Distributed Computing Environments
Using Adaptive Centrality-Entropy Index with Oscillation Suppression and Fault
Propagation Control.

This module is assembly only. Endpoints live in src/routers/, the pipeline
lives in src/services/analysis.py, and the patent modules (101-112) live in
their original packages with their reference numbering intact.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import analysis, pricing, sandbox, scenarios

# Product routers depend on the database. They are imported lazily below so
# that a deployment without DATABASE_URL -- which is every deployment until
# the Postgres service is attached -- still serves the scenario, pricing, and
# benchmark endpoints backing the USPTO/NIW evidence pages.

app = FastAPI(
    title="CloudOptimizer Core Engine",
    description="Governance-Aware Dynamic Resource Allocation using Adaptive CEI",
    version="1.0.0",
)


def _allowed_origins() -> list[str]:
    """
    Resolve CORS origins from CORS_ALLOWED_ORIGINS (comma-separated).

    Defaults to the previous hardcoded localhost pair when unset, so an
    existing deployment behaves exactly as before until it is configured.
    The demo pages call this service directly for /pricing/savings and
    /benchmark/hpa-vs-cei, so a deployed frontend needs its own origin
    listed here or those two panels silently stay empty -- the frontend
    swallows the error and renders nothing.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:3000", "http://localhost:3001"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router)
app.include_router(scenarios.router)
app.include_router(pricing.router)

# Sandbox is public and needs no database: the point is to demonstrate the
# product with nothing installed and nothing signed up for.
app.include_router(sandbox.router)


def _mount_product_routers() -> bool:
    """
    Attach the agent-ingest and dashboard routers when a database is present.

    Registering them unconditionally would make every request to this service
    depend on Postgres being reachable, including the unauthenticated
    scenario endpoints that back the NIW/USPTO evidence pages. Those have no
    database dependency and should not acquire one.
    """
    if not os.environ.get("DATABASE_URL", "").strip():
        return False
    from .routers import app_api, ingest, scan

    app.include_router(ingest.router)
    app.include_router(scan.router)
    app.include_router(app_api.router)
    return True


PRODUCT_API_ENABLED = _mount_product_routers()


@app.get("/health", tags=["meta"])
async def health_check():
    return {
        "status": "healthy",
        "engine": "CloudOptimizer CEI Core",
        "version": "1.0.0",
        # Lets an operator confirm at a glance whether the agent-facing API is
        # live, rather than inferring it from a 404 on /v1/ingest.
        "product_api": "enabled" if PRODUCT_API_ENABLED else "disabled (no DATABASE_URL)",
    }
