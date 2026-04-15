# Scenario 1: Cloud Microservices — E-Commerce Platform

## Description
A production-representative e-commerce microservices topology spanning edge, core, supporting, and discretionary service tiers. This scenario exercises the CEI framework against the most common commercial deployment pattern.

## Topology
- 15 nodes across 5 governance tiers
- 17 directed dependency edges
- Includes critical (payment, database), core (order, auth), supporting (catalog, search), and discretionary (analytics) tiers

## Demonstrated Behaviors
- **Oscillation detection**: `catalog-svc` and `order-svc` exhibit sinusoidal scaling patterns that trigger the oscillation suppression module.
- **Governance-aware classification**: `analytics-svc` and `reporting-svc` show idle patterns but must not be confused with `notif-svc` (low utilization but core-tier governance).
- **Dependency-aware allocation**: `db-primary` is a cascade root; the framework evaluates `k-hop` neighborhoods before any modification.
- **Compliance constraints**: `payment-svc` and `db-primary` carry PCI-DSS constraints that prevent certain optimizations regardless of utilization.

## Data Files
- `topology.json` — Node definitions and dependency graph
- `governance.json` — Tier definitions and policy rules
- `telemetry.json` — 180-point utilization time-series per node

## Expected Outcomes
- Structural waste identified: `analytics-svc`, `reporting-svc` (discretionary, idle)
- Oscillation events suppressed: `catalog-svc`, `order-svc`
- No modifications to compliance-constrained nodes
