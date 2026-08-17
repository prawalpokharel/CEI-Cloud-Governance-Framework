# Backlog

Single tracked list: decisions waiting on you, what was skipped and why, bugs,
gaps, and debt.

Last updated: Phases 1–4 complete; 5–7 partially built; 6.5 and 8 not started.

---

## 1 · Decisions waiting on you

These change what gets built. None is blocked on engineering.

| # | Decision | Why it matters |
|---|---|---|
| **D1** | **Centrality mode** — `blast_radius` (default) or `structural` | Switcher is live in the dashboard. On Online Boutique they rank `productcatalogservice` vs `frontend` first. This underpins the "5 that can take your system down" claim, vulnerability priority, alert filtering, and the automation gate — one setting, four features. |
| **D2** | **Derived instance types in scenarios** | `smallest_fitting_instance()` infers cost from `cpu_limit`/`mem_gb`, which the scenario files never declare. Keep, or write explicit `instance_type` into the topology JSONs? Affects NIW-facing figures. |
| **D3** | **npm lockfiles** | Untracked. Committing pins versions Railway currently resolves freely. |
| **D4** | **Setting `DATABASE_URL` opens public signup** | `/v1/auth/signup` goes live the moment the variable is set. Options in `SETUP.md`: leave unset, run the product API as a second Railway service, or add an invite gate (~1h). |
| **D5** | **Open a live pull request?** | The whole chain is verified against your real repo in dry run. Opening one for real is an outward-facing write to a public repository — one command away, but yours to authorize. |
| **D6** | **Does the agent get write RBAC?** | Blocks Phase 7 execution. Currently `get/list/watch` only, which is the whole trust story. |
| **D7** | **Auto-apply defaults** | The engine ships with `auto_apply_enabled=False` and `has_tests=False`, so nothing is ever applied automatically today. Confirm that stays the default at GA. |
| **D8** | **Log platform for Phase 6.5** | ClickHouse vs OpenSearch. Adds a second datastore and materially changes hosting cost. |
| **D9** | **Rotate the GitHub App private key** | The PEM was pasted into a chat transcript. Nothing depends on that specific key. |
| **D10** | **Pin the Gemini model or track an alias** | `gemini-3.7-flash` is pinned now. `gemini-flash-latest` auto-tracks but changes fix quality under you without notice. |

---

## 2 · Skipped, and why

Not stubbed, not half-built. Each has a real blocker.

### Phase 4 — Fix with AI · **built, one authorization from live**

Verified end to end against `prawalpokharel/CEI-Cloud-Governance-Framework`
with real GitHub and real Gemini calls, stopping short of the push.

Still outstanding:

- **Sandboxed test execution.** `has_tests` is hardcoded False, so nothing
  can reach `auto_apply` regardless of settings. Running customer test suites
  needs an isolated runner, and choosing where is a security decision.
- **Base-image rebase PRs.** OS-package findings — the majority of image
  vulnerabilities — are correctly routed away from manifest edits, but
  choosing the replacement tag needs registry queries not yet implemented.
- **GitLab.** Only GitHub is implemented.

### Phase 5 — CSPM · **Azure built and live; AWS and GCP not started**

Built: IaC misconfiguration scanning via Trivy config, and **Azure CSPM** —
public blob access, HTTP-permitting storage, weak TLS, unrestricted storage
networking, NSG rules exposing administrative ports to the internet, and
subscription-scoped privileged role assignments.

Authenticates with `DefaultAzureCredential`: the operator's `az login` in
development, a managed identity in production, no client secret either way.
Verified live against a real subscription, which produced a genuine finding
(2 principals holding Owner at subscription scope).

The Azure SDK lives in `requirements-cloud.txt`, not the base requirements —
~40 MB a Kubernetes-only deployment never needs. The module degrades to
"unavailable" when it is absent.

Not started: AWS and GCP equivalents, and the customer-facing cloud-account
connect flow (cross-account role + External ID).

The existing Express OAuth scaffolding is **not** what Phase 5 describes and
should be deleted rather than extended (T4).

### Phase 6 — Egress traffic analysis · **schema verified, behaviour unvalidated**

Built: segmentation analysis, NetworkPolicy generation, and egress analysis
over Hubble flow data — flagging denied egress, administrative ports leaving
the cluster, bare-IP destinations with no DNS, and destinations only one
workload reaches. Ranked by CEI.

**Not validated against a live Hubble.** Cilium was installed on the local
`cei-test` cluster and could not start:

    failed to retrieve qdisc list of link eth0: operation not supported

Docker Desktop's linuxkit kernel (6.5.11) has no traffic-control subsystem —
`tc qdisc show` itself fails — so Cilium's eBPF datapath cannot attach. This
is not a configuration problem and no Cilium setting works around it. The
cluster was restored to kindnet afterwards.

**Schema verified against upstream Cilium.** Every field name was checked
against `api/v1/flow/flow.proto` and every reserved identity against
`pkg/datapath/types/types_generated.go`. That check found a real defect:
dual-stack clusters use identities 9 and 10 for the internet, not 2, and the
original code matched them only by accident through a fallback. Cilium's
Ingress identity (8) was also being counted as an external destination.

What remains unvalidated is behaviour under real traffic — whether a
90-second window is wide enough, and how the CLI behaves under load.
**Run it on a real Linux cluster before relying on the numbers.**

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
| G2 | Images not published | `release-images.yml` publishes both agent and scanner, multi-arch, verifying the manifest and that the entrypoint runs. Tag `release-v0.1.0` to fire it. |
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
| 4 — Fix with AI | Built and verified end to end against the real repo and Gemini. Live PR pending D5. |
| 5 — IaC + CSPM + cloud connect | IaC done. CSPM and cloud connect blocked on credentials. |
| 6 — Network layer | Segmentation and policy generation done. Egress analysis needs flow data. |
| 6.5 — Observability Lite | Not started. Blocked on D8. |
| 7 — Write mode | Policy engine done and tested. Execution blocked on D6. |
| 8 — SAST | Not started. Sequence after Phase 4. |

**727 tests** — 599 core-engine, 111 agent, 17 frontend.
