"""
CEI-driven savings calculator.

Given a topology (with provider + instance_type + replicas per node) and the
analysis output (per-node CEI scores + recommendations), produce a per-node
rightsizing recommendation and a total monthly savings estimate.

Heuristic (matches the patent specification's actuator behavior):
  - classification == "low" and recommendation == "consolidate" → drop one
    instance size in the same family OR reduce replica count by 1.
  - classification == "moderate" and CEI well below tau_down (default 0.25)
    → drop one instance size.
  - classification in ("elevated", "critical") with recommendation
    "scale_up" → step up one size; cost delta is negative (more spend) but
    we still report it so the caller can show the trade-off.
  - everything else → no action.

The recommendation ALSO respects governance min_replicas if provided.
"""

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

from .cost_tables import INSTANCE_PRICES, monthly_cost, cheaper_alternatives, HOURS_PER_MONTH


@dataclass
class SavingsRecommendation:
    node_id: str
    provider: str
    current_instance: str
    current_replicas: int
    current_monthly_usd: float
    action: str  # "downsize" | "reduce_replicas" | "upsize" | "no_action"
    suggested_instance: Optional[str]
    suggested_replicas: int
    suggested_monthly_usd: float
    monthly_delta_usd: float  # negative = saving, positive = extra spend
    annual_delta_usd: float
    rationale: str

    def to_dict(self):
        return asdict(self)


def _next_smaller(provider: str, instance_type: str) -> Optional[str]:
    """Return the next-cheaper instance in the same tier with at least 1 vCPU."""
    table = INSTANCE_PRICES.get(provider, {})
    original = table.get(instance_type)
    if not original:
        return None
    same_tier = [
        (name, spec)
        for name, spec in table.items()
        if spec["tier"] == original["tier"] and spec["hourly_usd"] < original["hourly_usd"]
    ]
    if not same_tier:
        # fall back to any cheaper option with vcpu >= 1
        same_tier = [
            (name, spec)
            for name, spec in table.items()
            if spec["hourly_usd"] < original["hourly_usd"] and spec["vcpu"] >= 1
        ]
    if not same_tier:
        return None
    same_tier.sort(key=lambda x: x[1]["hourly_usd"], reverse=True)
    return same_tier[0][0]


def _next_larger(provider: str, instance_type: str) -> Optional[str]:
    table = INSTANCE_PRICES.get(provider, {})
    original = table.get(instance_type)
    if not original:
        return None
    same_tier = [
        (name, spec)
        for name, spec in table.items()
        if spec["tier"] == original["tier"] and spec["hourly_usd"] > original["hourly_usd"]
    ]
    if not same_tier:
        return None
    same_tier.sort(key=lambda x: x[1]["hourly_usd"])
    return same_tier[0][0]


