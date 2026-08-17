# Setup

## Local development

```bash
docker compose up -d
cd core-engine && alembic upgrade head
DATABASE_URL=postgres://cloudopt:devpass@localhost:55432/cloudoptimizer_test alembic upgrade head
```

Two databases, because the schema tests delete every tenant — which cascades
to clusters, users, and API keys. The suite refuses to start if
`TEST_DATABASE_URL` matches `DATABASE_URL`.

Credentials here are deliberately trivial: the container binds to localhost
only, holds scratch data, and is meant to be thrown away
(`docker compose down -v`).

Run everything:

```bash
cd core-engine && .venv/bin/python -m pytest -q     # core engine
cd agent && .venv/bin/python -m pytest -q           # agent
cd frontend && npm test                             # api client
```

---

## Production (Railway)

**No database credentials are ever pasted, committed, or shared.** Railway
injects them by reference.

### 1. Add the Postgres service

In the Railway project: **New → Database → PostgreSQL**. Railway creates it
with its own generated credentials and exposes them as service variables.

### 2. Reference it from core-engine

On the **core-engine service only**, add a variable:

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

That `${{...}}` is a Railway reference, not a literal. The value resolves at
deploy time and never appears in the dashboard as text, in git, or anywhere
else. Rotating the database password requires no redeploy.

Do **not** set `DATABASE_URL` on the Express backend — it has no database
dependency and should keep none.

### 3. Generate the session secret

```bash
openssl rand -base64 48
```

Set the output as `APP_SECRET_KEY` on core-engine. Generate it yourself; it
does not need to be shared with anyone, including me. Anything under 32
characters is rejected at runtime.

### 4. Set the allowed origins

```
CORS_ALLOWED_ORIGINS = https://cloudoptimizer.app
```

Worth doing regardless of the rest: the demo pages call the core engine
directly for `/pricing/savings` and `/benchmark/hpa-vs-cei`, and the frontend
swallows the failure silently. If CORS is blocking those today, those panels
have been rendering empty with no error shown.

### 5. Run migrations

Migrations do **not** run automatically, and should not run on app startup —
with more than one replica, concurrent startups race each other.

Preferred: set the service's **pre-deploy command** to `alembic upgrade head`.
It runs once per deploy, before the new version takes traffic, and is
idempotent.

Alternative, from your machine without ever seeing the credentials:

```bash
railway run --service core-engine alembic upgrade head
```

`railway run` injects the service's environment into a local process. The
values pass through your shell's memory, not your clipboard or your history.

### 6. Schedule retention

Snapshots accumulate at roughly 1,440 per cluster per day. Nothing prunes them
automatically — running a delete loop inside the web process would have every
replica doing it at once and competing with request handling for the
connection pool.

Schedule this daily (Railway cron, or any scheduler that can run a one-off
command against the service):

```bash
python -m src.cli prune
```

Check what it would remove first:

```bash
python -m src.cli prune --dry-run
```

Snapshots older than 24h and workload samples older than 30d are removed.
Each cluster's most recent snapshot is always kept regardless of age, so a
cluster whose agent went offline still renders its last known topology.

---

## Before you set DATABASE_URL, know what it turns on

The product API mounts only when `DATABASE_URL` is present. Setting it makes
these live on the public internet:

- `POST /v1/auth/signup` — **anyone can create an account**
- `POST /v1/auth/login`
- `POST /v1/clusters` — authenticated users can mint agent API keys
- `POST /v1/ingest` — agents can submit snapshots

That is the intended product surface, and it is the right shape for the
self-serve motion in the roadmap. But it is open signup from the moment you
set the variable. If you would rather not have that yet, three options:

1. **Leave `DATABASE_URL` unset** on the production core-engine and keep
   developing locally. The scenario and pricing endpoints backing the NIW
   evidence continue to work exactly as they do now; `/health` will report
   `product_api: disabled`.
2. **Deploy the product API as a second Railway service** off the same repo,
   on a different subdomain, with `DATABASE_URL` set there only. The NIW
   service stays untouched.
3. **Add a signup gate** — invite code or allowlist. Roughly an hour of work;
   say the word and it goes in before you flip the variable.

Option 2 is worth considering on its own merits: it keeps the USPTO/NIW
evidence surface on a service whose deploys are unrelated to product work,
which matters while the petition is live.

---

## What I should never be given

Not out of caution — these genuinely are not needed for the work:

- Production or staging database credentials
- `APP_SECRET_KEY` (generate it, set it, done)
- Railway account credentials or API tokens
- Cloud provider keys (AWS/Azure/GCP)
- Any real customer cluster's agent API key

Everything through Week 3 runs against the local throwaway database. If
something needs diagnosing against production later, `railway run` and
`railway logs` produce output you can paste — which is a redactable artifact,
unlike a credential.

## GitHub webhook (automatic pull-request analysis)

Without this, the blast-radius check runs only when something calls it. With
it, every pull request is analysed as it opens and re-analysed on each push.

**1. Set a webhook secret on the server.** Generate one and set it as
`GITHUB_WEBHOOK_SECRET`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

The endpoint returns 503 until this is set. That is deliberate: without a
secret it would be an unauthenticated remote trigger against your
infrastructure data, so it refuses rather than accepting unsigned payloads.

**2. Point the GitHub App's webhook at the server.**

- Payload URL: `https://<your-host>/v1/webhooks/github`
- Content type: `application/json`
- Secret: the value from step 1
- Events: **Pull requests** only

**3. Link the repository to the cluster it deploys to.**

```bash
curl -X PUT https://<your-host>/v1/clusters/<cluster-id>/repository \
  -H "Authorization: Bearer <session-token>" \
  -H "Content-Type: application/json" \
  -d '{"repository": "your-org/your-repo"}'
```

This link is what lets an inbound delivery find the right dependency graph.
Until it exists the webhook acknowledges deliveries and does nothing —
analysing a pull request against the wrong cluster would report impact
numbers that are confident and entirely fictional, which is worse than
reporting none.

The check is advisory by default: it posts a comment and a neutral check run,
and does not fail the build. A check that blocks merges on a heuristic gets
bypassed within a week and ignored afterwards.
