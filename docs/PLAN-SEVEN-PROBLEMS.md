# CloudOptimizer: the seven problems plan

**Thesis.** Modern cloud reliability tooling measures components; outages are a
property of the *relationships between* components. Every problem below is a
different face of the same blind spot — a dependency structure nobody can see —
and CloudOptimizer already owns the two assets that address it: a runtime
dependency graph observed from real clusters, and a validation method (chaos
correlation, Spearman 0.98) that proves the graph corresponds to how systems
actually fail. CEI is the scoring layer where ranking matters; several problems
below need the graph but not the score, and this plan says which is which.

**Why now.** October 2025 delivered the argument for us. A DNS failure on one
DynamoDB endpoint in us-east-1 cascaded across dozens of nominally independent
AWS services; nine days later Azure Front Door took down authentication and
routing for services that believed they were multi-region. Both events are
problems 1, 2, 5, and 6 below, at global scale, in the same month. The market
has just been taught that *component redundancy does not survive shared
dependencies* — and no incumbent ships a tool that shows a customer their own
version of that risk.

---

## Where each problem stands

| # | Problem | Foundation already built | Status |
|---|---|---|---|
| 1 | Hidden dependency concentration | external_deps.py: hidden coupling, convergence, Terraform join | **Extend** |
| 2 | Correlated failures | blast_radius.py + chaos validation; fault/propagation.py (patent 109) | **Extend** |
| 3 | Recovery-induced failures | Nothing. Greenfield | **Build** |
| 4 | Elasticity lag | autoscaler_at_ceiling detection; graph; no prediction | **Build** |
| 5 | Control-plane dependency | CSPM (Azure live); nothing separates planes | **Build** |
| 6 | Multi-cloud false independence | external_deps per cluster; multi-cluster tenancy in DB | **Extend** |
| 7 | Criticality ≠ utilization | CEI + safe_to_delete + chaos validation. This is the founding thesis | **Largely done — productize** |

---

## Problem 1 — Hidden dependency concentration

*Multi-AZ, multi-region, and still one IAM, one DNS zone, one CDN, one KMS.*

**Have:** `external_deps.py` finds N workloads converging on one external
endpoint, flags hidden coupling between workloads with no in-cluster path, and
joins observed egress with Terraform state. Category weights already encode
that identity and DNS fail everything at once.

**Build:**

1. **Dependency Concentration Index (DCI).** One number per cluster: the
   normalised HHI machinery from `graph_simulation.concentration()`, applied to
   the *combined* graph (workloads + external nodes), weighted by category.
   A cluster can hold internal concentration low and still score badly because
   everything terminates at one auth provider — which is exactly the number
   that should be bad. Trend it per snapshot; report it next to the topology.
2. **Infrastructure layers as nodes.** Egress sees applications' dependencies;
   it cannot see that every node pulls from one registry, every cert chains to
   one CA, every pod resolves through one upstream DNS forwarder. Sources that
   need no new access: CoreDNS ConfigMap reference (forwarders), node image
   registries (already collected per workload), Ingress TLS issuer annotations,
   StorageClass provisioners.
3. **"What would it take" queries.** Invert the question: for a target blast
   radius ("no single external failure reaches >30% of CEI mass"), report the
   minimum set of dependencies to diversify. This is min-cut over the combined
   graph — cheap, and it turns a finding into a work item.

**CEI role:** weighting. Convergence of 14 low-CEI batch jobs and 14 high-CEI
services are different findings. **Validation:** chaos — take the shared
dependency down (NetworkChaos partition to the endpoint, on a real Linux
cluster) and measure whether the "independent" workloads fail together as
predicted. Same protocol as CHAOS-VALIDATION.md, new failure mode.

---

## Problem 2 — Correlated failures

*Reliability math treats failures as independent; dependencies make them
cascade.*

**Have:** validated blast radius (recall 1.0 on live experiments), and patent
module 109's cascade propagation — currently driven by degree heuristics.

**Build:**

