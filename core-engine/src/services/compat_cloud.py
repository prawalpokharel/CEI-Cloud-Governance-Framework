"""
Local implementation of the legacy backend's cloud-connect demo.

The marketing site's /connect page talks to a hosted Node backend that is not
in this repository. That backend's cloud connections were always DEMO
connections -- the frontend renders a MOCK badge from the provider metadata
it returns -- so a local implementation with the same contract is faithful to
the original design rather than an imitation of something real.

Everything here is deterministic and labelled: fixed provider topologies
(seeded, so two runs render identically), `mock: true` on every provider,
`source: "mock discovery (local demo)"` on every topology. Connections live
in process memory because that is exactly the persistence a demo needs --
a restart resets the demo, which is a feature.

The analysis, however, is entirely real: the mock topology is run through
the same CEI pipeline (`run_analysis`) as every other entry point, so the
numbers on the connect page are genuine pipeline output over sample data,
not sample output.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any

PROVIDERS = [
    {"key": "aws", "name": "Amazon Web Services", "mock": True},
    {"key": "gcp", "name": "Google Cloud Platform", "mock": True},
    {"key": "azure", "name": "Microsoft Azure", "mock": True},
]
PROVIDER_KEYS = {p["key"] for p in PROVIDERS}

# Provider-flavoured sample estates. Shapes chosen to exercise the pipeline:
# each has an entry tier, a shared middle service, and stateful leaves, so
# CEI produces a meaningful ranking rather than a flat one.
_TOPOLOGY_SPECS: dict[str, list[tuple[str, str, list[str]]]] = {
    "aws": [
        ("api-gateway", "web", ["orders-service", "users-service"]),
        ("orders-service", "app", ["orders-db", "payments-service", "cache"]),
        ("users-service", "app", ["users-db", "cache"]),
        ("payments-service", "app", ["payments-db", "queue"]),
        ("reporting", "app", ["orders-db"]),
        ("orders-db", "data", []),
        ("users-db", "data", []),
        ("payments-db", "data", []),
        ("cache", "data", []),
        ("queue", "data", []),
    ],
    "gcp": [
        ("ingress-lb", "web", ["frontend-svc"]),
        ("frontend-svc", "web", ["catalog-svc", "checkout-svc"]),
        ("catalog-svc", "app", ["catalog-sql", "search"]),
        ("checkout-svc", "app", ["orders-sql", "pubsub"]),
        ("recommender", "app", ["catalog-sql"]),
        ("catalog-sql", "data", []),
        ("orders-sql", "data", []),
        ("search", "data", []),
        ("pubsub", "data", []),
    ],
    "azure": [
        ("front-door", "web", ["portal-app"]),
        ("portal-app", "app", ["core-api"]),
        ("core-api", "app", ["sql-primary", "service-bus", "redis"]),
        ("batch-worker", "app", ["sql-primary", "service-bus"]),
        ("sql-primary", "data", []),
        ("service-bus", "data", []),
        ("redis", "data", []),
    ],
}

# In-memory demo state. Deliberately process-local: restarting the server
# resets the demo, and nothing about a mock connection deserves a table.
_connected: dict[str, dict[str, Any]] = {}


def providers() -> dict[str, Any]:
    return {"providers": PROVIDERS}


def status() -> dict[str, Any]:
    return {"connected": [
        {"provider": key, **meta} for key, meta in sorted(_connected.items())
    ]}


def connect(provider: str) -> bool:
    if provider not in PROVIDER_KEYS:
        return False
    _connected[provider] = {
        "mock": True,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    return True


def disconnect(provider: str) -> None:
    _connected.pop(provider, None)


def build_topology(provider: str) -> dict[str, Any] | None:
    """
    The provider's sample estate, in the /analyze telemetry shape.

    Seeded per provider: the same topology renders on every visit, which is
    what makes a demo demonstrable. History is generated the same way the
    pipeline's scenario path does, so the entropy term operates on plausible
    series rather than constants.
    """
    spec = _TOPOLOGY_SPECS.get(provider)
    if spec is None:
        return None
    rng = random.Random(f"cloudoptimizer-demo-{provider}")

    nodes = []
    edges = []
    for name, tier, dependencies in spec:
        base = {"web": 55.0, "app": 45.0, "data": 30.0}[tier]
        utilization = base + rng.uniform(-10, 15)
        nodes.append({
            "node_id": name,
            "id": name,      # the connect page renders n.id and n.tier
            "tier": tier,
            "metrics": {
                "cpu_utilization": round(utilization, 1),
                "memory_utilization": round(utilization + rng.uniform(-8, 12), 1),
                "network_throughput": round(rng.uniform(10, 400), 1),
                "disk_io": round(rng.uniform(5, 120), 1),
            },
            "utilization_history": [
                {
                    "cpu": max(0.02, min(0.98, (utilization + rng.gauss(0, 6)) / 100)),
                    "memory": max(0.02, min(0.98, (utilization + rng.gauss(0, 8)) / 100)),
                }
                for _ in range(40)
            ],
            "tags": {"criticality": "operational" if tier != "data" else "mission_critical"},
        })
        for dependency in dependencies:
            edges.append({
                "source": name,
                "target": dependency,
                "weight": 1.0,
                "type": "runtime",
            })

    return {
        "provider": provider,
        "source": "mock discovery (local demo)",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "mock": True,
        "topology": {"nodes": nodes, "edges": edges},
    }
