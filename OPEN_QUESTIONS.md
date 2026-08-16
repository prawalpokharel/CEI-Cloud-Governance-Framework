# Open questions & known gaps

Things that are inconsistent, incorrect, or need a product decision. Macro
work continues around these; none of them block Week 3.

Status key: **[opinion]** needs your call · **[gap]** known, scheduled ·
**[debt]** works, wants cleanup

---

## Needs your opinion

### 1. Rightsizing marks nearly every workload **[opinion]**

`RecommendationActuator` treats a node as a rightsizing candidate when
`cei_score < 0.70 and risk_factor < 0.7 and monthly_cost > 0`. Most nodes
satisfy that, so `cloud_microservices` returns `rightsize` for 15 of 15
workloads at a flat 15% of spend. That is not an analysis; it is "15% of your
bill" with extra steps.

Eligibility keys on CEI — a measure of *criticality* — when the question is
*utilization headroom*. A low-CEI workload can be running hot; a high-CEI one
can be idle.

The live cluster already shows what the right basis looks like: 86–98% unused
CPU per workload, measured. Roadmap Phase 2 specifies exactly this
("requested-vs-used per pod"), so the cleanest path is to leave the current
heuristic as an acknowledged placeholder and build real rightsizing on
measured headroom in Phase 2.

**Decision:** replace now, or let Phase 2 do it?

### 2. What does centrality mean? **[opinion]**

On Online Boutique, `frontend` ranks #1. It has in-degree 1 and out-degree 7,
so PageRank says low and degree/betweenness say high; the current composite
(0.35 PageRank / 0.30 betweenness / 0.20 degree / 0.15 closeness) happens to
favour the latter.

Both readings are defensible:

- **Internal blast radius** — frontend fails, no other service breaks → rank low
- **User-facing importance** — frontend fails, total outage → rank first

This is the claim the whole wedge rests on ("the 5 that can take your system
down"), so it should be chosen deliberately. Needed before CEI tuning in Week 3.

### 3. Scenario instance types are now derived, not declared **[opinion]**

Scenario topologies declare `cpu_limit` / `mem_gb` but no instance type, so
savings were structurally $0. `smallest_fitting_instance()` now picks the
cheapest instance satisfying the declared requirement, which is what makes
`cloud_microservices` price at $11,267/mo with $1,690/mo identified.

That is an inference the scenario files do not state. It is documented and
deterministic, but if the NIW submission describes these scenarios as having
specific cost characteristics, you may prefer to write explicit
`instance_type` values into the topology JSONs instead.

**Decision:** keep the derivation, or declare instance types explicitly?

### 4. npm lockfiles are untracked **[opinion — you said flag it]**

`npm install` generated `backend/package-lock.json` and
`frontend/package-lock.json`. Committing them pins versions Railway currently
resolves freely. Normally you want them committed; it is a deploy-affecting
change nobody has validated against your environment.

---

## Known gaps, scheduled

### 5. CEI on live clusters **[done — Week 3]**

`GET /v1/clusters/{id}/cei` computes CEI over the latest snapshot, and the
dashboard colours and sizes the map by it. Centrality mode is switchable in
the UI, so decision #2 can be made by looking at real output rather than
reasoning about it.

### 6. Entropy from real history **[done — Week 3]**

Live CEI reads accumulated `workload_samples`. Below 30 samples the entropy
term is withheld and beta redistributed across alpha and gamma, and the
response reports the effective weights actually used. Nothing is fabricated on
the live path.

Note the scenario path still synthesizes history when none is supplied — that
fallback is what the NIW goldens are pinned against, so it stays until you
decide otherwise.

### 7. Snapshot retention is documented but not enforced **[gap]**

Schema comments say ~24h; no job prunes them. At 60s intervals one cluster
writes ~1,440 snapshots/day. Needs a scheduled delete before any real fleet.

### 8. The agent image is not published yet **[gap — one command]**

`.github/workflows/release-agent.yml` builds and pushes multi-arch to ghcr.io
and fails if either architecture is missing from the manifest. Both platforms
verified building locally. Publish with:

```
git tag agent-v0.1.0 && git push origin agent-v0.1.0
```

Until then the chart's default `image.repository` points at an image that does
not exist.

### 9. Rollback manager is still in-process **[gap]**

Module 112 keeps snapshots in a dict that dies on restart and does not work
across replicas. Postgres tables exist; migrating it was not in Weeks 1–2.

### 10. Email verification is not implemented **[gap]**

`users.email_verified_at` exists and is never set. Deliberate — the GTM target
is signup-to-dashboard in under 10 minutes, so verification should be
asynchronous, not a gate. Needs an email provider.

### 11. `APP_SECRET_KEY` is required for /app **[gap — deploy config]**

Dashboard auth returns 503 until it is set (32+ chars). Deliberately lazy so
the scenario endpoints keep working without it.

---

### 16. Test suite wiped the dev database **[resolved, worth knowing]**

`test_db_schema.py` deletes every Tenant, which cascades to clusters, users,
and API keys. The file documented "use a separate database" from the start —
and within the hour both variables were pointed at the dev database, silently
destroying registered clusters and leaving a running agent authenticating with
keys that no longer existed. The agent reported `401 Invalid API key`, which
looked like an agent bug and was not.

The suite now refuses to start when `TEST_DATABASE_URL` equals
`DATABASE_URL`, and CI creates a throwaway `cloudoptimizer_test` database.
Noted because the lesson generalises: a comment is not a safeguard.

### 17. Setting DATABASE_URL opens public signup **[opinion]**

The product API mounts only when `DATABASE_URL` is present, so setting it on
Railway makes `/v1/auth/signup` live to the internet — anyone can create an
account and mint agent API keys. That is the intended self-serve shape, but it
happens the moment the variable is set, not when you decide you are ready.

Options in `SETUP.md`: leave it unset and keep developing locally, deploy the
product API as a separate Railway service so the NIW evidence surface is
untouched by product deploys, or add an invite-code gate first (~1 hour).

---

## Debt

### 12. Duplicate `scenarios/` tree **[debt]**

Repo root and `core-engine/` hold byte-identical copies (~120k lines twice).
The duplicate is load-bearing: Railway deploys the `core-engine` subtree, and
`loader.py`'s path fallback depends on it. Deduplicating requires changing the
Railway root directory first — confirm that setting before touching it.

### 13. `/api/cloud/*` is unauthenticated **[debt]**

The Express backend's OAuth routes, including the callback and token store,
have no auth. Currently mock-mode only, so no real credentials are at risk.
Roadmap Phase 5 replaces this mechanism entirely with cross-account IAM roles,
so the work is throwaway — but it should be gated or removed rather than left
open.

### 14. `ClusterTopologyMap` is a copy of `D3DependencyGraph` **[debt]**

Deliberate: the original renders the NIW evidence pages and should not be one
refactor away from a regression while the petition is live. Reconcile once the
petition clears.

### 15. Agent is not yet open-sourced **[debt — your call, after Phase 1]**

`agent/` is structured to split out cleanly. The trust story is already
testable: read-only RBAC with no secrets/configmaps, values-never-transmitted
verified by canary test, `--dry-run` to inspect payloads before sending.