1. **Correlated availability model.** The industry computes availability as
   the product of independent component numbers; the graph shows which terms
   are *not* independent. Deliverable: "your architecture's effective
   availability is 99.2%, not the 99.95% your redundancy implies, because
   these 6 components share fate through these 3 dependencies." Monte Carlo
   over the graph: sample failures, propagate over edges (confidence as
   transmission probability), report the joint distribution. No new data
   needed.
2. **Calibrate cascade probability from chaos results.** We now have measured
   propagation events. Edge confidence (0.9 for env_reference) is a prior;
   every chaos experiment is evidence to update it per-edge-kind. This turns
   module 109 from a heuristic into a fitted model, and every customer chaos
   run makes it better — a data moat.
3. **Cascade depth in blast radius.** Report not just *what* fails but the
   expected *order and depth* (hop distance already computed) — which is what
   an incident commander actually wants during the event.

**CEI role:** central — this is the patent's home turf. **Validation:**
prediction intervals vs measured cascades; extend the harness to multi-hop
timing (the auth→api→web mid-propagation observation from the live run shows
the harness can already see cascade order).

---

## Problem 3 — Recovery-induced failures

*Failover itself creates reconnect storms, cache-miss storms, thundering
herds. Fixing the trigger doesn't fix the failure.*

The research frame is **metastable failure**: sustained overload maintained by
the system's own recovery behaviour (retries) after the trigger is gone. Over
half of studied metastable incidents are retry storms. Nobody ships a product
that predicts which *topologies* are metastable-prone. Greenfield, and the
strongest research contribution available to us.

**Build:**

1. **Recovery amplification score (static).** From the graph: when X recovers,
   how many dependents reconnect simultaneously, and what does the fan-in at X
   look like at time-of-recovery vs steady state? High fan-in + no observed
   backoff diversity + a stateful target (DB category from external_deps) =
   thundering-herd shape. Purely structural, computable today.
2. **Metastability detection signals.** From workload_samples: post-incident
   load that does not return to baseline after the trigger clears is the
   metastable signature. Detect "recovered but load did not," which is
   invisible to health checks (everything reports ready while running hot).
3. **Recovery chaos protocol.** Extend the harness: measure not just what
   fails during the experiment but the *recovery curve* after it ends — time
   to baseline readiness, and whether load overshoots. "CEI predicts blast
   radius" becomes "CEI predicts blast radius AND recovery cost." A second
   headline number no one else has.

**CEI role:** partial — entropy (load variability) is a real input here;
centrality identifies where herds converge. **Sales frame:** "your failover
plan is itself an outage plan; we can tell you where."

---

## Problem 4 — Resource elasticity lag

*Autoscaling reacts to CPU after the fact; the dependency signal arrives
earlier.*

**Have:** the graph knows who is upstream of whom; drift knows what changed;
`autoscaler_at_ceiling` knows who has no headroom.

**Build:**

