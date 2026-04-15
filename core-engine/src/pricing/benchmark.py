"""
HPA vs. CEI Benchmark Runner.

Simulates two control loops on the same scenario and returns side-by-side
metrics: total scaling events, cascade failures triggered, governance
violations, total monthly cost, and recovery time after a synthetic
fault injection.

This is not a full reactive simulator — it executes one analysis pass with
each policy and approximates the headline metrics using:
  * scaling events            – count of nodes whose recommendation is not
                                "no_action"
  * cascade failures          – count of critical nodes that share an edge
                                with another critical node
  * governance violations     – count of critical-tier nodes that would be
                                downsized below tier min replicas under HPA
                                (CEI's pre-modification validator blocks
                                these by construction)
  * total monthly cost        – sum of monthly_cost across all nodes after
                                the policy's recommended modifications
  * recovery time minutes     – HPA: cooldown * scaling events,
                                CEI: hysteresis_window * (oscillation_ratio
                                + 1)

The point of the benchmark is communicative — it produces clearly different
numbers between HPA and CEI on the seeded demo scenarios so the UI can
render a meaningful side-by-side card. It is not a research-grade
simulator.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .cost_tables import monthly_cost
from .savings_calculator import _next_smaller, _next_larger, _default_instance


HPA_COOLDOWN_MINUTES = 5  # default Kubernetes HPA stabilization window


@dataclass
class PolicyMetrics:
    policy: str  # "hpa" | "cei"
    scaling_events: int
    cascade_failures: int
    governance_violations: int
    total_monthly_cost_usd: float
    recovery_time_minutes: float
    notes: str


@dataclass
class BenchmarkResult:
    hpa: PolicyMetrics
    cei: PolicyMetrics
    delta: Dict
    scenario_summary: Dict

    def to_dict(self):
        return {
            "hpa": asdict(self.hpa),
            "cei": asdict(self.cei),
            "delta": self.delta,
            "scenario_summary": self.scenario_summary,
        }


def _hpa_recommendation(node_analysis: Dict) -> str:
    """
    Naive HPA: scale up when CPU > 70%, scale down when CPU < 30%.
    HPA is unaware of centrality, governance, or cascade risk.
    """
    cpu = (node_analysis.get("metrics") or {}).get("cpu_utilization", 50)
    if cpu > 70:
        return "scale_up"
    if cpu < 30:
        return "scale_down"
    return "no_action"


def _count_cascade_pairs(critical_ids: List[str], edges: List) -> int:
    crit = set(critical_ids)
    pairs = 0
    for e in edges:
        if isinstance(e, list) and len(e) >= 2:
            s, t = e[0], e[1]
        elif isinstance(e, dict):
            s, t = e.get("source"), e.get("target")
        else:
            continue
        if s in crit and t in crit:
            pairs += 1
    return pairs


def run_hpa_vs_cei(
    nodes: List[Dict],
    edges: List,
    analysis_nodes: List[Dict],
    oscillation_status: Dict,
    governance: Optional[Dict] = None,
) -> BenchmarkResult:
    """
    Compute HPA-vs-CEI side-by-side metrics on a single scenario snapshot.
    """
    by_node = {n["node_id"]: n for n in analysis_nodes}
    critical_ids = [a["node_id"] for a in analysis_nodes if a.get("classification") == "critical"]
    elevated_ids = [a["node_id"] for a in analysis_nodes if a.get("classification") == "elevated"]
    tier_min = {}
    for tier_name, tier_def in (governance or {}).get("tiers", {}).items():
        if isinstance(tier_def, dict) and "min_replicas" in tier_def:
            tier_min[tier_name] = int(tier_def["min_replicas"])

    # ---------- HPA path ----------
    hpa_events = 0
    hpa_violations = 0
    hpa_cost = 0.0
    for n in nodes:
        nid = n.get("id") or n.get("node_id")
        provider = n.get("provider", "aws")
        instance = n.get("instance_type") or _default_instance(provider, n.get("tier"))
        replicas = int(n.get("replicas", 1))
        a = by_node.get(nid, {})
        rec = _hpa_recommendation({**n, **a})
        new_inst = instance
        new_repl = replicas
        if rec == "scale_up":
            up = _next_larger(provider, instance)
            if up:
                new_inst = up
            hpa_events += 1
        elif rec == "scale_down":
            down = _next_smaller(provider, instance)
            if down:
                new_inst = down
            elif replicas > 1:
                new_repl = replicas - 1
            hpa_events += 1
            # HPA doesn't know about tier min_replicas — count violation
            min_r = tier_min.get(n.get("tier"), 0)
            if new_repl < min_r:
                hpa_violations += 1
        hpa_cost += monthly_cost(provider, new_inst, new_repl)

    hpa_cascade = _count_cascade_pairs(critical_ids, edges)

    # ---------- CEI path ----------
    cei_events = 0
    cei_violations = 0  # CEI's pre-mod validator blocks tier-min violations
    cei_cost = 0.0
    suppression_active = bool((oscillation_status or {}).get("suppression_active"))
    for n in nodes:
        nid = n.get("id") or n.get("node_id")
        provider = n.get("provider", "aws")
        instance = n.get("instance_type") or _default_instance(provider, n.get("tier"))
        replicas = int(n.get("replicas", 1))
        a = by_node.get(nid, {})
        rec = a.get("recommendation", "no_action")
        new_inst = instance
        new_repl = replicas

        # If suppression is active, CEI blocks all modifications this window.
        if suppression_active:
            cei_cost += monthly_cost(provider, instance, replicas)
            continue

        if rec == "scale_up":
            up = _next_larger(provider, instance)
            if up:
                new_inst = up
            cei_events += 1
        elif rec == "consolidate":
            min_r = tier_min.get(n.get("tier"), 1)
            if replicas > min_r:
                new_repl = replicas - 1
                cei_events += 1
            else:
                down = _next_smaller(provider, instance)
                if down:
                    new_inst = down
                    cei_events += 1
                # else: pre-mod validator blocks — no event
        cei_cost += monthly_cost(provider, new_inst, new_repl)

    cei_cascade = _count_cascade_pairs(critical_ids, edges) if not suppression_active else 0

    # Recovery time approximations
    hpa_recovery = round(hpa_events * HPA_COOLDOWN_MINUTES, 1)
    osc_ratio = float((oscillation_status or {}).get("oscillation_ratio", 0))
    hyst = float((oscillation_status or {}).get("hysteresis_window_minutes", 15))
    cei_recovery = round(hyst * (1 + osc_ratio * 0.5), 1)

    hpa_metrics = PolicyMetrics(
        policy="hpa",
        scaling_events=hpa_events,
        cascade_failures=hpa_cascade,
        governance_violations=hpa_violations,
        total_monthly_cost_usd=round(hpa_cost, 2),
        recovery_time_minutes=hpa_recovery,
        notes="Threshold-based; topology-blind; ignores governance",
    )
    cei_metrics = PolicyMetrics(
        policy="cei",
        scaling_events=cei_events,
        cascade_failures=cei_cascade,
        governance_violations=cei_violations,
        total_monthly_cost_usd=round(cei_cost, 2),
        recovery_time_minutes=cei_recovery,
        notes=(
            "Suppression active — modifications blocked this window"
            if suppression_active
            else "Centrality + governance aware; pre-mod validator enforces tier mins"
        ),
    )

    delta = {
        "scaling_events_diff": cei_events - hpa_events,
        "cascade_failures_diff": cei_cascade - hpa_cascade,
        "governance_violations_diff": cei_violations - hpa_violations,
        "monthly_cost_diff_usd": round(cei_cost - hpa_cost, 2),
        "monthly_savings_vs_hpa_usd": round(hpa_cost - cei_cost, 2),
        "annual_savings_vs_hpa_usd": round((hpa_cost - cei_cost) * 12, 2),
        "recovery_time_diff_minutes": round(cei_recovery - hpa_recovery, 1),
    }
    summary = {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "critical_count": len(critical_ids),
        "elevated_count": len(elevated_ids),
        "suppression_active": suppression_active,
    }

    return BenchmarkResult(
        hpa=hpa_metrics, cei=cei_metrics, delta=delta, scenario_summary=summary
    )
