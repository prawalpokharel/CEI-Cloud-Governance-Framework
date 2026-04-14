# Scenario 3: Drone Swarm — Edge Compute Mesh

## Description
A tactical drone swarm with ground control stations, airborne comms relays, sensor drones, and an edge fusion server. Exercises the distributed/hybrid compute allocation pillar in a latency-sensitive, intermittently-connected environment.

## Topology
- 14 nodes with strict latency budgets (50ms critical, 100ms core, 200ms supporting)
- 19 directed edges representing mesh radio + fiber + satcom links
- Mix of ground, airborne relay, and mobile platforms

## Demonstrated Behaviors
- **Latency-budgeted allocation**: Framework incorporates per-tier latency constraints into governance scoring.
- **Link redundancy enforcement**: Critical comms paths (GCS to lead drone) require min_redundancy of 2 — framework blocks optimizations that would reduce redundancy below policy.
- **Degradation detection**: `drone-03` shows degrading utilization pattern (thermal throttling or battery fade). Framework flags early.
- **Hot-standby coordinator**: `drone-lead` must always have hot standby; framework integrates with platform handoff logic.
- **Electromagnetic environment**: `relay-01/02` carry frequency-hopping policy requirement.

## Data Files
- `topology.json`, `governance.json`, `telemetry.json`

## Expected Outcomes
- Relay drones (drone-06, 07) identified as reduceable during stable operations
- Degrading drone (drone-03) flagged for preventive handoff
- Comms relays protected despite saturation (critical-tier + redundancy requirement)
