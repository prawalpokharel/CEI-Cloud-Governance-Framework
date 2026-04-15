"""
Cloud provider on-demand pricing tables.

Prices are USD/hour for the smallest unit of the resource (one instance,
one node, one pod). Numbers are reasonable mid-2025 averages for
us-east-1 / eastus / us-central1; precise list pricing varies by region
and Reserved/Spot discount tier — these tables are intended to drive
order-of-magnitude rightsizing recommendations, not invoice-accurate
billing. A real deployment would pull from each provider's pricing API.

Schema:
    INSTANCE_PRICES[provider][instance_type] = {
        "hourly_usd": float,
        "vcpu": int,
        "memory_gib": int,
        "tier": "burstable" | "general" | "compute" | "memory" | "gpu" | "k8s",
    }
"""

from typing import Dict, List

HOURS_PER_MONTH = 730  # AWS billing convention

INSTANCE_PRICES: Dict[str, Dict[str, Dict]] = {
    "aws": {
        # Burstable (T family)
        "t3.small":    {"hourly_usd": 0.0208, "vcpu": 2,  "memory_gib": 2,   "tier": "burstable"},
        "t3.medium":   {"hourly_usd": 0.0416, "vcpu": 2,  "memory_gib": 4,   "tier": "burstable"},
        "t3.large":    {"hourly_usd": 0.0832, "vcpu": 2,  "memory_gib": 8,   "tier": "burstable"},
        "t3.xlarge":   {"hourly_usd": 0.1664, "vcpu": 4,  "memory_gib": 16,  "tier": "burstable"},
        # General-purpose (M family)
        "m5.large":    {"hourly_usd": 0.096,  "vcpu": 2,  "memory_gib": 8,   "tier": "general"},
        "m5.xlarge":   {"hourly_usd": 0.192,  "vcpu": 4,  "memory_gib": 16,  "tier": "general"},
        "m5.2xlarge":  {"hourly_usd": 0.384,  "vcpu": 8,  "memory_gib": 32,  "tier": "general"},
        "m5.4xlarge":  {"hourly_usd": 0.768,  "vcpu": 16, "memory_gib": 64,  "tier": "general"},
        # Compute-optimized (C family)
        "c5.large":    {"hourly_usd": 0.085,  "vcpu": 2,  "memory_gib": 4,   "tier": "compute"},
        "c5.xlarge":   {"hourly_usd": 0.17,   "vcpu": 4,  "memory_gib": 8,   "tier": "compute"},
        "c5.2xlarge":  {"hourly_usd": 0.34,   "vcpu": 8,  "memory_gib": 16,  "tier": "compute"},
        # Memory-optimized (R family)
        "r5.large":    {"hourly_usd": 0.126,  "vcpu": 2,  "memory_gib": 16,  "tier": "memory"},
        "r5.xlarge":   {"hourly_usd": 0.252,  "vcpu": 4,  "memory_gib": 32,  "tier": "memory"},
        # GPU
        "p3.2xlarge":  {"hourly_usd": 3.06,   "vcpu": 8,  "memory_gib": 61,  "tier": "gpu"},
        "p4d.24xlarge":{"hourly_usd": 32.77,  "vcpu": 96, "memory_gib": 1152,"tier": "gpu"},
    },
    "azure": {
        "Standard_B2ms":     {"hourly_usd": 0.0832, "vcpu": 2,  "memory_gib": 8,   "tier": "burstable"},
        "Standard_B4ms":     {"hourly_usd": 0.1664, "vcpu": 4,  "memory_gib": 16,  "tier": "burstable"},
        "Standard_D2s_v3":   {"hourly_usd": 0.096,  "vcpu": 2,  "memory_gib": 8,   "tier": "general"},
        "Standard_D4s_v3":   {"hourly_usd": 0.192,  "vcpu": 4,  "memory_gib": 16,  "tier": "general"},
        "Standard_D8s_v3":   {"hourly_usd": 0.384,  "vcpu": 8,  "memory_gib": 32,  "tier": "general"},
        "Standard_F4s_v2":   {"hourly_usd": 0.169,  "vcpu": 4,  "memory_gib": 8,   "tier": "compute"},
        "Standard_F8s_v2":   {"hourly_usd": 0.338,  "vcpu": 8,  "memory_gib": 16,  "tier": "compute"},
        "Standard_E4s_v3":   {"hourly_usd": 0.252,  "vcpu": 4,  "memory_gib": 32,  "tier": "memory"},
        "Standard_E8s_v3":   {"hourly_usd": 0.504,  "vcpu": 8,  "memory_gib": 64,  "tier": "memory"},
        "Standard_NC6s_v3":  {"hourly_usd": 3.06,   "vcpu": 6,  "memory_gib": 112, "tier": "gpu"},
    },
    "gcp": {
        "e2-small":         {"hourly_usd": 0.0167, "vcpu": 2,  "memory_gib": 2,   "tier": "burstable"},
        "e2-medium":        {"hourly_usd": 0.0335, "vcpu": 2,  "memory_gib": 4,   "tier": "burstable"},
        "e2-standard-2":    {"hourly_usd": 0.067,  "vcpu": 2,  "memory_gib": 8,   "tier": "general"},
        "n2-standard-2":    {"hourly_usd": 0.097,  "vcpu": 2,  "memory_gib": 8,   "tier": "general"},
        "n2-standard-4":    {"hourly_usd": 0.194,  "vcpu": 4,  "memory_gib": 16,  "tier": "general"},
        "n2-standard-8":    {"hourly_usd": 0.388,  "vcpu": 8,  "memory_gib": 32,  "tier": "general"},
        "c2-standard-4":    {"hourly_usd": 0.21,   "vcpu": 4,  "memory_gib": 16,  "tier": "compute"},
        "c2-standard-8":    {"hourly_usd": 0.42,   "vcpu": 8,  "memory_gib": 32,  "tier": "compute"},
        "n2-highmem-4":     {"hourly_usd": 0.262,  "vcpu": 4,  "memory_gib": 32,  "tier": "memory"},
        "a2-highgpu-1g":    {"hourly_usd": 3.67,   "vcpu": 12, "memory_gib": 85,  "tier": "gpu"},
    },
    "kubernetes": {
        # Treat pod sizes as flat per-resource cost (informational only).
        "pod-sm":   {"hourly_usd": 0.012, "vcpu": 1,  "memory_gib": 2,  "tier": "k8s"},
        "pod-md":   {"hourly_usd": 0.024, "vcpu": 2,  "memory_gib": 4,  "tier": "k8s"},
        "pod-lg":   {"hourly_usd": 0.060, "vcpu": 4,  "memory_gib": 8,  "tier": "k8s"},
        "pod-xl":   {"hourly_usd": 0.120, "vcpu": 8,  "memory_gib": 16, "tier": "k8s"},
    },
}


