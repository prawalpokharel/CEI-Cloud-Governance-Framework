# CloudOptimizer — Governance-Aware Dynamic Resource Allocation SaaS Platform

> ### 👉 New here? Start with **[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md)**
>
> What CloudOptimizer is, why ranking by blast radius changes the answer, how
> to try it on a throwaway local cluster in 10 minutes, and how to install it
> on your own Kubernetes. Includes exactly what the agent can and cannot read,
> and how to verify that yourself before installing.

## Overview

CloudOptimizer is a full-stack SaaS implementation of the methodology described in **USPTO Non-Provisional Utility Application No. 19/641,446** (filed April 7, 2026 under 35 USC 111(a), claiming priority from provisional **63/999,378**): *System and Method for Dynamic Resource Allocation in Distributed Computing Environments Using Adaptive Centrality-Entropy Index with Oscillation Suppression and Fault Propagation Control.*

## Architecture

```
cloud-optimizer/
├── core-engine/       # Python/FastAPI — CEI computation, graph analysis, product API
├── agent/             # Python — read-only in-cluster Kubernetes agent + scanner
├── charts/            # Helm chart for the agent (read-only RBAC, opt-in scanner)
├── frontend/          # Next.js 14 — scenario pages and the /app dashboard
├── backend/           # Node.js/Express — demo proxy for the scenario pages
├── scenarios/         # Reference datasets for the five demonstration domains
├── spikes/            # Throwaway validation experiments, kept for their findings
└── docs/              # Runbooks
```

The patent modules (101–112) live in `core-engine/src/` with their reference
numbering intact and are exercised by the scenario endpoints. The agent,
scanner, and product API added later build on the same CEI engine rather than
replacing it — `core-engine/tests/golden/` pins the scenario responses so the
demonstration numbers cannot move as a side effect of product work.

See `SETUP.md` for running it, `BACKLOG.md` for current state.

## Patent Claim Cross-Reference (USPTO App. No. 19/641,446)

| Patent Component (Ref#) | Implementation Module | Claim |
|---|---|---|
| Data Collection Module (101) | `core-engine/src/cei/data_collector.py` | 1 |
| Distributed Computing Env (102) | `agent/cloudoptimizer_agent/collector.py` | 1 |
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

For installing the agent on a Kubernetes cluster, see
[docs/GETTING-STARTED.md](docs/GETTING-STARTED.md). The commands below run the
three services locally for development.

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
