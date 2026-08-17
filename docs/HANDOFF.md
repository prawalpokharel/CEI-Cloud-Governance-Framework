# CloudOptimizer — Project Handoff

Written 2026-08-17 on `feat/iverson` (3948cc9). This is the document a new
maintainer — human or AI — reads to know exactly what this product is, why
it is shaped this way, and what to do next. [CLAUDE.md](../CLAUDE.md) at the
repo root is the short version; this is the long one.

---

## 1. What CloudOptimizer is

CloudOptimizer answers the question monitoring can't: **"what happens to
everything else when this thing fails?"** Monitoring watches components;
outages come from the relationships between them. The product maps what
cloud services *actually* depend on, scores how dangerous each dependency
concentration is, validates those predictions against real controlled
failure, and prices the fixes.

The core invention is **CEI (Criticality-Entropy Index)**:
`CEI = α·centrality + β·entropy + γ·risk`, patent pending
(USPTO App. No. 19/641,446). Blast-radius mode runs PageRank toward
dependents; graph edges mean "source depends on target", so blast radius
walks ancestors (the things that break when you break).

Two audiences, deliberately:

- **Customers** — sign up at `/`, install a read-only agent in their
  cluster, get a live dashboard of blast radius, drift, diagnosis, and
  dollar-priced prescriptions.
- **NIW reviewers** — the owner's National Interest Waiver immigration
  application references this product. `/niw` is the reviewer-facing surface
  (5 canned scenarios, patent mapping, validation evidence). **It must never
  break or change output** — 32 golden tests pin scenario analysis results
  byte-identical.

## 2. The headline numbers (all measured, not claimed)

- Blast-radius predictions vs measured impact under real pod-kill chaos:
  **Spearman 0.98, recall 1.0** (docs/CHAOS-VALIDATION.md; run on a 3-node
  kind cluster `cei-test`).
- Diagnosis engine validated against injected ground-truth faults on the
  demo estate — correct root identified in every seeded case.
- Demo estate ("production-sim", 12 services): every planted flaw is found
  by the intended panel — CEI tops `orders-db` (0.73, hub with no PDB);
  ownership flags `legacy-ledger` (:latest, ownerless); 21 resilience
  findings incl. a drain-blocking PDB (`session-cache` minAvailable ==
  replicas); prescriptions price `api-gateway` +1 replica at net +$328k/yr
  and an `orders-db` split at net +$182k/yr.
- Test ledger: core-engine **631 passed / 46 skipped / 0 failed** (incl. 32
  goldens), frontend **17/17**, page/API sweep 16/16, homepage TTFB ~6ms
  through the local stack.

## 3. Architecture

```
customer cluster                          platform (Railway in prod / minikube locally)
┌──────────────────────┐   HTTPS    ┌─────────────────────────────────────┐
│ cloudoptimizer-agent │ ─────────► │ core-engine (FastAPI)               │
│ (read-only RBAC:     │  /v1/ingest│  ├─ ingest → snapshot → drift rail  │
│  get/list/watch)     │   60s      │  ├─ analysis engines (services/)    │
└──────────────────────┘            │  ├─ /v1 dashboard+auth API          │
                                    │  ├─ /api compat (NIW + local cloud) │
                                    │  └─ GitHub App → remediation PRs    │
                                    │ Postgres                            │
                                    │ frontend (Next.js 14)               │
                                    └─────────────────────────────────────┘
```

Key principle: **the agent is read-only forever**. It collects topology
(workloads, services, pods, PDBs, HPAs, probes, annotations, env-reference
edges, ingress edges), redacts env values in-process (`agent` redact.py),
and ships ~34KB snapshots. All action (PRs with deterministic manifest
edits) happens from the platform side via a GitHub App — never via cluster
write access. This split was an explicit product decision when PR
remediation was added: the *application* may act, the *agent* may not.

### Analysis engines (core-engine/src/services/)

| Engine | What it answers |
|---|---|
| blast_radius | who breaks when X breaks (directed PageRank; chaos-validated) |
| diagnose | live incident root cause: unhealthy-subgraph frontier = root candidates; OOM/ImagePull are self-caused; CrashLoop ambiguous unless deps healthy; onset ordering breaks cycles; ≥2 roots sharing an external endpoint promotes it to prime suspect; recent-change diff on roots only. `/v1/clusters/{id}/diagnose?format=markdown` produces an incident brief |
| drift + drift_store | every snapshot compared to its predecessor at ingest (the "drift rail"), drift_events table, 24h debounce, critical-only Slack |
| prescribe | risk in dollars (Monte-Carlo availability × downtime cost rate), interventions as graph transforms, Δrisk/$ ranking, greedy set selection under budget; `structural_health` composite |
| resilience | PDB/HPA/probe/replica audits (incl. drain-blocking PDBs, CPU-only HPAs) |
| safe_to_delete | verdict + evidence before removing a workload |
| ownership | ownerless/unlabeled critical dependencies |
| external_deps | dependencies *below* the cluster (endpoints outside), DCI (Dependency Concentration Index) |
| pr_review / manifest_diff / graph_simulation | pre-merge CEI on the implied post-merge graph (PR-time blast-radius check) |
| chaos / recovery | controlled-failure validation, recovery-curve measurement, metastability detection, pre-scale playbooks |
| availability / fleet / control_plane | correlated-failure MC availability, cross-cluster convergence, control-plane + GPU fragmentation audits |
| carbon / cost / complexity | carbon accounting, cost safety, config-entropy index |
| remediation / fix | findings → deterministic YAML edits → GitHub App PRs |

### Routes that matter

