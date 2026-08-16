# NIW / USPTO golden files

These files record the exact responses of the demonstration surface backing
USPTO App. No. 19/641,446 and the pending NIW petition:

- `/scenarios/*` — the five scenario datasets and their analyses
- `/pricing/*` — cost tables and the savings calculator
- `/benchmark/hpa-vs-cei` — the HPA comparison

`tests/test_niw_golden.py` asserts live responses still match. Regenerate only
via `python -m tests.regen_golden`, and only when a change to these numbers is
intended and reviewed.

## These record current behavior, not correct behavior

The harness is a regression detector. It answers "did this change?", never
"is this right?".

### Defect 1 — scenario telemetry never reached the pipeline — FIXED

`ScenarioLoader.to_core_engine_format` wrote real telemetry to the node's
**top level**, while `DataCollector._process_node` read it from **inside
`metrics`**. The paths never matched, so every node's real history (180
points) was discarded in favour of `_generate_synthetic_history()` — Gaussian
noise around the current reading.

A second half to the same bug: scenario points are `{"t", "cpu", "mem"}` but
every consumer reads `"memory"`. Correcting only the path would have left
memory defaulting to a flat `0.5` — which reads as perfectly stable and
zero-entropy.

Both are fixed. History is now accepted from either location and normalized
to `{"cpu", "memory"}`, with percentage inputs rescaled to fractions.

Effect on the captures: entropy spread roughly doubled (0.30 → 0.70 on
cloud_microservices), because entropy now measures real workload variability
instead of the width of a noise distribution.

### Defect 2 — the oscillation detector was amplitude-blind — FIXED

`_compute_oscillation_frequency` counted every sign change in the first
derivative and used amplitude only as an additive bonus
(`frequency * (1 + std)`). A noisy series reverses on roughly half its
samples regardless of amplitude, so essentially all real telemetry scored
0.5–0.7 — a series with σ=0.001 scored *higher* than one with σ=0.05.

Replaced with a deadband (zigzag) reduction: a reversal counts only once the
series retraces from its running extreme by `min_amplitude` (0.10). O(t) is
then `reversal_rate * amplitude_factor`, requiring both frequent *and* large
reversals.

| series | before | after | flagged now (θ=0.3) |
|---|---|---|---|
| perfectly flat | 0.000 | 0.000 | no |
| noise σ=0.001 | 0.705 | 0.000 | no |
| noise σ=0.05 (ordinary jitter) | 0.631 | 0.232 | no |
| noise σ=0.10 | — | 0.898 | yes |
| diurnal sine, 0.6 peak-to-peak | — | 0.091 | no |
| square-wave thrash 0.2↔0.8 | — | 1.000 | yes |

Effect on the captures: cloud_microservices went from 15/15 nodes flagged
with suppression active to 3/15 with suppression off.

### Defect 3 — the k-hop safety check compares a sum against a ratio — OPEN

`PreModificationValidator` aborts a modification when
`cumulative_centrality_change >= safety_threshold`. The left side is an
unbounded **sum** of neighbour centralities; the right side defaults to
`0.7`, a value on a [0, 1] scale.

The sum grows with neighbourhood size while the mean does not:

| node | k_hop_count | cumulative | mean |
|---|---|---|---|
| data-ingest-01 | 3 | 1.333 | 0.44 |
| gpu-worker-01 | 10 | 4.267 | 0.43 |

So any node with a connected neighbour exceeds the threshold, `is_safe` is
false almost everywhere, and every recommendation collapses to `no_action`
with `blocked_reason: "k-hop impact exceeds safety threshold"`. This is why
`total_potential_savings` is `$0.00` across all five scenarios even after
Defects 1 and 2 are fixed.

Two signals that this is a units mismatch rather than intent: the mean is
stable across neighbourhood sizes, and the validator already computes
`max_single_impact` but never uses it in the safety check.

Left open deliberately. The patent specification uses the word "cumulative",
so the correct resolution — normalize the measure, or recalibrate the
threshold to the scale of a sum — is a decision about Module 110's
described behaviour, not a typo to silently patch.

A secondary contributor: scenario topologies carry `cpu_limit`/`mem_gb` but
no `instance_type` or `monthly_cost`, so the actuator computes savings
against a cost of zero. The demo pages avoid this by calling
`/pricing/savings`, which synthesizes an instance type from the tier.

## What was corrected before capture

Three changes were required to make a golden harness possible at all, none of
which alter which data the pipeline uses:

1. **Per-request pipeline modules** (`src/engine.py`). Modules 101–111 were
   process-level singletons carrying mutating state, so repeating an
   identical request returned different numbers each time — `beta` decayed
   0.27 → 0.09 over four calls — and analyzing one scenario shifted the next
   one's results. Module 112 (rollback) remains shared, as its snapshot store
   must outlive a request.
2. **Seeded synthetic history.** `_generate_synthetic_history` drew from
   numpy's global RNG, so identical requests produced different output.
   It is now seeded per node id, making it stable and order-independent.
3. **Sorted `k_hop_nodes`.** Returned as `list(set)`, whose order varies with
   `PYTHONHASHSEED` — the same request served a different ordering on every
   deploy.

Determinism is verified across three `PYTHONHASHSEED` values.

## Float tolerance

Comparison uses `abs_tol=1e-6`. Goldens are captured on darwin/arm64 and
asserted in CI on linux/amd64; PageRank and betweenness accumulate floats in
a BLAS-dependent order. The engine rounds most output to 4dp, so real
regressions are orders of magnitude larger than this tolerance. Strings,
booleans, integers, structure, and list lengths compare exactly.
