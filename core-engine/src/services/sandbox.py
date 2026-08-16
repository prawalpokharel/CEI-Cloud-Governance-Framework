"""
Sandbox mode.

A realistic cluster with no install required, so the product can be
demonstrated before anyone has a cluster to point it at -- in a sales call, on
the marketing site, or by an evaluator who wants to see the output before
granting an agent a ServiceAccount.

Built from the same snapshot shape the agent produces and fed through the same
analysis path, so what a visitor sees is what the product actually computes.
A mocked screenshot would drift from reality within a week; this cannot.

Deterministic by construction (fixed seed, fixed clock), so the demo shows the
same numbers every time. A demo whose headline figure changes between two
people loading it is not a demo.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

GIB = 1024 ** 3

# Fixed so every visitor sees identical numbers.
SANDBOX_SEED = 20260816
SANDBOX_EPOCH = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)

# A plausible mid-size e-commerce platform: shared backends, a payments path
# under compliance constraints, a data pipeline, and the usual long tail of
# under-loved internal tools where the waste actually lives.
_TOPOLOGY = [
    # (name, namespace, replicas, cpu_req_per_pod, mem_gib_per_pod, utilization, depends_on)
    ("ingress-nginx",      "ingress",   3, 0.5,  1.0, 0.55, []),
    ("storefront",         "shop",      6, 1.0,  2.0, 0.62, ["catalog-api", "cart-api", "search-api", "recommendations"]),
    ("checkout",           "shop",      4, 1.0,  2.0, 0.48, ["cart-api", "payments", "inventory", "notifications"]),
    ("catalog-api",        "shop",      5, 0.5,  1.0, 0.71, ["postgres-primary", "redis-cache"]),
    ("cart-api",           "shop",      4, 0.5,  1.0, 0.44, ["redis-cache", "postgres-primary"]),
    ("search-api",         "shop",      3, 2.0,  4.0, 0.31, ["elasticsearch"]),
    ("recommendations",    "shop",      2, 2.0,  8.0, 0.09, ["feature-store", "postgres-replica"]),
    ("payments",           "payments",  4, 0.5,  1.0, 0.38, ["postgres-primary", "fraud-scoring"]),
    ("fraud-scoring",      "payments",  2, 4.0,  8.0, 0.22, ["feature-store"]),
    ("inventory",          "shop",      3, 0.5,  1.0, 0.51, ["postgres-primary"]),
    ("notifications",      "platform",  2, 0.25, 0.5, 0.18, ["redis-cache"]),
    ("postgres-primary",   "data",      1, 4.0, 16.0, 0.67, []),
    ("postgres-replica",   "data",      2, 4.0, 16.0, 0.29, []),
    ("redis-cache",        "data",      3, 1.0,  4.0, 0.41, []),
    ("elasticsearch",      "data",      3, 2.0,  8.0, 0.36, []),
    ("feature-store",      "data",      2, 2.0,  8.0, 0.14, ["postgres-replica"]),
    ("etl-nightly",        "data",      4, 4.0,  8.0, 0.04, ["postgres-replica", "elasticsearch"]),
    ("analytics-worker",   "data",      6, 2.0,  4.0, 0.07, ["postgres-replica"]),
    ("legacy-reports",     "internal",  2, 2.0,  4.0, 0.02, ["postgres-replica"]),
    ("admin-portal",       "internal",  2, 0.5,  2.0, 0.06, ["postgres-primary"]),
    ("grafana",            "observability", 1, 0.5, 1.0, 0.23, []),
    ("prometheus",         "observability", 1, 2.0, 8.0, 0.58, []),
    ("log-shipper",        "observability", 8, 0.25, 0.5, 0.33, []),
    ("ci-runner",          "internal",  4, 2.0,  4.0, 0.11, []),
    ("staging-storefront", "staging",   2, 1.0,  2.0, 0.03, ["staging-catalog"]),
    ("staging-catalog",    "staging",   2, 0.5,  1.0, 0.02, []),
]

# Deliberate faults, so the health panel has something to rank. Chosen to
# demonstrate the ordering claim: a crash loop in a shared backend outranks
# one in a scratch namespace.
_FAULTS = {
    "recommendations": {"restarts": 14, "terminated": ["OOMKilled"]},
    "etl-nightly": {"restarts": 6, "terminated": ["Error"]},
    "staging-catalog": {"restarts": 9, "terminated": ["Error"]},
    "legacy-reports": {"waiting": ["ImagePullBackOff"], "phase": "Pending"},
}


def _key(namespace: str, name: str) -> str:
    return f"{namespace}/Deployment/{name}"


def build_sandbox_snapshot() -> dict[str, Any]:
    """Produce a snapshot in exactly the shape the agent emits."""
    rng = random.Random(SANDBOX_SEED)
    by_name = {row[0]: row for row in _TOPOLOGY}

    total_cpu = sum(r[2] * r[3] for r in _TOPOLOGY)
    total_mem = sum(r[2] * r[4] for r in _TOPOLOGY)
    # ~70% committed, which is what a healthy production cluster looks like.
    node_count = max(3, int(total_cpu / (16 * 0.70)) + 1)

    nodes = [
        {
            "name": f"ip-10-0-{i // 8}-{(i * 37) % 256}.ec2.internal",
            "uid": f"sandbox-node-{i}",
            "labels": {
                "node.kubernetes.io/instance-type": "m5.4xlarge",
                "topology.kubernetes.io/region": "us-east-1",
                "topology.kubernetes.io/zone": f"us-east-1{'abc'[i % 3]}",
                "eks.amazonaws.com/nodegroup": "general",
            },
            "instance_type": "m5.4xlarge",
            "provider_id": f"aws:///us-east-1{'abc'[i % 3]}/i-{i:012x}",
            "ready": True,
            "unschedulable": False,
            "allocatable_cpu_cores": 15.89,
            "allocatable_memory_bytes": int(61.5 * GIB),
            "capacity_cpu_cores": 16.0,
            "capacity_memory_bytes": 64 * GIB,
            "kubelet_version": "v1.29.6-eks-1552ad0",
            "architecture": "amd64",
            "region": "us-east-1",
            "zone": f"us-east-1{'abc'[i % 3]}",
        }
        for i in range(node_count)
    ]

    workloads = []
    pods = []
    services = []
    for index, (name, namespace, replicas, cpu_pod, mem_gib_pod, util, _deps) in enumerate(
        _TOPOLOGY
    ):
        mem_pod = int(mem_gib_pod * GIB)
        fault = _FAULTS.get(name, {})

        workloads.append({
            "key": _key(namespace, name),
            "name": name,
            "namespace": namespace,
            "kind": "Deployment",
            "uid": f"sandbox-wl-{index}",
            "labels": {"app": name},
            "pod_labels": {"app": name},
            "images": [f"registry.internal/{name}:2.14.{index}"],
            "replicas_desired": replicas,
            # A crash-looping workload does not have all replicas ready.
            "replicas_ready": max(0, replicas - 1) if fault else replicas,
            "cpu_cores_requested": round(cpu_pod * replicas, 3),
            "memory_bytes_requested": mem_pod * replicas,
            "cpu_cores_requested_per_pod": cpu_pod,
            "memory_bytes_requested_per_pod": mem_pod,
            "cpu_cores_used": round(cpu_pod * replicas * util, 4),
            "memory_bytes_used": int(mem_pod * replicas * util * 1.15),
            "pods_measured": replicas,
            "env_summary": {
                "count": rng.randint(4, 14),
                "sensitive_count": rng.randint(0, 3),
                "names": ["PORT", "LOG_LEVEL", "<redacted>"],
            },
            "service_references": [],
            "service_account": name,
            "node_selector": {},
        })

        services.append({
            "name": name,
            "namespace": namespace,
            "uid": f"sandbox-svc-{index}",
            "type": "ClusterIP",
            "selector": {"app": name},
            "ports": [{"port": 8080, "target_port": "8080",
                       "protocol": "TCP", "name": "http"}],
            "cluster_ip": f"172.20.{index // 256}.{index % 256}",
        })

        for replica in range(replicas):
            pods.append({
                "name": f"{name}-{abs(hash((name, replica))) % 0xfffffff:07x}-{replica}",
                "namespace": namespace,
                "phase": fault.get("phase", "Running"),
                "node_name": nodes[(index + replica) % len(nodes)]["name"],
                "labels": {"app": name},
                "restart_count": fault.get("restarts", 0) if replica == 0 else 0,
                "waiting_reasons": fault.get("waiting", []) if replica == 0 else [],
                "last_terminated_reasons": (
                    fault.get("terminated", []) if replica == 0 else []
                ),
                "owner_kind": "ReplicaSet",
                "owner_name": name,
                "ready": not (fault and replica == 0),
            })

    edges = []
    for name, namespace, *_rest in _TOPOLOGY:
        for dependency in by_name[name][6]:
            target = by_name.get(dependency)
            if target:
                edges.append({
                    "source": _key(namespace, name),
                    "target": _key(target[1], dependency),
                    "confidence": 0.9,
                    "source_kind": "env_reference",
                })

    return {
        "schema_version": 1,
        "agent_version": "0.1.0",
        "seq": 1,
        "captured_at": SANDBOX_EPOCH.isoformat(),
        "cluster": {
            "uid": "sandbox-cluster",
            "provider": "eks",
            "kubernetes_version": "1.29",
            "node_count": len(nodes),
            "metrics_available": True,
            "metrics_reason": None,
        },
        "nodes": nodes,
        "workloads": workloads,
        "services": services,
        "pods": pods,
        "ingresses": [],
        "network_policies": [],
        "edges": edges,
        "summary": {
            "nodes": len(nodes),
            "workloads": len(workloads),
            "services": len(services),
            "pods": len(pods),
            "edges": {"total": len(edges), "by_source": {"env_reference": len(edges)}},
        },
    }


def build_sandbox_history(snapshot: dict, samples: int = 40) -> dict[str, list[dict]]:
    """
    Synthesize enough observation history for the entropy term to engage.

    Each workload gets a distinct variability profile so entropy discriminates
    between them -- batch jobs swing widely, databases are steady. Without
    that the demo would show a real entropy term that happened to be uniform,
    which is a worse impression than showing none.
    """
    rng = random.Random(SANDBOX_SEED + 1)
    history: dict[str, list[dict]] = {}

    for workload in snapshot["workloads"]:
        base_cpu = workload["cpu_cores_used"] or 0.0
        base_mem = workload["memory_bytes_used"] or 0
        name = workload["name"]

        if any(token in name for token in ("etl", "ci-", "analytics", "worker")):
            swing = 0.85     # batch: idle, then spike
        elif any(token in name for token in ("postgres", "redis", "elastic")):
            swing = 0.12     # stateful: steady by design
        else:
            swing = 0.35     # request-serving: diurnal

        points = []
        for i in range(samples):
            factor = 1.0 + swing * rng.uniform(-1.0, 1.0)
            points.append({
                "cpu_cores_used": max(0.0, base_cpu * factor),
                "cpu_cores_requested": workload["cpu_cores_requested"],
                "mem_bytes_used": max(0, int(base_mem * (1 + swing * 0.3 * rng.uniform(-1, 1)))),
                "mem_bytes_requested": workload["memory_bytes_requested"],
            })
        history[workload["key"]] = points

    return history


# --------------------------------------------------------------------------
# Vulnerability scan results
# --------------------------------------------------------------------------

# Modelled on what a real Debian-based image returns: a long tail dominated by
# OS packages, a handful of criticals, and several findings with no published
# fix. The point of the demo is that severity alone does not order these.
_SANDBOX_CVES = [
    # (cve, severity, cvss, package, class, fixed)
    ("CVE-2026-31789", "CRITICAL", 9.8, "openssl", "os-pkgs", "3.0.15-1"),
    ("CVE-2026-31789", "CRITICAL", 9.8, "libssl3", "os-pkgs", "3.0.15-1"),
    ("CVE-2026-22047", "CRITICAL", 9.1, "glibc", "os-pkgs", "2.36-9+deb12u9"),
    ("CVE-2026-19881", "HIGH", 8.8, "libxml2", "os-pkgs", "2.9.14+dfsg-1.3"),
    ("CVE-2026-17402", "HIGH", 7.5, "zlib1g", "os-pkgs", None),
    ("CVE-2026-15990", "HIGH", 7.4, "requests", "lang-pkgs", "2.32.4"),
    ("CVE-2026-11238", "MEDIUM", 6.5, "urllib3", "lang-pkgs", "2.2.3"),
    ("CVE-2026-10087", "MEDIUM", 5.9, "perl-base", "os-pkgs", "5.36.0-7"),
    ("CVE-2025-98221", "MEDIUM", 5.3, "libgcrypt20", "os-pkgs", None),
]

# Which sandbox workloads run which image. Chosen so the demo contains the
# comparison the product is sold on: the same CVE in a shared backend and in
# an internal tool nobody depends on.
_SANDBOX_IMAGES = {
    "registry.internal/catalog-api:2.14.3": ["shop/Deployment/catalog-api"],
    "registry.internal/postgres-primary:15.4": ["data/Deployment/postgres-primary"],
    "registry.internal/payments:2.14.7": ["payments/Deployment/payments"],
    "registry.internal/legacy-reports:1.2.0": ["internal/Deployment/legacy-reports"],
    "registry.internal/ci-runner:3.0.1": ["internal/Deployment/ci-runner"],
}


def build_sandbox_scans() -> list[dict]:
    """Scan results in the shape the scan ingest stores."""
    scans = []
    for index, (reference, workload_keys) in enumerate(sorted(_SANDBOX_IMAGES.items())):
        # Vary the finding set per image so the demo is not five identical rows.
        cves = _SANDBOX_CVES[index % 3:] if index else _SANDBOX_CVES
        vulns = [
            {
                "id": cve,
                "severity": severity,
                "cvss_score": cvss,
                "pkg_name": package,
                "installed_version": "1.0.0",
                "fixed_version": fixed,
                "pkg_class": pkg_class,
                "title": f"{package}: {cve}",
                "primary_url": f"https://avd.aquasec.com/nvd/{cve.lower()}",
            }
            for cve, severity, cvss, package, pkg_class, fixed in cves
        ]
        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
        for v in vulns:
            counts[v["severity"]] += 1
        # A real image carries far more low-severity findings than the
        # scanner transmits; the counts reflect that even though the detail
        # does not.
        counts["LOW"] = 40 + index * 11
        counts["UNKNOWN"] = 8 + index

        scans.append({
            "image_reference": reference,
            "workload_keys": workload_keys,
            "vulnerabilities": vulns,
            "counts": counts,
            "fixable_count": sum(1 for v in vulns if v["fixed_version"]),
            "os_family": "debian",
            "os_name": "12.4",
            "scanned_at": SANDBOX_EPOCH.isoformat(),
            "scan_error": None,
        })
    return scans
