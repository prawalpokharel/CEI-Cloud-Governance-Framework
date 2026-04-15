#!/usr/bin/env python3
"""
CEI Scenario Runner
===================

Loads a scenario (topology + governance + telemetry), computes CEI scores,
and produces an HPA-baseline comparison demonstrating oscillation suppression,
governance-aware classification, and dependency-aware modification decisions.

Usage:
    python runner.py --scenario cloud_microservices
    python runner.py --scenario gpu_cluster --mode compare
    python runner.py --all
"""
import json
import math
import argparse
from pathlib import Path
from collections import defaultdict

SCENARIOS_DIR = Path(__file__).parent


# -----------------------------------------------------------
# Graph primitives
# -----------------------------------------------------------
def build_graph(nodes, edges):
    adj = defaultdict(set)
    rev = defaultdict(set)
    for src, dst in edges:
        adj[src].add(dst)
        rev[dst].add(src)
    return adj, rev


def compute_centrality(nodes, adj, rev):
    """Approximate PageRank + degree centrality (simplified)."""
    ids = [n["id"] for n in nodes]
    N = len(ids)
    if N == 0:
        return {}
    pr = {n: 1.0 / N for n in ids}
    d = 0.85
    for _ in range(30):
        new_pr = {}
        for n in ids:
            incoming = rev[n]
            share = sum(pr[s] / max(1, len(adj[s])) for s in incoming)
            new_pr[n] = (1 - d) / N + d * share
        pr = new_pr
    # Normalize
    max_pr = max(pr.values()) if pr else 1.0
    return {n: pr[n] / max_pr for n in ids}


def compute_entropy(telemetry_points, n_bins=10):
    """Shannon entropy of utilization distribution, normalized to [0,1]."""
    if not telemetry_points:
        return 0.0
    vals = [p["cpu"] for p in telemetry_points]
    bins = [0] * n_bins
    for v in vals:
        idx = min(n_bins - 1, int(v * n_bins))
        bins[idx] += 1
    total = sum(bins)
    if total == 0:
        return 0.0
    h = 0.0
    for c in bins:
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h / math.log2(n_bins) if n_bins > 1 else 0.0


def compute_governance_risk(node, tier_policies):
    """Governance compliance score [0=fully compliant, 1=violations].

    Higher risk = more constraints the framework must respect.
    """
    tier = node.get("tier", "supporting")
    policy = tier_policies.get(tier, {})
    # Simple model: critical=high constraint weight, supporting=low
    weights = {"critical": 0.90, "core": 0.65, "edge": 0.60, "supporting": 0.30, "discretionary": 0.15}
    return weights.get(tier, 0.5)


def compute_cei(centrality, entropy, governance, alpha=0.4, beta=0.3, gamma=0.3):
    """CEI = alpha*C + beta*H + gamma*R."""
    return alpha * centrality + beta * entropy + gamma * governance


# -----------------------------------------------------------
# Oscillation detection
# -----------------------------------------------------------
def detect_oscillations(telemetry, threshold=0.15):
    """Count directional reversals (flip-flops) in utilization."""
    events = 0
    vals = [p["cpu"] for p in telemetry]
    for i in range(2, len(vals)):
        d1 = vals[i - 1] - vals[i - 2]
        d2 = vals[i] - vals[i - 1]
        if d1 * d2 < 0 and abs(d1) > threshold / 2 and abs(d2) > threshold / 2:
            events += 1
    return events


# -----------------------------------------------------------
# HPA baseline (reactive threshold autoscaler)
# -----------------------------------------------------------
def hpa_decisions(telemetry, scale_up=0.70, scale_down=0.30):
    """Simulate horizontal pod autoscaler: scale on instantaneous threshold."""
    actions = []
    for i, p in enumerate(telemetry):
        if p["cpu"] > scale_up:
            actions.append(("scale_up", i))
        elif p["cpu"] < scale_down:
            actions.append(("scale_down", i))
    return actions


# -----------------------------------------------------------
# CEI decision logic
# -----------------------------------------------------------
def cei_decisions(node, cei_score, oscillation_count, tier_policies, tau_up=0.65, tau_down=0.35):
    """
    CEI decision: considers score, oscillation state, and governance.
    Returns (action, reason).
    """
    tier = node.get("tier", "supporting")
    policy = tier_policies.get(tier, {})

    # Suppression: high oscillation count -> no action
    if oscillation_count > 20:
        return ("no_action", "oscillation_suppression")

    # Governance guard: critical tier below threshold but governance-protected
    if tier == "critical" and cei_score < tau_down:
        return ("no_action", "governance_protected_despite_low_score")

    # Compliance-constrained
    if policy.get("dr_required") and cei_score < tau_down:
        return ("no_action", "dr_requirement_prevents_reduction")

    if cei_score > tau_up:
        return ("scale_up", "cei_exceeds_upper_threshold")
    if cei_score < tau_down:
        return ("scale_down", "cei_below_lower_threshold")
    return ("monitor", "within_stable_band")


