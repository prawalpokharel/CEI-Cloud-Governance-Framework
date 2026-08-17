# Local development environment

Run the full product on your machine — API, dashboard, database — and feed it
from a real local cluster, before anything touches production.

```bash
docker compose -f docker-compose.dev.yml up --build
```

| Service | Where | Notes |
|---|---|---|
| Dashboard | http://localhost:3000 | Next.js production build |
| API | http://localhost:8000 | migrations run automatically on start |
| API docs | http://localhost:8000/docs | interactive OpenAPI |
| Postgres | localhost:55433 | `cloudopt` / `devpass` / `cloudoptimizer` |

Port 55433 deliberately avoids 55432, which the test suite uses for its own
database — you can run the dev stack and the tests at the same time.

## First run

1. Open http://localhost:3000, sign up (any email — it is your local
   database), and create a cluster. **Copy the API key from the response —
   it is shown exactly once.**
2. Feed it from a local kind cluster with the agent, no image build needed:

```bash
cd agent
python -m venv .venv && .venv/bin/pip install -r requirements.txt
CLOUDOPTIMIZER_ENDPOINT=http://localhost:8000 \
CLOUDOPTIMIZER_API_KEY=<the key from step 1> \
.venv/bin/python -m cloudoptimizer_agent
```

The agent uses your current kubeconfig context. Within a minute the
dashboard shows topology, CEI, health — and from the second snapshot on, the
drift rail runs on every ingest.

3. Optional integrations activate from your shell environment (see the
   `${VAR:-}` entries in the compose file): export `GITHUB_APP_*` for
   remediation PRs and the PR blast-radius check, `SLACK_WEBHOOK_URL` for
   drift notifications, `GEMINI_API_KEY` for fix-with-AI. Absent, each
   feature reports itself unconfigured instead of failing.

## Alternative: Kubernetes-native local deploy (minikube + Terraform + DevSpace)

For a local environment that mirrors a real cluster deployment rather than
compose:

```bash
./deploy.sh
```

One command: starts minikube, builds both images directly into its container
runtime (`imagePullPolicy: Never` guarantees the local build is what runs),
applies [deploy/local/main.tf](../deploy/local/main.tf), waits for readiness,
prints URLs. Then iterate with live code sync:

```bash
devspace dev
```

`./deploy.sh status` shows what is running; `./deploy.sh destroy` tears the
stack down (minikube itself stays). Terraform owns *what* runs, DevSpace owns
the *loop* — the same split a production GitOps setup has, which is the point
of practicing it locally.

## What is dev-grade here, on purpose

Fixed database credentials, a fixed `APP_SECRET_KEY`, no TLS. **None of
these values may travel to production.** Production configuration lives on
the platform (Railway) as real secrets; this repo contains no production
credential and the person deploying never needs to share one.

## From local to production

The path is deliberately short:

1. **Local**: this compose file. Iterate here; the container runs the same
   `uvicorn src.main:app` the `Procfile` runs in production, and migrations
   run the same `alembic upgrade head`.
2. **Verify**: `pytest` in `core-engine/` and `agent/` (the API suite runs
   against a real Postgres), `npm test` in `frontend/`. The NIW golden files
   must be unchanged: `python -m tests.regen_golden` reporting `changed=0`.
3. **Production (Railway)**: push the branch, let CI pass, merge. Railway
   deploys from `main` with `DATABASE_URL`, `APP_SECRET_KEY`, and the
   integration secrets set in its dashboard — the only difference from your
   local stack is who holds the secrets.

Agent images for customer clusters are a separate release channel:
`git tag release-vX.Y.Z && git push origin release-vX.Y.Z` publishes
multi-arch images to ghcr.io with a manifest check and a runtime smoke test
(`.github/workflows/release-images.yml`).
