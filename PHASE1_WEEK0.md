# Phase 1 — Week 0

Foundations for the agent-first pivot. Everything here is additive or
behaviour-preserving with respect to the deployed services, except two
deliberate corrections to the CEI engine described under *Numbers that moved*.

## Deploying this

**Nothing is required.** All three services start and behave as before with no
configuration changes. The database layer is dormant until Week 1 wires it in —
`src/main.py` does not import `src/db`, so `DATABASE_URL` is unnecessary.

Three optional settings, in rough priority order:

| Variable | Service | Why |
|---|---|---|
| `CORS_ALLOWED_ORIGINS` | core-engine | Comma-separated origins. **Probably already needed.** The demo pages call the core engine directly for `/pricing/savings` and `/benchmark/hpa-vs-cei`; without the deployed frontend origin listed, the browser blocks both and the pages silently render those panels empty. Defaults to the previous localhost pair. |
| `JWT_SECRET` | backend | Authenticated routes now return `503` rather than signing with a hardcoded secret. Public demo routes are unaffected either way. |
| `DATABASE_URL` | core-engine | Not used yet. Reference the Railway Postgres service variable rather than pasting a value, so rotation doesn't require a redeploy. |

Migrations are **not** run automatically on deploy. When the time comes:
`cd core-engine && alembic upgrade head`.

### Before adding the Postgres service

Confirm the core-engine service's Railway **root directory**. The duplicated
`scenarios/` tree at repo root and under `core-engine/` exists because only the
`core-engine` subtree is deployed — the loader's path fallback in
`src/scenarios/loader.py` depends on it. Deduplicating requires changing that
setting first, so it has been left alone.

## What changed

### Deterministic engine (0.3)

Patent modules 101–111 were process-level singletons carrying mutating state.
Repeating an identical request returned different numbers each time — β decayed
0.27 → 0.09 over four calls, progressively erasing the entropy term — and
analysing one scenario shifted the next one's results. Four of five scenarios
were affected.

They are now built per request (`src/engine.py`). Class names and patent
reference numbers are unchanged. Module 112 (rollback) stays shared, since its
snapshot store must outlive a request; it is still process-local and moves to
Postgres in Week 1.

### Regression guard for the NIW surface (0.2)

`core-engine/tests/golden/` pins 32 responses across `/scenarios`, `/pricing`,
and `/benchmark`, using the exact payloads the demo pages send. Verified to
catch a 0.01 threshold change, and verified reproducible across
`PYTHONHASHSEED` values.

```bash
cd core-engine
.venv/bin/python -m pytest                  # assert nothing moved
.venv/bin/python -m tests.compare_surface   # readable before/after summary
.venv/bin/python -m tests.regen_golden      # re-pin (reviewed changes only)
```

### Persistence (0.4)

Seven tables — tenants, users, clusters, api_keys, snapshots,
workload_samples, audit_log — with `tenant_id` everywhere and timezone-aware
timestamps throughout. Handles Railway's `postgres://` URL form and strips the
libpq-only parameters asyncpg rejects. Migration verified reversible.

### Router split (0.5)

`main.py` went from 489 lines to 64 and is now assembly only. Endpoints live in
`src/routers/`, the pipeline in `src/services/analysis.py`, shared models in
`src/schemas.py`. Verified behaviour-neutral: goldens pass unchanged.

### Hardening (0.6)

- Removed `/scenarios/_debug`, which publicly returned filesystem paths and
  directory listings. Unreferenced anywhere.
- `JWT_SECRET` no longer falls back to a literal published in this repository.
  Missing secret now warns at boot and fails authenticated routes closed with
  `503`, rather than crashing the process — the demo routes back NIW evidence
  pages and don't authenticate, so refusing to start would take them offline to
  fix a problem they don't have.
- CORS origins configurable, defaulting to previous behaviour.

### CI (0.7)

`.github/workflows/ci.yml` — pytest against a real Postgres, migration
reversibility, `alembic check` for model/schema drift, golden reproducibility,
backend syntax check, frontend build. All jobs verified locally.

> No git remote is configured, so CI won't run until this is pushed to GitHub.

## Numbers that moved

Two defects were corrected, both of which change NIW-facing figures. Run
`python -m tests.compare_surface` against the previous commit to see the full
before/after.

**Scenario telemetry never reached the pipeline.** `DataCollector` read history
from `node["metrics"]["utilization_history"]`; the loader wrote it to
`node["utilization_history"]`. The paths never matched, so all 180 real points
per node were discarded for unseeded Gaussian noise. A second half: scenario
points use `"mem"` but every consumer reads `"memory"`, so fixing only the path
would have left memory at a flat `0.5`. Both fixed — entropy spread roughly
doubled, because it now measures workload variability rather than the width of
a noise distribution.

**The oscillation detector was amplitude-blind.** It counted every derivative
sign change and treated amplitude as an additive bonus, so any noisy series
scored 0.5–0.7 — σ=0.001 scored *higher* than σ=0.05. Replaced with a deadband
reduction requiring both frequent and large reversals. cloud_microservices went
from 15/15 flagged with suppression active to 3/15 with suppression off.

The demonstration surface is strictly better defined than before: it now uses
the datasets it cites, and returns the same answer twice.

## Open decisions

1. **k-hop safety check compares a sum against a ratio** (detail in
   `core-engine/tests/golden/README.md`). This is why savings remain `$0.00`
   across all scenarios even after the two fixes above. Left open because the
   patent specification uses the word "cumulative", making the resolution a
   decision about Module 110's described behaviour rather than a typo.
2. **What centrality means** — internal blast radius or user-facing importance?
   See `spikes/k8s_edge_inference/FINDINGS.md`. Needed before CEI tuning in
   Week 3.
3. **Unpinned dependencies.** `requirements.txt` has no version pins, so a
   redeploy can silently change the engine's numeric output. Worth pinning now
   that goldens exist to detect it.
