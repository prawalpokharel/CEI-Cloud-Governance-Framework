# Scenario 2: GPU Cluster — ML Training Infrastructure

## Description
A heterogeneous GPU training cluster with mixed H100/A100 hardware, data pipelines, checkpoint storage, and inference serving. Exercises the framework against the retraining-governance and compute-allocation pillars.

## Topology
- 15 nodes including 8 GPU workers (5 training, 3 supporting/inference)
- 19 edges modeling data flow from ingest through training to inference
- Mixed accelerator types (H100, A100) with different memory tiers

## Demonstrated Behaviors
- **Heterogeneous allocation**: Framework distinguishes between H100 and A100 nodes when evaluating substitutability.
- **Checkpoint criticality**: `checkpoint-store` has write-fence policy — framework must not reduce capacity during active checkpointing.
- **Retraining window governance**: Critical-tier nodes (inference workers) have scheduled-only retraining windows; core-tier nodes accept opportunistic retraining.
- **Inference SLO protection**: `gpu-worker-07/08` serve inference traffic with p99 latency SLO — framework prevents modifications that would increase tail latency.
- **Data residency constraint**: Feature store and ingest pinned to `us-gov-west-1`.

## Data Files
- `topology.json`, `governance.json`, `telemetry.json`

## Expected Outcomes
- Idle A100 workers identified as candidates for scale-down (gpu-worker-04, 05, 06)
- H100 workers protected despite high utilization (governance + cascade risk)
- Inference SLO nodes never modified without k-hop validation