# -----------------------------------------------------------
# Main scenario execution
# -----------------------------------------------------------
def run_scenario(scenario_name):
    sdir = SCENARIOS_DIR / scenario_name
    if not sdir.exists():
        print(f"Error: scenario '{scenario_name}' not found.")
        return

    topo = json.loads((sdir / "topology.json").read_text())
    gov = json.loads((sdir / "governance.json").read_text())
    tel = json.loads((sdir / "telemetry.json").read_text())

    nodes = topo["nodes"]
    edges = topo["edges"]
    tier_policies = gov["tiers"]

    adj, rev = build_graph(nodes, edges)
    centrality = compute_centrality(nodes, adj, rev)

    print(f"\n{'=' * 70}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'=' * 70}")
    print(f"Nodes: {len(nodes)}  Edges: {len(edges)}")
    print(f"{'=' * 70}")
    print(f"\n{'Node':<28}{'C':>7}{'H':>7}{'R':>7}{'CEI':>7}  {'HPA':<14}{'CEI decision':<36}")
    print("-" * 106)

    hpa_stats = {"scale_up": 0, "scale_down": 0, "oscillations": 0}
    cei_stats = {"scale_up": 0, "scale_down": 0, "no_action": 0, "monitor": 0}

    results = []

    for node in nodes:
        nid = node["id"]
        telemetry = tel.get(nid, [])

        c = centrality.get(nid, 0.0)
        h = compute_entropy(telemetry)
        r = compute_governance_risk(node, tier_policies)
        cei = compute_cei(c, h, r)

        osc_count = detect_oscillations(telemetry)
        hpa_acts = hpa_decisions(telemetry)
        hpa_up = sum(1 for a, _ in hpa_acts if a == "scale_up")
        hpa_down = sum(1 for a, _ in hpa_acts if a == "scale_down")
        hpa_summary = f"up:{hpa_up} down:{hpa_down}"

        cei_action, cei_reason = cei_decisions(node, cei, osc_count, tier_policies)

        hpa_stats["scale_up"] += hpa_up
        hpa_stats["scale_down"] += hpa_down
        hpa_stats["oscillations"] += osc_count
        cei_stats[cei_action] = cei_stats.get(cei_action, 0) + 1

        print(f"{nid:<28}{c:>7.2f}{h:>7.2f}{r:>7.2f}{cei:>7.2f}  {hpa_summary:<14}{cei_action}: {cei_reason}")

        results.append({
            "node": nid, "tier": node.get("tier"),
            "centrality": round(c, 3), "entropy": round(h, 3),
            "governance_risk": round(r, 3), "cei": round(cei, 3),
            "oscillations": osc_count,
            "hpa_actions": len(hpa_acts),
            "cei_action": cei_action, "cei_reason": cei_reason
        })

    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"HPA baseline:   {hpa_stats['scale_up']} scale-ups, {hpa_stats['scale_down']} scale-downs")
    print(f"HPA oscillation events (unsuppressed): {hpa_stats['oscillations']}")
    print(f"CEI decisions:  {cei_stats.get('scale_up', 0)} up, {cei_stats.get('scale_down', 0)} down, "
          f"{cei_stats.get('no_action', 0)} no-action, {cei_stats.get('monitor', 0)} monitor")
    print(f"CEI oscillation suppression: {sum(1 for r in results if r['cei_reason'] == 'oscillation_suppression')} nodes")
    print(f"CEI governance protections: {sum(1 for r in results if 'governance' in r['cei_reason'] or 'dr_requirement' in r['cei_reason'])} nodes")

    # Save
    out = sdir / "results.json"
    out.write_text(json.dumps({
        "scenario": scenario_name,
        "summary": {"hpa": hpa_stats, "cei": cei_stats},
        "node_results": results
    }, indent=2))
    print(f"\nResults written to {out}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", help="Scenario name (directory name)")
    parser.add_argument("--all", action="store_true", help="Run all scenarios")
    args = parser.parse_args()

    all_scenarios = ["cloud_microservices", "gpu_cluster", "drone_swarm",
                     "underwater_aps", "nc3_strategic_comms"]

    if args.all:
        for s in all_scenarios:
            run_scenario(s)
    elif args.scenario:
        run_scenario(args.scenario)
    else:
        print("Usage: python runner.py --scenario <name> | --all")
        print(f"Available scenarios: {', '.join(all_scenarios)}")


if __name__ == "__main__":
    main()
