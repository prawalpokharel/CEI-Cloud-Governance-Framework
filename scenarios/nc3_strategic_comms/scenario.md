# Scenario 5: NC3 Strategic Communications Network

## Description
A notional nuclear command-and-control (NC3) strategic communications topology with space, HF, and VLF segments; crypto and auth gateways; and platform terminals across bomber, submarine, silo, and mobile platforms. Exercises the framework under extreme-redundancy, low-utilization, adversarial-threat conditions.

**Note:** This scenario uses unclassified, publicly-available architectural concepts. It is not representative of any specific deployed system.

## Topology
- 17 nodes spanning command authority, crypto modules, ground/space relays, platform terminals
- 18 edges with tri-redundant path structure to each critical terminal
- Multi-segment architecture (MILSAT/HF/VLF) for survivability

## Demonstrated Behaviors
- **Criticality despite low utilization**: NC3 systems operate at very low utilization by design — they must be immediately available during crisis. Framework recognizes that low utilization does NOT indicate reduceable capacity in this governance context.
- **Path diversity enforcement**: Silo/submarine/bomber terminals require minimum 3 physically-distinct communication paths — framework enforces during any optimization.
- **Crypto two-person integrity**: Crypto HSMs cannot be modified without TPI policy satisfaction.
- **Network segmentation**: TS-SCI nodes are network-isolated — framework respects segmentation in dependency analysis.
- **Training isolation**: `training-node` shows oscillating pattern but is isolated from operational traffic — framework can optimize without cascade risk.

## Data Files
- `topology.json`, `governance.json`, `telemetry.json`

## Expected Outcomes
- Training node is the only node safe to modify based on utilization
- All critical nodes protected regardless of apparent idleness (governance tier + redundancy requirements)
- Demonstrates framework's ability to operate correctly in domains where utilization-based optimization is dangerous