1. **Topology-aware scaling advisor (advisory first).** If `db` degrades,
   its dependents' queues will grow with a lag the graph can name. Signal:
   upstream degradation + dependent HPA near ceiling = "scale these now, the
   pressure is already in flight." Ship as a *recommendation feed with a
   measured counterfactual* ("had you scaled at T, the queue peak at T+90s
   would have been absorbed") — not as a controller.
2. **HPA topology audit.** Static finding, cheap, immediately sellable: HPAs
   whose metrics watch the workload itself while its dominant failure mode is
   upstream (CPU-based HPA on an IO-bound dependent scales on exactly the
   wrong signal).
3. **Only later, and gated: closed-loop.** Feeding a topology signal into a
   fast control loop is precisely the oscillation risk the patent's stability
   monitor exists to manage — do not ship acting mode until the advisor's
   recommendations have a measured track record. The oscillation detector
   becomes the safety interlock, which is a patent claim earning its keep.

**CEI role:** indirect — the graph, not the score. The score's job is
prioritizing *which* dependents matter when upstream degrades.

---

## Problem 5 — Cloud control-plane dependency

*Your pods are healthy; the APIs needed to change or recover them are down.*

AWS's own guidance (static stability, control/data plane separation) says data
planes should survive control-plane impairment — but no tool measures whether
a customer's *recovery paths* depend on control planes. October 2025: workloads
kept serving while nothing could be launched, scaled, or failed over.

**Build:**

1. **Recovery-path dependency audit.** For each resilience mechanism the
   cluster relies on, classify what it needs when it fires: HPA scale-up →
   node provisioning → cloud control plane + registry + IAM. Failover to a
   standby that must first be *scaled up* → control plane. PDB-protected
   drain → API server. Output: "your steady state survives a control-plane
   outage; your recovery does not, in these 7 specific places."
2. **Static-stability score.** Fraction of recovery paths that work with the
   control plane down: pre-pulled images, warm standbys at full replica count,
   pre-provisioned capacity vs launch-on-demand. This is a checkable, sellable
   number tied directly to AWS's published best practice.
3. **CSPM extension.** The Azure CSPM already enumerates control-plane
   resources; add the same for AWS/GCP as the cloud connect work lands
   (already in BACKLOG), with control-plane dependency as a first-class
   finding category.

**CEI role:** minimal — this is graph + classification, not scoring. Honest
scoping: CEI does not apply everywhere, and this is one of the places.

---

## Problem 6 — Multi-cloud isn't really independent

*Two clouds sharing DNS, identity, CDN, or a SaaS are one failure domain
wearing two logos.*

**Have:** external_deps per cluster; the DB already models many clusters per
tenant.

**Build:**

1. **Cross-cluster convergence.** Same analysis as problem 1, run across the
   tenant's *fleet*: external endpoints reached from clusters on different
   providers. An Auth0 tenant reached from EKS and AKS is the finding; so is
   the same Cloudflare zone fronting both. The October Azure event is the
   demo script: "you moved to two clouds to survive this — here is the list
   of things both clouds still share."
2. **True independence score per workload pair.** For services deployed
   redundantly across clouds, compute shared fate: intersection of their
   transitive external dependency sets, weighted by category. Deliverable is a
   number a CTO can put in a board deck: "our multi-cloud deployment is 62%
   independent, and the remaining 38% is these five dependencies."
3. **Provider infrastructure overlap (research tier).** Beyond observed
   egress: shared cert authorities, shared BGP upstreams, shared SaaS control
   planes. Static knowledge base joined to the observed graph. Lower
   confidence, clearly labelled as such — the provenance discipline from
   external_deps (observed vs declared vs assumed) extends naturally.

**CEI role:** weighting again. **Prerequisite:** none — this is mostly a new
aggregation over data the fleet already sends, plus a fleet-level API.

---

## Problem 7 — Criticality ≠ utilization

*The 2%-utilized service with 300 dependents matters more than the 90%-utilized
one with none.*

This is CEI's founding thesis, and it is now **validated**: safe_to_delete
gates utilization-based recommendations by dependency structure, and the chaos
correlation demonstrates the structure is real. Remaining work is
productization, not research:

1. **Ship the safety-gated cost story as the wedge.** "We make your FinOps
   recommendations safe to execute" — the claimed-vs-safe savings split is
   built; it needs the UI surface and a Kubecost/OpenCost import path so it
   gates *their* recommendations, not just ours.
2. **Topology-aware allocation (research tier).** Scheduler hints from CEI:
   spread constraints and priority classes derived from measured criticality
   rather than hand-set. Ties back to problem 4's interlock discipline.

---

## Cross-cutting: drift wired into ingest (do this first)

**Recommendation carried into the plan:** every snapshot should be compared to
its predecessor automatically at ingest, not on demand.

- On accepted snapshot N: load N-1, run `drift.compare_snapshots`, persist
  events to a `drift_events` table (cluster_id, window, kind, severity,
  workload_key, evidence).
- Notify through the existing notify.py path on `critical` only —
  `became_load_bearing`, `concentration_rose ≥40%`, `load_bearing_workload_
  disappeared`. Everything else accumulates silently for the dashboard.
- Debounce: identical event for the same workload within 24h does not
  re-notify. The drift design already avoids alerting on standing state;
  the debounce protects the remaining edge.
- This is the delivery vehicle for half the plan: DCI trend (problem 1),
  metastability signals (problem 3), and cross-cluster convergence changes
  (problem 6) all become drift event kinds on the same rail once it exists.

Estimated scope: one table + migration, ~60 lines in the ingest path, reuse of
everything else. Highest leverage per line of code in this plan.

---

## Sequencing

**Phase A — the rail and the aggregations (now).**
Drift-into-ingest. DCI. Cross-cluster convergence (fleet API). HPA topology
audit. Recovery amplification score (static). All reuse existing data; no new
agent permissions; each lands as an endpoint + drift event kind.

**Phase B — the models (next).**
Correlated availability (Monte Carlo). Recovery chaos protocol + recovery
curves. Cascade calibration from accumulated chaos runs. Control-plane
recovery-path audit. Needs: nothing external; the chaos harness is the
validation engine for all of it.

**Phase C — the frontier · BUILT.**
Metastability detection from accumulated usage samples (the
recovered-but-load-did-not signature, /metastability). Pre-scale playbooks
per upstream (topology-aware scaling in advisory form, /playbook).
Provider-substrate knowledge base joined into fleet independence
(diversification-that-is-not, marked assumed_public_knowledge). Joint
intervention-set selection under budget (greedy marginal benefit,
/prescriptions/set). Carbon as an estimate with the fragility warning
attached (/carbon), GPU fragmentation measurement (in /control-plane), and
the configuration-complexity index (in structural_health). Remaining
frontier: DCGM GPU topology, counterfactual scaling replays against
recorded incidents, and calibrating the replica-correlation share from
chaos runs.

**Standing constraints.** The application ACTS — through pull requests via
the GitHub App, subject to the customer's own review and merge controls —
while the in-cluster agent keeps its read-only ClusterRole: acting through
the customer's GitOps pipeline is action with an audit trail, a reviewer,
and a revert button. NIW demonstration surface stays pinned by goldens.
Every predictive claim ships with its chaos-validation protocol — "here is
the number, here is how to check it on your own cluster" is the
differentiator every incumbent lacks.

---

## Sources

- [Metastable Failures in the Wild (OSDI '22)](https://www.usenix.org/system/files/osdi22-huang-lexiang.pdf) ·
  [MSF-Model: prediction of metastable failures](https://arxiv.org/html/2309.16181) ·
  [Metastable failures explained](https://read.thecoder.cafe/p/metastable-failures)
- [AWS: Static stability using Availability Zones](https://d1.awsstatic.com/builderslibrary/pdfs/static-stability-using-availability-zones.pdf) ·
  [AWS: Control planes and data planes](https://docs.aws.amazon.com/whitepapers/latest/aws-fault-isolation-boundaries/control-planes-and-data-planes.html) ·
  [Reducing control-plane dependencies in us-east-1](https://repost.aws/articles/ARe7BZRCUhRlap9b7yFSagLw/reducing-control-plane-dependencies-in-us-east-1-a-practical-guide-for-aws-customers)
- [Cloud outages 2025: DNS failures and control-plane crises](https://windowsforum.com/threads/cloud-outages-2025-dns-failures-and-control-plane-crises.387534/) ·
  [Multi-region failure domains: lessons from 2025's outages](https://dsa-research.org/blog/multi-region-failure-domains-2025-outages/) ·
  [Invisible dependencies: the Google Cloud outage](https://www.catchpoint.com/blog/invisible-dependencies-visible-impact-lessons-from-the-google-cloud-outage)

---

# Addendum: problems 8–12, and the synthesis

The first seven problems are about *seeing* structure. These five are about
*acting* on it — and they converge on one framework, built as
`prescribe.py`: interventions ranked by risk reduced per dollar, with risk
and cost in the same currency.

## Problem 8 — Cost optimization creates fragility · **BUILT**

The scenario: removing 30 idle replicas saves $70k/year; the optimizer
reports "-18% cost, successful"; the architecture is now considerably more
fragile — and nothing measured it.

`prescribe.py` prices both columns. Every replica-removal candidate carries
its savings AND the annualised cost of the expected downtime it buys
(availability Monte Carlo × operator's cost-of-downtime rate), and gets a
NET. A removal whose fragility costs more than it saves gets the verdict
`savings_cost_more_than_they_save` — the optimization that was a loss
wearing a savings report, named. This is risk-aware FinOps:
min(αC + γR) with C and R in dollars, solved by candidate evaluation
under common random numbers.

Out of scope in v1, stated: L (latency — no latency data collected) and
E (carbon — see problem 9).

## Problem 9 — Carbon-aware scheduling ignores systemic risk · planned

Moving compute to cheaper/greener regions concentrates workloads — a
carbon optimizer with no fragility term recreates problem 8 with a
different objective. The framework slot already exists: carbon is one more
dollar-denominated term in the prescription NET (region carbon intensity ×
energy price or internal carbon price). **Missing data, honestly:** region
carbon intensity (publishable static table — addable), per-workload energy
(not collected; approximable from CPU requests × node TDP class). Planned
as a prescription term, not a separate product.

## Problem 10 — GPU fragmentation · planned, data-gated

"GPUs available" while memory/topology/interconnect constraints make the
capacity unusable is a bin-packing-with-topology problem. The agent
currently collects no GPU data at all; step one is `nvidia.com/gpu`
allocatable/requested (visible in the API today), which enables
fragmentation *measurement* (free GPUs nobody can use at current request
shapes). True topology awareness (NVLink domains, MIG layout) needs
node-local data beyond the Kubernetes API — DCGM integration, explicitly
future. Not started; sequenced after the fleet features because it serves a
narrower audience until AI workloads dominate a customer's clusters.

## Problem 11 — Configuration entropy · planned

Thousands of IAM rules, Terraform resources, and policies accumulate
complexity nobody can reason about. We already parse Terraform state and
collect Azure role assignments; the buildable metric is a complexity index
over: resource-type diversity (Shannon entropy — the term is already in the
patent's vocabulary), dependency depth, policy count per principal, and
orphaned-resource share (declared but unreferenced by any observed
workload). The interesting research cut: complexity *growth rate* as a
leading indicator, on the drift rail like everything else. Planned for the
rail after fleet adoption.

## Problem 12 — No measure of cloud systemic risk · **BUILT (v1)**

"CPU 38%, memory 61%, latency 72ms, error rate 0.3%, pods healthy" — and
the architecture has quietly evolved into one Service X / Queue / IAM away
from a disproportionate failure. Conventional monitoring answers "is
something failing"; nothing answers "has the architecture evolved into a
fragile state".

`structural_health` (in every /prescriptions response) is the v1 answer,
composed from validated parts rather than invented fresh: DCI over the
combined graph, internal concentration, largest single blast radius, top
recovery amplification — subscores always shown, because a single opaque
number is astrology with extra steps. The drift rail is its time
derivative: the same numbers, watched per snapshot, alerting on the change.
Chaos correlation (0.98) is what licenses taking the structural numbers
seriously: the graph they are computed from demonstrably matches how the
system actually fails.

## The synthesis: prescriptive resilience optimization · **BUILT (v1)**

`GET /clusters/{id}/prescriptions` answers "which intervention reduces
systemic risk the most per dollar":

- **Candidates:** add_replica (partial-correlation model — the correlated
  share of failures never divides away, so replicas have honestly
  diminishing returns), remove_replicas (the FinOps move, both columns),
  split_dependency (a second hub instance, dependents divided — the move
  that attacks concentration itself), diversify_external (independent
  failover squares a shared dependency's unavailability), and always
  do_nothing.
- **Evaluation:** each candidate re-runs the seeded Monte Carlo under
  common random numbers, so deltas are architecture, not sampling luck.
- **Honesty rules:** interventions whose price is unknowable from a
  snapshot (a second identity provider's contract) report
  `pricing_required` with the risk reduction computed, never an invented
  cost. On a healthy topology the framework recommends doing nothing —
  verified live, which is the property separating a prescription engine
  from a work generator.

**v2 direction:** joint optimization over intervention *sets* (currently
one-at-a-time), latency and carbon terms as data arrives, and calibrating
the replica-correlation share from chaos experiments the way edge
transmission already is.
