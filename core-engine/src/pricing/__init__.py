"""Pricing engine: cost tables, rightsizing, HPA-vs-CEI benchmark.

This module implements the cost-savings layer described in roadmap PR 7.
It is intentionally self-contained so the rest of the CEI pipeline does
not depend on any pricing detail — analyze() can run with or without
this module loaded.
"""

from .cost_tables import (
    INSTANCE_PRICES,
    list_supported_providers,
    monthly_cost,
)
from .savings_calculator import (
    SavingsRecommendation,
    compute_savings,
)
from .benchmark import (
    BenchmarkResult,
    run_hpa_vs_cei,
)

__all__ = [
    "INSTANCE_PRICES",
    "list_supported_providers",
    "monthly_cost",
    "SavingsRecommendation",
    "compute_savings",
    "BenchmarkResult",
    "run_hpa_vs_cei",
]