def compute_savings(
    nodes: List[Dict],
    analysis_nodes: List[Dict],
    governance: Optional[Dict] = None,
    tau_down: float = 0.25,
    tau_up: float = 0.65,
) -> Dict:
    """
    Build per-node rightsizing recommendations and aggregate savings.

    Inputs:
      nodes           – topology nodes with provider / instance_type / replicas
                        (and optionally tier metadata)
      analysis_nodes  – analysis.nodes with cei_score / classification /
                        recommendation per node_id
      governance      – optional {tiers: {tier_name: {min_replicas: int}}}
    """
    by_node = {n["node_id"]: n for n in analysis_nodes}
    tier_min = {}
    for tier_name, tier_def in (governance or {}).get("tiers", {}).items():
        if isinstance(tier_def, dict) and "min_replicas" in tier_def:
            tier_min[tier_name] = int(tier_def["min_replicas"])

    recs: List[SavingsRecommendation] = []
    total_current = 0.0
    total_suggested = 0.0

    for n in nodes:
        nid = n.get("id") or n.get("node_id")
        if not nid:
            continue
        provider = n.get("provider", "aws")
        instance = n.get("instance_type") or _default_instance(provider, n.get("tier"))
        replicas = int(n.get("replicas", 1))
        current_cost = monthly_cost(provider, instance, replicas)
        total_current += current_cost

        a = by_node.get(nid)
        if not a:
            recs.append(_no_action(nid, provider, instance, replicas, current_cost,
                                   "no analysis available"))
            total_suggested += current_cost
            continue

        cls = a.get("classification", "moderate")
        rec_word = a.get("recommendation", "no_action")
        cei = a.get("cei_score", 0.5)

        new_instance = instance
        new_replicas = replicas
        action = "no_action"
        rationale = "within tolerance"

        if rec_word == "consolidate" or (cls == "low" and cei < tau_down):
            # try replica reduction first if above tier min
            min_r = tier_min.get(n.get("tier", ""), 1)
            if replicas > min_r:
                new_replicas = replicas - 1
                action = "reduce_replicas"
                rationale = f"low CEI ({cei:.2f}) and replicas above tier min ({min_r})"
            else:
                smaller = _next_smaller(provider, instance)
                if smaller:
                    new_instance = smaller
                    action = "downsize"
                    rationale = f"low CEI ({cei:.2f}); already at min replicas, downshift instance"
        elif rec_word == "scale_up" or (cls == "critical" and cei > tau_up):
            larger = _next_larger(provider, instance)
            if larger:
                new_instance = larger
                action = "upsize"
                rationale = f"high CEI ({cei:.2f}) and critical classification — step up size"

        suggested_cost = monthly_cost(provider, new_instance, new_replicas)
        total_suggested += suggested_cost

        recs.append(
            SavingsRecommendation(
                node_id=nid,
                provider=provider,
                current_instance=instance,
                current_replicas=replicas,
                current_monthly_usd=current_cost,
                action=action,
                suggested_instance=new_instance if new_instance != instance else None,
                suggested_replicas=new_replicas,
                suggested_monthly_usd=suggested_cost,
                monthly_delta_usd=round(suggested_cost - current_cost, 2),
                annual_delta_usd=round((suggested_cost - current_cost) * 12, 2),
                rationale=rationale,
            )
        )

    monthly_savings = round(total_current - total_suggested, 2)
    return {
        "total_current_monthly_usd": round(total_current, 2),
        "total_suggested_monthly_usd": round(total_suggested, 2),
        "total_monthly_savings_usd": monthly_savings,
        "total_annual_savings_usd": round(monthly_savings * 12, 2),
        "node_recommendations": [r.to_dict() for r in recs],
        "thresholds": {"tau_down": tau_down, "tau_up": tau_up},
        "hours_per_month": HOURS_PER_MONTH,
    }


def _no_action(nid, provider, instance, replicas, current_cost, why):
    return SavingsRecommendation(
        node_id=nid,
        provider=provider,
        current_instance=instance,
        current_replicas=replicas,
        current_monthly_usd=current_cost,
        action="no_action",
        suggested_instance=None,
        suggested_replicas=replicas,
        suggested_monthly_usd=current_cost,
        monthly_delta_usd=0.0,
        annual_delta_usd=0.0,
        rationale=why,
    )


def _default_instance(provider: str, tier: Optional[str]) -> str:
    """Pick a sensible default instance type when the topology omits it."""
    defaults = {
        ("aws", "critical"):      "m5.xlarge",
        ("aws", "core"):          "m5.large",
        ("aws", "edge"):          "t3.medium",
        ("aws", "supporting"):    "t3.medium",
        ("aws", "discretionary"): "t3.small",
        ("azure", "critical"):    "Standard_D4s_v3",
        ("azure", "core"):        "Standard_D2s_v3",
        ("azure", "edge"):        "Standard_B2ms",
        ("azure", "supporting"):  "Standard_B2ms",
        ("gcp", "critical"):      "n2-standard-4",
        ("gcp", "core"):          "n2-standard-2",
        ("gcp", "edge"):          "e2-medium",
        ("gcp", "supporting"):    "e2-medium",
    }
    if tier and (provider, tier) in defaults:
        return defaults[(provider, tier)]
    # Provider-level fallback
    fallback = {
        "aws": "t3.medium",
        "azure": "Standard_B2ms",
        "gcp": "e2-medium",
        "kubernetes": "pod-md",
    }
    return fallback.get(provider, "t3.medium")
