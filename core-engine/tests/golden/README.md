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
"is this right?". Two significant defects are frozen into the current
captures and are documented here so nobody mistakes them for intent.

### Defect 1 — scenario telemetry never reaches the pipeline

`ScenarioLoader.to_core_engine_format` writes real telemetry to the node's
**top level**:

```python
nodes.append({..., "utilization_history": node_telemetry})
```

`DataCollector._process_node` reads it from **inside `metrics`**:

```python
metrics = node.get("metrics", {})
utilization_history = metrics.get("utilization_history", [])   # always empty
```

The paths never match. Every node's real history (180 points per node) is
discarded and replaced by `_generate_synthetic_history()` — Gaussian noise
around the current CPU/memory reading.

Consequences, all present in these captures:

- The scenario telemetry cited on the demo pages and in the patent
  (DARPA/NATO/RAND-derived) has never been used by an analysis.
- Shannon entropy (`beta`, ~35% of the CEI score) is computed from noise.
- The stability monitor, which drives weight recalibration, reads noise.
- The oscillation detector sees noise and flags **100% of nodes** in every
  scenario, so suppression is permanently active.
- With suppression active, every recommendation collapses to `no_action`
  and `total_potential_savings` is `$0.00` for all five scenarios.

Fixing the lookup moves nearly every number in these files. It is therefore
held as a reviewed decision, not a silent correction.

### Defect 2 — the oscillation detector is amplitude-blind

`_compute_oscillation_frequency` counts sign changes in the first derivative
and normalizes by sample count. Any non-monotonic series scores ~0.5–0.7
regardless of amplitude:

| series                          | O(t)  | flagged at θ=0.3 |
|---------------------------------|-------|------------------|
| noise, σ=0.05                   | 0.631 | yes              |
| noise, σ=0.001 (flat in practice) | 0.705 | yes            |
| perfectly flat                  | 0.000 | no               |
| smooth monotonic ramp           | 0.000 | no               |

Only *perfectly* flat or *perfectly* monotonic input scores zero, and real
telemetry is neither. This independently guarantees permanent suppression
once Defect 1 is fixed, so both must be addressed together.

This matters beyond the demos: live Kubernetes CPU telemetry will trip the
same path on every workload, which would ship the Phase 1 product with its
recommendation engine permanently disabled.

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
