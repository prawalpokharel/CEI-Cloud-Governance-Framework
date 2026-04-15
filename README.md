# CloudOptimizer — Governance-Aware Dynamic Resource Allocation SaaS Platform

## Overview

CloudOptimizer is a full-stack SaaS implementation of the methodology described in **USPTO Non-Provisional Utility Application No. 19/641,446** (filed April 7, 2026 under 35 USC 111(a), claiming priority from provisional **63/999,378**): *System and Method for Dynamic Resource Allocation in Distributed Computing Environments Using Adaptive Centrality-Entropy Index with Oscillation Suppression and Fault Propagation Control.*

## Architecture

```
cloud-optimizer/
├── frontend/          # Next.js 14 — Interactive dashboards & visualization
├── backend/           # Node.js/Express — API server, auth, orchestration
├── core-engine/       # Python/FastAPI — CEI computation, graph analysis, optimization
└── README.md
```

## Patent Claim Cross-Reference (USPTO App. No. 19/641,446)

| Patent Component (Ref#) | Implementation Module | Claim |
|---|---|---|
| Data Collection Module (101) | `core-engine/src/cei/data_collector.py` | 1 |
| Distributed Computing Env (102) | `backend/src/services/cloud_providers.js` | 1 |
| Graph Constructor (103) | `core-engine/src/graph/dependency_graph.py` | 1 |
| Governance Policy Store (104) | `core-engine/src/governance/policy_store.py` | 2 |
| Stability Monitor (105) | `core-engine/src/cei/stability_monitor.py` | 2 |
| CEI Calculator (106) | `core-engine/src/cei/cei_calculator.py` | 1 |
| Adaptive Recalibration (107) | `core-engine/src/cei/adaptive_weights.py` | 2 |
| Oscillation Detector (108) | `core-engine/src/oscillation/detector.py` | 1,3 |
| Fault Propagation Simulator (109) | `core-engine/src/fault/propagation.py` | 1 |
| Pre-Modification Validator (110) | `core-engine/src/simulation/validator.py` | 1 |
| Actuator (111) | `core-engine/src/recommendation/actuator.py` | 1 |
| Rollback Manager (112) | `core-engine/src/rollback/manager.py` | 1 |

## Quick Start

```bash
# Core Engine (Python/FastAPI)
cd core-engine && pip install -r requirements.txt
uvicorn src.main:app --reload --port 8000

# Backend (Node.js/Express)
cd backend && npm install && npm run dev  # port 3001

# Frontend (Next.js 14)
cd frontend && npm install && npm run dev  # port 3000
```

## References
- USPTO Non-Provisional Utility Patent Application No. 19/641,446 (priority date 63/999,378)
- P. Pokharel, "Governance-Aware Dynamic Resource Allocation..." IEEE Cloud Summit 2026
- P. Pokharel, "AI Modernization in the US Air Force..." SSRN, 2025
