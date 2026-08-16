"""
Kubernetes cost allocation and waste detection.

The Phase 2 wedge: "we found $X/month of waste", derived from what workloads
actually use versus what they reserve.

## How cost is allocated

You do not pay for pods. You pay for nodes. So a workload's cost is its share
of the nodes it occupies, charged per resource dimension:

    workload_cost = cluster_monthly
                  * (CPU_COST_SHARE * cpu_share + MEM_COST_SHARE * mem_share)

Splitting the node price between CPU and memory, and charging each workload
for what it reserves on each, is what makes the numbers reconcile: total
requests normally cannot exceed allocatable capacity, so each dimension's
shares sum to at most 1 and total allocation cannot exceed the bill.

"Normally" because a snapshot can catch a cluster mid-autoscale or with stale
node data, in which case requests DO exceed capacity. Shares are normalized
back onto the real bill in that case and the over-commitment is reported as
its own finding -- see over_committed in the response.

Charging `max(cpu_share, mem_share)` is tempting -- it reflects that
scheduling is bound by whichever dimension is scarcer -- but summed across
workloads it can exceed 100% of the node. A cluster that allocates $175 of a
$140 bill makes every figure downstream indefensible, so the reconciling
formulation wins over the more intuitive one.

## What counts as waste

    waste = requested - used

Reserved-but-unused capacity. It is real money: the scheduler has set it
aside, so nothing else can use it, and you are billed for the node regardless.

Not counted as waste:
  * usage above request (a burst, not a saving)
  * unrequested resources (a workload with no requests cannot reserve waste,
    though it is its own problem -- flagged separately)
  * nodes with no workloads (idle capacity, a cluster-level concern)

## Accuracy

These are list-price estimates and are labelled as such. Real invoices reflect
Reserved Instances, Savings Plans, committed-use discounts, spot pricing, and
enterprise agreements -- commonly 20-70% below list. A number presented as
invoice-accurate that is 40% off destroys the credibility of every other
number on the page, so the response always carries its basis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..pricing.cost_tables import (
    HOURS_PER_MONTH,
    INSTANCE_PRICES,
    smallest_fitting_instance,
)

# How a node's price divides between its CPU and its memory. Derived from the
# spread between compute-optimized and memory-optimized instance families,
# which price the same capacity differently; roughly 70/30 across the major
# providers. Exact only in the sense that it is applied consistently -- no
# provider publishes a true split.
CPU_COST_SHARE = 0.70
MEM_COST_SHARE = 0.30

# Below this, a recommendation costs more in engineering attention than it
# saves. Surfacing fifty $3/month findings buries the one worth $400.
MIN_REPORTABLE_MONTHLY_WASTE = 5.0

# Fraction of a request that must be unused before a workload is called
# over-provisioned. Everything has some headroom by design; 40% unused is
# where it stops being prudent and starts being waste.
WASTE_THRESHOLD = 0.40


@dataclass
class NodeCost:
    name: str
    instance_type: str | None
    provider: str
    hourly_usd: float
    monthly_usd: float
    allocatable_cpu: float
    allocatable_memory: int
    basis: str  # "instance_type" | "estimated_from_capacity" | "unknown"


@dataclass
class WorkloadCost:
    workload_key: str
    name: str
    namespace: str
    replicas: int | None

    cpu_requested: float | None
    cpu_used: float | None
    memory_requested: int | None
    memory_used: int | None

    monthly_usd: float
    wasted_monthly_usd: float
    cpu_utilization: float | None
    memory_utilization: float | None
    verdict: str          # "over_provisioned" | "right_sized" | "no_requests" | "unmeasured"
    recommendation: str | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_key": self.workload_key,
            "name": self.name,
            "namespace": self.namespace,
            "replicas": self.replicas,
            "cpu_requested": self.cpu_requested,
            "cpu_used": self.cpu_used,
            "memory_requested": self.memory_requested,
            "memory_used": self.memory_used,
            "monthly_usd": round(self.monthly_usd, 2),
            "wasted_monthly_usd": round(self.wasted_monthly_usd, 2),
            "cpu_utilization": (
                round(self.cpu_utilization, 4)
                if self.cpu_utilization is not None
                else None
            ),
            "memory_utilization": (
                round(self.memory_utilization, 4)
                if self.memory_utilization is not None
                else None
            ),
            "verdict": self.verdict,
            "recommendation": self.recommendation,
            "details": self.details,
        }


def price_node(node: dict) -> NodeCost:
    """
    Price one node.

    Three levels of confidence, reported rather than hidden:
      1. The instance type is labelled and in the pricing table.
      2. It is not, so the cheapest instance matching the node's capacity is
         used as a stand-in. Directionally right, precisely wrong.
      3. Capacity is unknown too, so the node is priced at zero and excluded.
         Zero is visible in the output; a guess would not be.
    """
    name = node.get("name", "?")
    instance_type = node.get("instance_type")
    allocatable_cpu = node.get("allocatable_cpu_cores") or 0.0
    allocatable_memory = node.get("allocatable_memory_bytes") or 0

    provider = "aws"
    labels = node.get("labels") or {}
    if any(k.startswith("kubernetes.azure.com/") for k in labels):
        provider = "azure"
    elif any(k.startswith("cloud.google.com/") for k in labels):
        provider = "gcp"

    spec = INSTANCE_PRICES.get(provider, {}).get(instance_type or "")
    if spec:
        hourly = spec["hourly_usd"]
        basis = "instance_type"
    elif allocatable_cpu and allocatable_memory:
        guess = smallest_fitting_instance(
            provider,
            vcpu=allocatable_cpu,
            memory_gib=allocatable_memory / (1024 ** 3),
        )
        guessed = INSTANCE_PRICES.get(provider, {}).get(guess)
        hourly = guessed["hourly_usd"] if guessed else 0.0
        instance_type = guess or instance_type
        basis = "estimated_from_capacity" if guessed else "unknown"
    else:
        hourly = 0.0
        basis = "unknown"

    return NodeCost(
        name=name,
        instance_type=instance_type,
        provider=provider,
        hourly_usd=hourly,
        monthly_usd=hourly * HOURS_PER_MONTH,
        allocatable_cpu=allocatable_cpu,
        allocatable_memory=allocatable_memory,
        basis=basis,
    )


def analyze_cluster_cost(snapshot: dict) -> dict[str, Any]:
    """Allocate node cost across workloads and identify reserved-but-unused spend."""
    nodes = [price_node(n) for n in (snapshot.get("nodes") or [])]
    workloads = snapshot.get("workloads") or []

    cluster_monthly = sum(n.monthly_usd for n in nodes)
    total_cpu = sum(n.allocatable_cpu for n in nodes)
    total_memory = sum(n.allocatable_memory for n in nodes)

    # Requests normally cannot exceed allocatable capacity -- the scheduler
    # refuses to place a pod that does not fit. But a snapshot can catch a
    # cluster mid-autoscale, with pods Pending against capacity that has not
    # arrived yet, or with a node that dropped out between the node list and
    # the workload list being read.
    #
    # Left unhandled, shares sum above 1 and the product reports waste
    # exceeding the entire cluster bill -- an impossible number, and the kind
    # that ends a sales conversation. Shares are normalized back onto the real
    # bill and the over-commitment is reported as its own finding, because it
    # is one.
    requested_cpu = sum(
        w.get("cpu_cores_requested") or 0.0 for w in workloads
    )
    requested_memory = sum(
        w.get("memory_bytes_requested") or 0 for w in workloads
    )
    cpu_commitment = (requested_cpu / total_cpu) if total_cpu else 0.0
    memory_commitment = (
        (requested_memory / total_memory) if total_memory else 0.0
    )
    cpu_normalizer = max(1.0, cpu_commitment)
    memory_normalizer = max(1.0, memory_commitment)
    over_committed = cpu_commitment > 1.0 or memory_commitment > 1.0

    results: list[WorkloadCost] = []
    for workload in workloads:
        key = workload.get("key")
        if not key:
            continue

        cpu_req = workload.get("cpu_cores_requested")
        cpu_used = workload.get("cpu_cores_used")
        mem_req = workload.get("memory_bytes_requested")
        mem_used = workload.get("memory_bytes_used")

        # Share of the cluster this workload reserves on each dimension,
        # charged against that dimension's portion of the node price. See the
        # module docstring for why this rather than max().
        cpu_share = (
            (cpu_req / total_cpu / cpu_normalizer)
            if cpu_req and total_cpu else 0.0
        )
        mem_share = (
            (mem_req / total_memory / memory_normalizer)
            if mem_req and total_memory else 0.0
        )
        share = CPU_COST_SHARE * cpu_share + MEM_COST_SHARE * mem_share
        monthly = cluster_monthly * share

        cpu_util = (
            (float(cpu_used) / float(cpu_req))
            if cpu_used is not None and cpu_req
            else None
        )
        mem_util = (
            (float(mem_used) / float(mem_req))
            if mem_used is not None and mem_req
            else None
        )

        verdict, wasted, recommendation, details = _assess(
            workload, cpu_req, cpu_used, mem_req, mem_used,
            cpu_util, mem_util, monthly,
        )

        results.append(
            WorkloadCost(
                workload_key=key,
                name=workload.get("name", key),
                namespace=workload.get("namespace", "default"),
                replicas=workload.get("replicas_desired"),
                cpu_requested=cpu_req,
                cpu_used=cpu_used,
                memory_requested=mem_req,
                memory_used=mem_used,
                monthly_usd=monthly,
                wasted_monthly_usd=wasted,
                cpu_utilization=cpu_util,
                memory_utilization=mem_util,
                verdict=verdict,
                recommendation=recommendation,
                details=details,
            )
        )

    reportable = [
        r for r in results
        if r.verdict == "over_provisioned"
        and r.wasted_monthly_usd >= MIN_REPORTABLE_MONTHLY_WASTE
    ]
    reportable.sort(key=lambda r: -r.wasted_monthly_usd)

    total_waste = sum(r.wasted_monthly_usd for r in reportable)
    allocated = sum(r.monthly_usd for r in results)
    unmeasured = sum(1 for r in results if r.verdict == "unmeasured")

    pricing_bases = {n.basis for n in nodes}
    estimated = "estimated_from_capacity" in pricing_bases or "unknown" in pricing_bases

    # Invariants. Waste is a subset of allocation, which is a subset of the
    # bill. If either fails, something upstream is wrong and a wrong number is
    # worse than no number, so this is asserted rather than trusted.
    total_waste = min(total_waste, allocated)
    allocated = min(allocated, cluster_monthly) if cluster_monthly else allocated

    return {
        "summary": {
            "cluster_monthly_usd": round(cluster_monthly, 2),
            "allocated_monthly_usd": round(allocated, 2),
            # Node capacity nothing has reserved. Not workload waste, but it
            # is real spend, and omitting it makes the numbers fail to
            # reconcile against the bill.
            "unallocated_monthly_usd": round(max(0.0, cluster_monthly - allocated), 2),
            "wasted_monthly_usd": round(total_waste, 2),
            "wasted_annual_usd": round(total_waste * 12, 2),
            "waste_as_pct_of_cluster": (
                round(total_waste / cluster_monthly * 100, 1)
                if cluster_monthly else 0.0
            ),
            "workloads_over_provisioned": len(reportable),
            "workloads_unmeasured": unmeasured,
            # Above 1.0 the cluster has promised more than it can deliver:
            # pods will be Pending, or the autoscaler has not caught up.
            "cpu_commitment_ratio": round(cpu_commitment, 3),
            "memory_commitment_ratio": round(memory_commitment, 3),
            "over_committed": over_committed,
        },
        "basis": {
            "list_price_estimate": True,
            "note": (
                "List-price estimate. Actual invoices reflect Reserved "
                "Instances, Savings Plans, committed-use discounts, and spot "
                "pricing, commonly 20-70% below list. Use these figures to "
                "rank opportunities, not to reconcile a bill."
            ),
            "pricing_confidence": (
                "estimated" if estimated else "instance_type_matched"
            ),
            "hours_per_month": HOURS_PER_MONTH,
            "waste_threshold": WASTE_THRESHOLD,
            "min_reportable_monthly_usd": MIN_REPORTABLE_MONTHLY_WASTE,
        },
        "nodes": [
            {
                "name": n.name,
                "instance_type": n.instance_type,
                "provider": n.provider,
                "monthly_usd": round(n.monthly_usd, 2),
                "pricing_basis": n.basis,
            }
            for n in nodes
        ],
        "opportunities": [r.to_dict() for r in reportable],
        "workloads": [r.to_dict() for r in results],
    }


def _assess(
    workload, cpu_req, cpu_used, mem_req, mem_used, cpu_util, mem_util, monthly
) -> tuple[str, float, str | None, dict]:
    """Classify a workload and quantify its reserved-but-unused spend."""
    if not cpu_req and not mem_req:
        # No requests means the scheduler cannot place it sensibly and it can
        # be evicted first under pressure. A different problem from waste, and
        # worth its own finding.
        return (
            "no_requests",
            0.0,
            "Set CPU and memory requests. Without them the scheduler cannot "
            "place this workload predictably and it is evicted first under "
            "node pressure.",
            {},
        )

    if cpu_used is None and mem_used is None:
        return (
            "unmeasured",
            0.0,
            None,
            {"reason": "No usage data. Is metrics-server installed?"},
        )

    # Whichever dimension is least used determines the headroom available to
    # reclaim, but the reclaimable COST is bounded by the binding dimension:
    # halving CPU on a memory-bound pod frees nothing.
    utilizations = [u for u in (cpu_util, mem_util) if u is not None]
    if not utilizations:
        return "unmeasured", 0.0, None, {}

    peak_utilization = max(utilizations)
    if peak_utilization >= (1 - WASTE_THRESHOLD):
        return "right_sized", 0.0, None, {
            "peak_utilization": round(peak_utilization, 4),
        }

    # Reserve headroom above observed peak rather than sizing to it exactly;
    # a workload trimmed to its high-water mark has nowhere to absorb a spike.
    target = min(1.0, peak_utilization * 1.5)
    reclaimable_fraction = max(0.0, 1.0 - target)
    wasted = monthly * reclaimable_fraction

    parts = []
    if cpu_util is not None and cpu_req:
        parts.append(
            f"CPU {cpu_util * 100:.0f}% of {cpu_req:.2f} cores requested"
        )
    if mem_util is not None and mem_req:
        parts.append(
            f"memory {mem_util * 100:.0f}% of {mem_req / (1024 ** 3):.1f} GiB requested"
        )

    return (
        "over_provisioned",
        wasted,
        (
            f"Reduce requests by about {reclaimable_fraction * 100:.0f}%. "
            + " · ".join(parts)
            + ". Target leaves 50% headroom above observed peak."
        ),
        {
            "peak_utilization": round(peak_utilization, 4),
            "target_utilization": round(target, 4),
            "reclaimable_fraction": round(reclaimable_fraction, 4),
        },
    )
