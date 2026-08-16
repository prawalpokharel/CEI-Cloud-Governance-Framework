# Backlog

Single tracked list: decisions waiting on you, what was skipped and why, bugs,
gaps, and debt.

Last updated: Phases 1–3 complete; 5–7 partially built; 4, 6.5, 8 not started.

---

## 1 · Decisions waiting on you

These change what gets built. None is blocked on engineering.

| # | Decision | Why it matters |
|---|---|---|
| **D1** | **Centrality mode** — `blast_radius` (default) or `structural` | Switcher is live in the dashboard. On Online Boutique they rank `productcatalogservice` vs `frontend` first. This underpins the "5 that can take your system down" claim, vulnerability priority, alert filtering, and the automation gate — one setting, four features. |
| **D2** | **Derived instance types in scenarios** | `smallest_fitting_instance()` infers cost from `cpu_limit`/`mem_gb`, which the scenario files never declare. Keep, or write explicit `instance_type` into the topology JSONs? Affects NIW-facing figures. |
| **D3** | **npm lockfiles** | Untracked. Committing pins versions Railway currently resolves freely. |
| **D4** | **Setting `DATABASE_URL` opens public signup** | `/v1/auth/signup` goes live the moment the variable is set. Options in `SETUP.md`: leave unset, run the product API as a second Railway service, or add an invite gate (~1h). |
| **D5** | **Does the product get write access to customer repos?** | Blocks Phase 4 entirely. See §2. |
| **D6** | **Does the agent get write RBAC?** | Blocks Phase 7 execution. Currently `get/list/watch` only, which is the whole trust story. |
| **D7** | **Auto-apply defaults** | The engine ships with `auto_apply_enabled=False` and `has_tests=False`, so nothing is ever applied automatically today. Confirm that stays the default at GA. |
| **D8** | **Log platform for Phase 6.5** | ClickHouse vs OpenSearch. Adds a second datastore and materially changes hosting cost. |

---

## 2 · Skipped, and why

Not stubbed, not half-built. Each has a real blocker.

### Phase 4 — Fix with AI · **not started**

Needs, all of which are yours to grant:

- A GitHub/GitLab App with write access to customer repositories
- An Anthropic API key for fix generation
- Sandboxed test execution — untrusted customer code runs somewhere, and
  choosing where is a security decision (isolated runner, ephemeral container,
  network-isolated namespace)

The *decision* surface is already built and tested: `services/policy.py`
decides what may be auto-applied, PR'd, or only alerted on, and it dry-runs
against real findings today. What is missing is execution, which is
deliberately the part that touches a credential capable of changing a
customer's system.

**Recommendation:** build behind a per-customer opt-in, PR-only in v1 (no
direct commits), with branch, commit author, and PR body all attributable to
the product rather than to a person.

### Phase 5 — CSPM and cloud account connect · **partial**

Built: IaC misconfiguration scanning (Terraform, CloudFormation, Kubernetes,
Helm, Dockerfiles) via Trivy config.

Skipped: CSPM checks (public buckets, over-permissioned IAM, open security
groups) and the cloud-account connect flow. Both need real cloud credentials —
AWS cross-account role + External ID, Azure multi-tenant consent, GCP service
account. The check logic is writable without them; the value is not
demonstrable without them, and untested cloud-permission code is worse than
none.

The existing Express OAuth scaffolding is **not** what Phase 5 describes and
should be deleted rather than extended (T4).

### Phase 6 — Egress traffic analysis · **partial**

Built: segmentation coverage analysis and NetworkPolicy generation from the
dependency graph.

Skipped: egress traffic analysis — "flag pods communicating with unexpected
external destinations". That needs flow data: eBPF (Cilium Hubble), a service
mesh, or VPC flow logs. All three are substantial infrastructure decisions and
none is inferable from the Kubernetes API.

This is also why generated NetworkPolicies are audit-first rather than
enforce-first. Real flow data would close that gap and is the single
highest-value addition to this phase.

### Phase 6.5 — Observability Lite · **not started**

Needs a second datastore (ClickHouse or OpenSearch) plus Fluent Bit in the
chart. Largest infrastructure addition in the roadmap and the one least
related to what exists — every other phase reuses the CEI graph; log storage
does not.

The one piece that *does* reuse it — CEI-correlated alerting, where anomalies
on high-centrality workloads page and the same anomaly on an idle pod does not
— is already implemented in `services/notify.py` and applies to health
findings today. Pointing it at logs is small once logs exist.

### Phase 8 — SAST · **not started**

Semgrep integrates the way Trivy did, so the scanner pattern is proven.
Blocked on the same thing as Phase 4: access to customer source code. Sequence
after Phase 4, since both need the Git integration and it should be built once.

---

## 3 · Bugs