def list_supported_providers() -> List[str]:
    return list(INSTANCE_PRICES.keys())


def monthly_cost(provider: str, instance_type: str, replicas: int = 1) -> float:
    """Return USD/month for a given instance type at the given replica count."""
    p = INSTANCE_PRICES.get(provider, {}).get(instance_type)
    if not p:
        return 0.0
    return round(p["hourly_usd"] * HOURS_PER_MONTH * max(0, replicas), 2)


def cheaper_alternatives(provider: str, instance_type: str, target_vcpu: int = None) -> List[Dict]:
    """
    Return all instance types in the same provider that have at least
    target_vcpu (or the original's vCPU if target_vcpu is None) and are
    strictly cheaper than the original. Sorted ascending by hourly cost.
    """
    table = INSTANCE_PRICES.get(provider, {})
    original = table.get(instance_type)
    if not original:
        return []
    min_vcpu = target_vcpu if target_vcpu is not None else original["vcpu"]
    candidates = []
    for name, spec in table.items():
        if name == instance_type:
            continue
        if spec["vcpu"] < min_vcpu:
            continue
        if spec["hourly_usd"] >= original["hourly_usd"]:
            continue
        candidates.append({"instance_type": name, **spec})
    candidates.sort(key=lambda c: c["hourly_usd"])
    return candidates