- `/v1/auth/signup|login` (12-char min passwords), `/v1/apikeys` (hashed,
  shown once), `/v1/clusters/*` (dashboard data), `/v1/ingest` (agent).
- `/api/demo/*` — pure aliases of the `/scenarios` handlers (NIW surface;
  numbers can't diverge, pinned by test).
- `/api/cloud/*` — legit mock (MOCK badge) of AWS/GCP/Azure topology with
  the real analysis pipeline, for local demos.
- Scenario list returns `{scenarios: [...]}` with key `scenario_id`.

### Frontend (Next.js 14, pages router, inline styles)

- `/` — product landing (dual-audience: every section leads business, then
  technical). Hero "Every dashboard was green. Then one service took down
  forty.", Sign up / Log in CTAs with instructions, two inline SVG diagrams,
  6 benefit cards, 0.98 / 100% / read-only proof strip, **For NIW
  Reviewers →** button in header and footer.
- `/niw` — the old landing page moved verbatim (CEI dashboard, validated
  recommendations, run-analysis, governance, dependency-graph tabs) plus a
  back-home link. `/NIW` 307s to it via `src/middleware.js` — NOT via
  next.config redirects(), which match case-insensitively and self-loop.
- `/app` — signup/onboarding (3-step empty state, per-cluster helm install
  hint, key reveal with `--set endpoint=...`), `?mode=login` preselects
  sign-in. `/app/[clusterId]` — the live dashboard.
- NIW sub-pages: `/demo`, `/national-interest`, `/upload`, `/connect`.

## 4. Local development

Two paths (docs/DEV.md):

- **docker compose** (fast inner loop): project name pinned
  `cloudoptimizer-dev` (an unpinned name once collided and deleted the test
  DB). Postgres container `co-postgres`.
- **Full stack**: `./deploy.sh` — minikube (profile default, 2cpu/4g),
  terraform apply from `deploy/local/` (its `.gitignore` is **user-managed,
  never touch**; tfstate must never be committed — it records secret
  values), images built INTO minikube. Then:
  - `./deploy.sh open` — self-healing port-forward loops (3000/8000) with a
    READY announcer; NodePorts don't work on macOS docker driver.
  - `./deploy.sh demo-cluster` — applies the 12-service estate.
  - `./deploy.sh agent <API_KEY>` — installs the Helm agent pointing at the
    in-cluster core-engine.
  - Preflight warns when other kind/k3d clusters are running (they starve
    minikube's CPU — this was the "website is really slow" root cause).

Full customer walkthrough that works today: sign up at `localhost:3000` →
create API key → `./deploy.sh agent <key>` → dashboard fills in ~1 minute
with live demo-estate data.

## 5. Debugging lessons already paid for (do not relearn)

1. K8s omits `readyReplicas` when 0 are ready — treat missing-with-status as
   0 or chaos measures nothing (collector + chaos `_ready_state`).
2. Two module-scoped FastAPI TestClients bind asyncpg to different event
   loops → session-scoped client fixture in conftest.
3. Migrate BOTH dev and test databases when adding a migration.
4. exec readiness probes time out at pod density (even literal `true`);
   use httpGet + a healthz-file loop. `sh -c` without `exec` ignores
   SIGTERM and wedges namespace deletion for ~20 min.
5. `kubectl port-forward` pins one pod; every rollout kills it → all
   forwards live in reconnect while-loops.
6. exec liveness probe default 1s timeout produced 7,124 false failures on
   a healthy postgres under CPU load → `timeoutSeconds: 5` everywhere.
7. `cmd | tail -1` hides the real exit code; capture `rc=$?` when logging
   builds.
8. Next.js `redirects()` is case-insensitive: a `/NIW` source matches
   `/niw` and loops forever. Exact-match middleware instead.
9. Clean-venv dependency guard catches requirements.txt omissions (PyYAML
   incident) before they crash a deploy.
10. `NEXT_PUBLIC_*` bakes at build; changing the API origin means
    rebuilding the frontend image, not just restarting it.

## 6. Security invariants (verified, keep verified)

- Git history contains zero `.env`, `*.pem`, `*.tfstate*` files.
- Agent ClusterRole: get/list/watch only; no Secret reads; no ConfigMap
  bodies; env var values redacted in-process before shipping.
- API keys: stored as hash, displayed once at creation.
- PR remediation never echoes Secret/ConfigMap values into PR bodies.
- Password policy: 12+ chars.

## 7. Branch and repo state

- Remote: `github.com/prawalpokharel/CEI-Cloud-Governance-Framework`
- `main` — pre-expansion baseline. **Do not merge into it unless asked.**
- `feat/cloud-expansion` — earlier phase (contained in iverson).
- `feat/iverson` — **the live branch**; everything above is on it.
- `spikes/` holds throwaway validation scripts; `backend/` is the older
  service kept for reference (core-engine is the real one).
- Local test clusters (not in repo): kind `cei-test` (chaos validation,
  3 nodes), `cascade-testbed`, `co-spike` — currently `docker stop`ped to
  free CPU; `docker start <name>-control-plane ...` revives them.

## 8. Where to go next (owner's standing direction + open threads)

- Backlog and explicit skips-with-reasons: docs/PLAN-SEVEN-PROBLEMS.md
  (12 modern-cloud problems; phases A–C built).
- LinkedIn/pitch material: docs/WHY-CLOUDOPTIMIZER.md (eight answers).
- Deploy the new landing + niw split to production (Railway) when asked —
  everything is verified locally.
- The user cares about: nothing broken (especially NIW), professional
  polish, measurable validation, and a working stranger-onboarding path.
  Every claim shown to users must trace to a measured number.
