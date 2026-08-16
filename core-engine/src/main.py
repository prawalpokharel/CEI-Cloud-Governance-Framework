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

from .routers import analysis, pricing, scenarios

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


@app.get("/health", tags=["meta"])
async def health_check():
    return {
        "status": "healthy",
        "engine": "CloudOptimizer CEI Core",
        "version": "1.0.0",
    }
