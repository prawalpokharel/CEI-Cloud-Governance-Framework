# Backlog

Single tracked list: decisions waiting on you, bugs, gaps, and debt.
Supersedes `OPEN_QUESTIONS.md`.

Last updated: end of Phase 1 weeks 1–4.

Legend: **[D]** decision needed · **[B]** bug · **[G]** gap, scheduled ·
**[T]** debt

---

## Waiting on you

| # | Item | Why it matters |
|---|---|---|
| D1 | **Centrality mode** — `blast_radius` (default) or `structural` | Now answerable by looking: the dashboard has a switcher. On Online Boutique they rank `productcatalogservice` vs `frontend` first. This is the "5 that can take your system down" claim. |
| D2 | **Rightsizing basis** | Being replaced in Week 6 with measured requested-vs-used. Confirm you want that rather than keeping the CEI-based heuristic. |
| D3 | **Derived instance types in scenarios** | `smallest_fitting_instance()` infers cost from `cpu_limit`/`mem_gb`, which the scenario files do not declare. Keep, or write explicit `instance_type` values into the topology JSONs? Affects NIW-facing figures. |
| D4 | **npm lockfiles** | Untracked. Committing pins versions Railway currently resolves freely. |
| D5 | **Setting `DATABASE_URL` opens public signup** | `/v1/auth/signup` goes live the moment the variable is set. Options in `SETUP.md`: leave unset, deploy the product API as a separate Railway service, or add an invite gate (~1h). |

---

## Bugs

| # | Item | Status |
|---|---|---|
| B1 | `/api/cloud/*` on the Express backend is unauthenticated — including the OAuth callback and token store | Open. Mock-mode only today, so no real credentials at risk. Phase 5 replaces the mechanism entirely with cross-account IAM roles, so the code is throwaway; it should still be gated or deleted rather than left open. |
| B2 | Rollback manager (Module 112) keeps snapshots in a process dict | **Deferred on purpose.** Nothing writes to a cluster yet, so there is nothing to roll back; it matters at Phase 7 (Write Mode). Moving it to Postgres now would also make the `/rollback/*` endpoints require `DATABASE_URL`, which they currently do not. |
| B3 | Snapshot retention | **Done.** `python -m src.cli prune` deletes snapshots >24h and samples >30d, chunked, always keeping each cluster's latest. Wire to a scheduler. |
| B4 | `detect_provider()` may not recognise every managed offering | Unverified. Cosmetic but visible. Confirm during multi-cloud validation. |

### Fixed this round

- Agent reported `cpu_cores_requested` per pod but `cpu_cores_used` summed
  across replicas, so a 3-replica workload looked 3x better utilized than it
  was — and would have understated its cost by the replica count
- CrashLoopBackOff detection keyed on a transient state, so a periodic
  snapshot missed real crash loops most of the time; now keys on restart
  count plus termination reason
- Cost allocation summed `max(cpu_share, mem_share)`, which allocated $175
  against a $140 bill

### Fixed, recorded so they are not reintroduced

- Scenario telemetry never reached the pipeline (path mismatch + `mem` vs `memory` key)
- Oscillation detector was amplitude-blind, flagging 100% of nodes forever
- k-hop safety check compared an unbounded sum against a ratio, so savings were always $0
- Cross-request state leak: β decayed 0.27 → 0.09 over four identical calls
- Registering one cluster twice caused silent permanent data loss (HTTP 200, nothing stored)
- Restarted agents went permanently silent (`seq` resets, idempotency keyed on it)
- `connected` meant "reported once, ever" — a dead agent showed as healthy
- Server silently overrode the operator's configured poll interval
- Response reported `beta: 0.35` while scoring with `beta: 0`
- Test suite wiped the dev database (guard added)

---

## Gaps

| # | Item | When |
|---|---|---|
| G1 | Agent image not published to ghcr.io | One command: `git tag agent-v0.1.0 && git push origin agent-v0.1.0` |
| G2 | Multi-cloud validation (EKS/AKS/GKE) not run | Yours — runbook at `docs/VALIDATION.md` |
| G3 | Scale test at 500–1000 pods | Still open. Snapshot is ~29 KiB at 18 workloads; the questions are ingest latency, JSONB write cost, and D3 render above ~300 nodes. |
| G4 | Waste detection in dollars | **Done.** `/v1/clusters/{id}/cost`. Node price split 70/30 CPU/memory and charged per dimension so allocation reconciles against the bill. |
| G5 | Health diagnostics | **Done.** `/v1/clusters/{id}/health`, ranked by CEI. Crash loops, OOMKills, unschedulable pods, image-pull failures, under-replication, single-replica-with-dependents, missing requests. |
| G9 | Retention needs a scheduler | `prune` exists; nothing invokes it. Railway cron or equivalent. |
| G7 | Email verification never set | Deliberate; needs an email provider |
| G8 | `APP_SECRET_KEY` required for `/app` | Deploy config, documented in `SETUP.md` |
| G6b | Demo/sandbox mode with realistic sample data | Still open — the last Phase 2 item. |

---

## Debt

| # | Item | Note |
|---|---|---|
| T1 | Duplicate `scenarios/` tree (~120k lines twice) | Load-bearing: Railway deploys the `core-engine` subtree and `loader.py`'s path fallback depends on it. Confirm the Railway root directory before touching. |
| T2 | `ClusterTopologyMap` is a copy of `D3DependencyGraph` | Deliberate while the NIW petition is live. Reconcile after. |
| T3 | Agent not yet open-sourced | Your call, after Phase 1 validation. Structured to split cleanly. |
| T4 | No frontend tests | CI builds it; nothing asserts behaviour. |
| T5 | Backend has only a syntax check | No real test suite. Low priority — it is frozen. |
| T6 | Scenario path still synthesizes history when none is supplied | Only fires when a caller supplies no history at all. Scenarios now supply real telemetry, so it is dormant there. The live path never uses it. |

---

## Phase roadmap position

- **Phase 1** (agent + topology + CEI) — weeks 1–4 complete; validation outstanding (G2)
- **Phase 2** (waste in $, health diagnostics) — cost and health done; demo/sandbox mode outstanding
- **Phase 3** (vulnerability scanning, reports, Slack) — Oct–Nov
- **Phase 4+** — per `CloudOptimizer Roadmap v2`
