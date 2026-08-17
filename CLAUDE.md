# CloudOptimizer

Production K8s/cloud governance SaaS built around **CEI (Criticality-Entropy
Index)** — patent pending (USPTO App. No. 19/641,446). The owner's NIW
immigration application references this product: **the NIW demo surface must
never break** (see Hard constraints).

**Read [docs/HANDOFF.md](docs/HANDOFF.md) before making changes** — it holds
the full project history, architecture decisions, validation results, and the
debugging lessons already paid for.

## What it is (one paragraph)

A read-only agent runs in a customer's Kubernetes cluster, snapshots the
workload/service/dependency topology every 60s, and ships it to a FastAPI
core engine. The engine builds a dependency graph and answers: what breaks if
X fails (blast radius, chaos-validated at Spearman 0.98), what is quietly
becoming a single point of failure (CEI + drift), what an incident's root
cause is (diagnosis engine), and which architectural fix buys the most risk
reduction per dollar (prescriptions). The agent never writes to the cluster;
remediation happens as GitHub App PRs from the platform side.

## Layout

- `core-engine/` — FastAPI + Postgres. All analysis engines in
  `src/services/`, HTTP in `src/routers/` (auth+dashboard `/v1` in
  `app_api.py`; NIW/local compat `/api/*` in `compat.py`; agent ingest
  `/v1/ingest`). Tests: `cd core-engine && .venv/bin/python -m pytest -q`.
- `agent/` — the in-cluster collector (read-only RBAC: get/list/watch only).
- `frontend/` — Next.js 14 pages router. `/` product landing, `/niw` NIW
  surface (moved verbatim from old `/`), `/app` customer dashboard.
- `charts/cloudoptimizer-agent/` — customer install Helm chart.
- `deploy/` — terraform for local minikube (`deploy/local/.gitignore` is
  user-managed — do not touch), demo estate in `deploy/demo-cluster/`.
- `scenarios/` — NIW demo datasets; golden tests pin outputs byte-identical.
- `deploy.sh` — local stack: `deploy | open | agent <KEY> | demo-cluster |
  destroy | status`.
- `docs/` — HANDOFF.md (start here), DEV.md, CHAOS-VALIDATION.md,
  PLAN-SEVEN-PROBLEMS.md, WHY-CLOUDOPTIMIZER.md, GETTING-STARTED.md.

## Hard constraints (violating any of these is a serious failure)

1. NIW surface (`/niw`, `/demo`, `/national-interest`, `/upload`,
   `/connect`, the 5 scenarios, 31/32 golden files) stays intact and
   byte-identical. Run `pytest -k golden` after touching anything near it.
2. Never commit `.env`, `*.pem`, or any `*.tfstate*` (tfstate records
   secrets). History is verifiably clean; keep it that way.
3. Agent ClusterRole stays get/list/watch — no secrets, no configmap bodies,
   no write verbs. Env var *values* never leave the agent (redact.py).
4. API keys stored hashed, shown once. Secret/ConfigMap values never echoed
   into PR bodies or comments.
5. Work happens on `feat/iverson`; do not merge to `main` unless asked.

## Environment gotchas (each cost hours — details in HANDOFF.md)

- macOS + minikube docker driver: NodePorts unreachable from host — always
  `./deploy.sh open` (self-healing port-forward loops on 3000/8000).
- Other kind/k3d clusters starve minikube's CPU → "site is slow". Preflight
  warns; `docker stop <name>-control-plane ...` pauses them.
- Next.js `redirects()` matches case-insensitively — the `/NIW → /niw`
  redirect lives in `src/middleware.js` (exact match) for that reason.
- `NEXT_PUBLIC_*` is baked at build time; rebuild the frontend image after
  changing it. Images build INTO minikube (`minikube image build`), and the
  core-engine image builds from the REPO ROOT (`-f core-engine/Dockerfile .`)
  so `scenarios/` ships.
- Demo-estate pods use httpGet probes + a healthz-file loop and `exec httpd`
  — exec probes time out at density and `sh -c` without exec ignores SIGTERM.
