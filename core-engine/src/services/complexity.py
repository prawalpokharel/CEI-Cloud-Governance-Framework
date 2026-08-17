"""
Configuration complexity: the entropy nobody budgeted for.

Clusters accumulate kinds, namespaces, policies, and dependencies faster
than anyone prunes them, and complexity is a risk multiplier with no
dashboard: every incident takes longer to reason about in a system nobody
can hold in their head.

The index here is deliberately made of parts a platform team can act on,
each normalised to [0, 1] and reported alongside the composite -- an opaque
single number would tell nobody what to simplify.

* **Scale** -- objects under management (log-scaled; 40 objects and 4000 are
  different worlds, 4000 and 4400 are not).
* **Diversity** -- Shannon entropy over resource kinds. A cluster of 300
  Deployments is big; a cluster of 15 kinds is complicated.
* **Wiring** -- edge density plus the share of cross-namespace edges, which
  cost more comprehension than local ones.
* **Spread** -- namespace count, log-scaled.

The interesting number is the trend: complexity GROWTH is the leading
indicator (the drift rail is the delivery vehicle for that, like everything
else). The absolute index mostly ranks clusters against each other.
"""

from __future__ import annotations

import math
from typing import Any


def _shannon(counts: list[int]) -> float:
    """Normalised Shannon entropy in [0, 1]."""
    total = sum(counts)
    if total <= 0 or len(counts) <= 1:
        return 0.0
    entropy = -sum(
        (c / total) * math.log2(c / total) for c in counts if c > 0
    )
    return entropy / math.log2(len(counts))


def _log_scale(value: float, knee: float) -> float:
    """0 at 0, ~0.5 at the knee, asymptotic to 1. Log because scale is."""
    if value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(knee) / 2)


def index(snapshot: dict, terraform_state: dict | str | None = None) -> dict[str, Any]:
    """
    The configuration complexity index for one snapshot.
    """
    workloads = snapshot.get("workloads") or []
    edges = snapshot.get("edges") or []

    kind_counts: dict[str, int] = {}
    namespaces: set[str] = set()
    for workload in workloads:
        kind_counts[workload.get("kind") or "?"] = (
            kind_counts.get(workload.get("kind") or "?", 0) + 1
        )
        if workload.get("namespace"):
            namespaces.add(workload["namespace"])

    object_count = (
        len(workloads)
        + len(snapshot.get("services") or [])
        + len(snapshot.get("ingresses") or [])
        + len(snapshot.get("network_policies") or [])
        + len(snapshot.get("disruption_budgets") or [])
        + len(snapshot.get("autoscalers") or [])
    )

    # Terraform widens the picture when supplied; absence just narrows scope.
    tf_types = 0
    if terraform_state:
        from .external_deps import parse_terraform_state
        tf_types = len({r["type"] for r in parse_terraform_state(terraform_state)})

    workload_keys = {w.get("key") for w in workloads}
    cross_namespace = 0
    real_edges = 0
    for edge in edges:
        source, target = edge.get("source"), edge.get("target")
        if source in workload_keys and target in workload_keys:
            real_edges += 1
            if str(source).split("/")[0] != str(target).split("/")[0]:
                cross_namespace += 1

    n = max(1, len(workloads))
    max_edges = n * (n - 1)
    subscores = {
        "scale": round(_log_scale(object_count + tf_types * 3, knee=200), 3),
        "diversity": round(_shannon(list(kind_counts.values())), 3),
        "wiring": round(
            min(1.0, (real_edges / max_edges if max_edges else 0) * 10)
            * (1 + (cross_namespace / real_edges if real_edges else 0)) / 2,
            3,
        ),
        "spread": round(_log_scale(len(namespaces), knee=15), 3),
    }
    composite = round(sum(subscores.values()) / len(subscores), 3)

    return {
        "index": composite,
        "subscores": subscores,
        "counts": {
            "objects": object_count,
            "workload_kinds": len(kind_counts),
            "namespaces": len(namespaces),
            "dependency_edges": real_edges,
            "cross_namespace_edges": cross_namespace,
            "terraform_resource_types": tf_types,
        },
        "note": (
            "Subscores are the actionable part; the composite ranks. The "
            "number that predicts trouble is the trend -- watch this on the "
            "drift rail, not as a one-off reading."
        ),
    }
