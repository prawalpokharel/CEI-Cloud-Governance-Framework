# Scenario 4: Underwater Acoustic Positioning System (APS)

## Description
A bandwidth-constrained underwater positioning network providing GPS-equivalent localization services to unmanned undersea vehicles (UUVs). Exercises the framework against sparse-connectivity, severely bandwidth-limited operating conditions relevant to programs such as DARPA's POSYDON.

## Topology
- 14 nodes: surface buoys (RF/satcom link to shore), seafloor acoustic anchors, mid-water relays, UUV clients
- 15 edges representing acoustic transmission paths (5-20 Kbit/s effective bandwidth per link)
- Vertical span from surface to 1800m depth

## Demonstrated Behaviors
- **Bandwidth scarcity**: Total acoustic bandwidth is orders of magnitude below RF equivalents. Framework makes allocation decisions based on positioning accuracy requirement rather than CPU utilization.
- **Minimum positioning sources**: Critical-tier UUVs require at least 3 simultaneous anchor beacons for positioning solution. Framework enforces this even when individual anchors appear underutilized.
- **Frequency band coordination**: Anchors operate on non-overlapping frequency bands (10-15 kHz, 15-20 kHz, 20-25 kHz). Framework respects band assignments when rebalancing.
- **Ocean noise adaptation**: Critical anchors carry noise-floor-tracking policy — framework avoids modifications during noise events.
- **Inertial fallback preservation**: Framework validates that UUV inertial navigation fallback is armed before any anchor reconfiguration.

## Data Files
- `topology.json`, `governance.json`, `telemetry.json`

## Expected Outcomes
- Idle anchor (seafloor-anchor-04) and supporting UUV (uuv-delta) identified as reduceable
- Oscillating relay (relay-node-01) triggers oscillation suppression
- Critical anchors protected despite saturation (positioning-sources requirement)
