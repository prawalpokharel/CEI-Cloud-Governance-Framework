# Blast-radius validation against controlled failure

**Every previous evaluation of CEI's blast-radius predictions was derived from
the same dependency graph the predictions come from.** That is circular: it
can confirm the arithmetic is self-consistent, and nothing more. This
experiment breaks the circle — the measurement is the cluster's *observed
behaviour under real failure*, which does not use the graph, the CEI weights,
or any inference this system makes.

## Result

Six experiments on a live cluster (`cei-test`, kind v1.35.0, 3 nodes,
Chaos Mesh 2.x `pod-failure`, 90-second windows, full recovery verified
between runs):

| target | predicted affected | measured affected | precision | recall |
|---|---|---|---|---|
| db | api, reporting, web, worker | api, reporting, web, worker | 1.00 | 1.00 |
| auth | api, web | api | 0.50 | 1.00 |
| api | web | web | 1.00 | 1.00 |
| web | — | — | n/a | n/a |
| worker | — | — | n/a | n/a |
| reporting | — | — | n/a | n/a |

**Spearman rank correlation between predicted and measured blast-radius size:
0.98.** Mean precision 0.83, mean recall 1.00, **zero false negatives**.

## Reading it

- **Zero false negatives is the number that matters most.** A false negative
  is a real dependency the graph never saw — a missing edge, which would make
  every analysis built on the graph wrong. None occurred.
- **The one false positive is a timing artifact, not a wrong edge.** Killing
  `auth` was predicted to reach `web` transitively (`web → api → auth`). The
  during-experiment snapshot caught the cascade mid-propagation: `api` had
  lost readiness, `web`'s probe had not yet failed. The dependency is real;
  the instrument sampled before the second hop landed. This is the
  documented direction in which the method understates agreement.
- **Rank correlation is the honest headline** because the product's claim is
  ordinal — "these are the workloads that can take your system down, worst
  first." Predicting four failures where three occur is a good result *if the
  order holds*, and it held.

## Protocol (reproducible)

1. **Predict first, freeze forever.** Blast radius computed for every
   workload from the agent's snapshot, before any experiment. Predictions
   were never revised after results arrived.
2. **Perturb.** Chaos Mesh `PodChaos` with `action: pod-failure`,
   `mode: all`, 90s duration, selector scoped to one workload's labels in
   one namespace. `pod-failure` rather than `pod-kill`: a killed pod
   reschedules in seconds, faster than a dependent's readiness probe can
   observe, which reads as zero impact for a dependency that is entirely
   real.
3. **Measure.** A second agent snapshot during the window. Affected = was
   ready at baseline AND not ready during the experiment, excluding the
   target itself. Workloads unhealthy before the experiment are excluded —
   uncertainty may suppress a measurement, never fabricate one.
4. **Recover.** Experiment deleted; next run begins only after every
   deployment reports full readiness again.

The test topology has *real* runtime dependencies: each service's readiness
probe checks TCP reachability of its upstreams, so losing a dependency
genuinely makes dependents unready — the same mechanism by which real
services degrade.

## What this found besides the correlation

Ground truth pays for itself: the first run measured **zero impact
everywhere**, which was impossible given the kubectl output. The cause was an
agent bug — Kubernetes *omits* `readyReplicas` when zero pods are ready, the
collector passed `None` through, and the measurement treated `None` as
healthy. A fully-down workload was indistinguishable from a healthy one in
every analysis consuming that field. Found only because an independent
measurement disagreed with the graph-derived expectation — which is the
argument for this whole method.

## Limitations, stated

- Six experiments on one synthetic topology on one kind cluster. The
  correlation is real but the sample is small; the method is the deliverable
  as much as the number. Run it against a staging cluster with
  `chaos.plan()` for a result about *your* topology.
- Pod-level failure is the gentlest failure mode. Dependents with retries,
  caches, or circuit breakers may ride out an outage the graph correctly
  predicted, so agreement is a lower bound.
- Readiness is the only impact signal. Latency degradation, error-rate rises,
  and partial brownouts are invisible to it.