| # | Item | Status |
|---|---|---|
| B1 | `detect_provider()` may not recognise every managed offering | Unverified. Cosmetic but visible. Confirm during multi-cloud validation. |
| B2 | Rollback manager (Module 112) keeps snapshots in a process dict | **Deferred deliberately.** Nothing writes to a cluster, so there is nothing to roll back; it matters at Phase 7 execution. Migrating now would make `/rollback/*` require `DATABASE_URL`, which it does not today. |
| B3 | `captured_at` migration is one-way once data exists | **By design, now explained.** Duplicate `seq` values are expected under the new schema, so the older stricter constraint cannot be restored. The downgrade says so instead of raising an opaque IntegrityError. Clean round-trip still verified in CI. |

### Fixed, recorded so they are not reintroduced

**Engine correctness**
- Scenario telemetry never reached the pipeline (path mismatch + `mem` vs `memory`)
- Oscillation detector was amplitude-blind, flagging 100% of nodes forever
- k-hop safety check compared an unbounded sum against a ratio → savings always $0
- Cross-request state leak: β decayed 0.27 → 0.09 over four identical calls
- Response reported `beta: 0.35` while scoring with `beta: 0`

**Data integrity**
- Registering one cluster twice caused silent permanent data loss (HTTP 200, nothing stored)
- Restarted agents went permanently silent (`seq` resets, idempotency keyed on it)
- Agent reported requests per-pod but usage fleet-wide → 3-replica workloads looked 3× better utilized, and would have understated cost by the replica count
- Test suite wiped the dev database (guard added)

**Analysis quality**
- Cost allocation could exceed the cluster bill — 108% waste, $9,728 against $8,970
- CrashLoopBackOff detection keyed on a transient state, missing most real crash loops
- Top-risk list showed one OpenSSL CVE three times, filling "the 5 that matter" with one problem
- `connected` meant "reported once, ever" — a dead agent showed as healthy
- Server silently overrode the operator's configured poll interval
- Two divergent "system namespace" lists across modules

---

## 4 · Gaps

| # | Item | Note |
|---|---|---|
| G1 | Agent image not published | `git tag agent-v0.1.0 && git push origin agent-v0.1.0` |
| G2 | Scanner image not published | Built and verified locally (333 MB, Trivy 0.58.0, non-root). Needs a release job like the agent's. |
| G3 | Multi-cloud validation not run | Yours — runbook at `docs/VALIDATION.md` |
| G4 | Schedulers not wired | `prune`, `report`, and `alert` all work and are verified. Need cron. |
| G5 | SMTP and Slack webhook unconfigured | Both degrade to "not delivered" and log. Set `SMTP_HOST` / `SLACK_WEBHOOK_URL`. |
| G6 | Email verification never set | Deliberate; needs an email provider. |
| G7 | `APP_SECRET_KEY` required for `/app` | Deploy config, documented in `SETUP.md`. |
| G8 | IaC scanning not wired to a source | The scanner function exists and is tested; nothing checks out a repo to point it at. Lands with the Phase 4 Git integration. |
| G9 | Network and remediation views not in the UI | Both APIs are live (`/network`, `/remediation`) and in the sandbox. No dashboard panels yet. |

---

## 5 · Debt

| # | Item | Note |
|---|---|---|
| T1 | Duplicate `scenarios/` tree (~120k lines twice) | Load-bearing: Railway deploys the `core-engine` subtree and `loader.py`'s path fallback depends on it. Confirm the Railway root directory before touching. |
| T2 | `ClusterTopologyMap` is a copy of `D3DependencyGraph` | Deliberate while the NIW petition is live. Reconcile after. |
| T3 | Agent not open-sourced | Your call, after Phase 1 validation. Structured to split cleanly. |
| T4 | Express `/api/cloud/*` OAuth scaffolding | Gated off (404 unless `ENABLE_CLOUD_OAUTH=true`). Implements a mechanism Phase 5 does not use. Delete once `/connect` pages can change. |
| T5 | Frontend component tests | API client covered (8 tests); components are not. |
| T6 | Backend has only a syntax check | Low priority — it is frozen. |
| T7 | Scenario path still synthesizes history when none is supplied | Dormant: scenarios supply real telemetry, and the live path never uses it. |

---

## 6 · Where things stand

| Phase | Status |
|---|---|
| 1 — Agent, topology, CEI | Complete. Multi-cloud validation outstanding (G3). |
| 2 — Waste in $, health, demo mode | Complete. |
| 3 — Vulnerability scanning, reports, Slack | Complete. |
| 4 — Fix with AI | Not started. Decision surface built; execution blocked on D5. |
| 5 — IaC + CSPM + cloud connect | IaC done. CSPM and cloud connect blocked on credentials. |
| 6 — Network layer | Segmentation and policy generation done. Egress analysis needs flow data. |
| 6.5 — Observability Lite | Not started. Blocked on D8. |
| 7 — Write mode | Policy engine done and tested. Execution blocked on D6. |
| 8 — SAST | Not started. Sequence after Phase 4. |

**206 tests** — 153 core-engine, 45 agent, 8 frontend.
