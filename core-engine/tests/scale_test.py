"""
Scale test.

    python -m tests.scale_test --workloads 500

Answers the questions a 20-workload kind cluster cannot: how large does a
snapshot get, how long does ingest take, and does the analysis stay usable as
the graph grows.

Generates a synthetic cluster with realistic shape -- a few shared backends
that many services depend on, a long tail of leaf services, a spread of
utilization -- and drives it through the real ingest path and the real
analysis services. Not a microbenchmark: the point is to find the first thing
that falls over, not to produce a number.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

GIB = 1024 ** 3


def build_snapshot(workload_count: int, seq: int = 1) -> dict:
    """
    Synthesize a cluster of the requested size.

    Shape matters more than size: a graph of isolated nodes would make
    centrality trivial and the timings meaningless. This builds a realistic
    dependency structure -- shared backends, mid-tier services, leaf workers
    -- so the graph algorithms do representative work.
    """
    rng = random.Random(42)

    # Size the node pool to what the workloads actually request. An earlier
    # version used a fixed ratio and generated a cluster requesting 555 cores
    # against 256 allocatable -- impossible in a real cluster, and it made the
    # cost model report 108% waste. Kept deliberate: the generator should
    # produce clusters that could exist.
    #
    # ~65% committed, which is a realistic production figure.
    est_cpu_per_workload = 0.4625 * 2.4   # mean request x mean replicas
    est_mem_per_workload = 0.9 * GIB * 2.4
    node_count = max(
        3,
        int(est_cpu_per_workload * workload_count / (16.0 * 0.65)),
        int(est_mem_per_workload * workload_count / (64 * GIB * 0.65)),
    )
    nodes = [
        {
            "name": f"node-{i}",
            "uid": f"node-uid-{i}",
            "labels": {"node.kubernetes.io/instance-type": "m5.4xlarge"},
            "instance_type": "m5.4xlarge",
            "provider_id": f"aws:///us-east-1a/i-{i:08x}",
            "ready": True,
            "allocatable_cpu_cores": 16.0,
            "allocatable_memory_bytes": 64 * GIB,
            "capacity_cpu_cores": 16.0,
            "capacity_memory_bytes": 64 * GIB,
        }
        for i in range(node_count)
    ]

    # 5% shared backends, 25% mid-tier, the rest leaves.
    backends = max(1, workload_count // 20)
    midtier = max(1, workload_count // 4)

    workloads = []
    pods = []
    for i in range(workload_count):
        namespace = f"team-{i % 12}"
        name = f"svc-{i}"
        key = f"{namespace}/Deployment/{name}"
        replicas = rng.choice([1, 1, 2, 3, 5])

        # Deliberately skewed: most workloads over-provisioned, a few hot.
        utilization = rng.choice([0.02, 0.05, 0.1, 0.2, 0.4, 0.75, 0.9])
        cpu_req_pod = rng.choice([0.1, 0.25, 0.5, 1.0])
        mem_req_pod = rng.choice([GIB // 4, GIB // 2, GIB, 2 * GIB])

        workloads.append({
            "key": key,
            "name": name,
            "namespace": namespace,
            "kind": "Deployment",
            "uid": f"wl-{i}",
            "labels": {"app": name},
            "pod_labels": {"app": name},
            "images": [f"registry.example.com/{name}:1.0.0"],
            "replicas_desired": replicas,
            "replicas_ready": replicas,
            "cpu_cores_requested": cpu_req_pod * replicas,
            "memory_bytes_requested": mem_req_pod * replicas,
            "cpu_cores_requested_per_pod": cpu_req_pod,
            "memory_bytes_requested_per_pod": mem_req_pod,
            "cpu_cores_used": cpu_req_pod * replicas * utilization,
            "memory_bytes_used": int(mem_req_pod * replicas * utilization),
            "pods_measured": replicas,
            "env_summary": {"count": 8, "sensitive_count": 1, "names": ["PORT"]},
            "service_references": [],
            "service_account": "default",
            "node_selector": {},
        })

        for r in range(replicas):
            pods.append({
                "name": f"{name}-{rng.randrange(16**8):08x}-{r}",
                "namespace": namespace,
                "phase": "Running",
                "node_name": f"node-{i % node_count}",
                "labels": {"app": name},
                "restart_count": rng.choice([0, 0, 0, 0, 1, 7]),
                "waiting_reasons": [],
                "last_terminated_reasons": rng.choice([[], [], [], ["Error"]]),
                "owner_kind": "ReplicaSet",
                "owner_name": name,
                "ready": True,
            })

    keys = [w["key"] for w in workloads]
    edges = []
    for i, key in enumerate(keys):
        if i < backends:
            continue  # backends depend on nothing
        if i < backends + midtier:
            targets = rng.sample(keys[:backends], k=min(3, backends))
        else:
            pool = keys[:backends + midtier]
            targets = rng.sample(pool, k=min(2, len(pool)))
        for target in targets:
            if target != key:
                edges.append({
                    "source": key,
                    "target": target,
                    "confidence": 0.9,
                    "source_kind": "env_reference",
                })

    services = [
        {
            "name": w["name"],
            "namespace": w["namespace"],
            "uid": f"svc-{i}",
            "type": "ClusterIP",
            "selector": {"app": w["name"]},
            "ports": [{"port": 8080, "target_port": "8080",
                       "protocol": "TCP", "name": "http"}],
            "cluster_ip": f"10.96.{i // 256}.{i % 256}",
        }
        for i, w in enumerate(workloads)
    ]

    return {
        "schema_version": 1,
        "agent_version": "0.1.0-scaletest",
        "seq": seq,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "cluster": {
            "uid": "scale-test-cluster",
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


def _timed(label: str, fn):
    start = time.perf_counter()
    result = fn()
    elapsed = (time.perf_counter() - start) * 1000
    return label, elapsed, result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workloads", type=int, default=500)
    parser.add_argument("--history", type=int, default=30,
                        help="Samples per workload for the entropy path")
    args = parser.parse_args()

    from src.services.cost import analyze_cluster_cost
    from src.services.health import diagnose
    from src.services.live_cei import compute_live_cei

    print("=" * 72)
    print(f"SCALE TEST — {args.workloads} workloads")
    print("=" * 72)

    _, build_ms, snapshot = _timed("build", lambda: build_snapshot(args.workloads))
    raw = json.dumps(snapshot, separators=(",", ":")).encode()
    compressed = gzip.compress(raw, compresslevel=6)

    print("\n[payload]")
    print(f"  workloads          {len(snapshot['workloads'])}")
    print(f"  pods               {len(snapshot['pods'])}")
    print(f"  edges              {len(snapshot['edges'])}")
    print(f"  nodes              {len(snapshot['nodes'])}")
    print(f"  uncompressed       {len(raw) / 1024:.0f} KiB")
    print(f"  gzipped            {len(compressed) / 1024:.0f} KiB "
          f"({len(compressed) / len(raw) * 100:.0f}%)")
    print(f"  per 60s per cluster {len(compressed) * 60 / 1024 / 1024:.1f} MiB/hour")

    history = {
        w["key"]: [
            {
                "cpu_cores_used": w["cpu_cores_used"] * (0.8 + 0.4 * (i % 5) / 5),
                "cpu_cores_requested": w["cpu_cores_requested"],
                "mem_bytes_used": w["memory_bytes_used"],
                "mem_bytes_requested": w["memory_bytes_requested"],
            }
            for i in range(args.history)
        ]
        for w in snapshot["workloads"]
    }

    print("\n[analysis]")
    timings = []
    for label, fn in [
        ("CEI (blast_radius)", lambda: compute_live_cei(snapshot, history)),
        ("CEI (no history)", lambda: compute_live_cei(snapshot, {})),
        ("cost allocation", lambda: analyze_cluster_cost(snapshot)),
        ("health diagnostics", lambda: diagnose(snapshot)),
    ]:
        name, ms, result = _timed(label, fn)
        timings.append((name, ms))
        print(f"  {name:22s} {ms:8.1f} ms")

    cei = compute_live_cei(snapshot, history)
    cost = analyze_cluster_cost(snapshot)
    health = diagnose(snapshot, {n["node_id"]: n for n in cei.nodes})

    print("\n[results]")
    print(f"  entropy ready       {cei.entropy_ready} ({cei.entropy_sample_count} samples)")
    print(f"  cluster cost        ${cost['summary']['cluster_monthly_usd']:,.0f}/mo")
    print(f"  waste found         ${cost['summary']['wasted_monthly_usd']:,.0f}/mo "
          f"({cost['summary']['waste_as_pct_of_cluster']}%)")
    print(f"  opportunities       {len(cost['opportunities'])}")
    print(f"  health findings     {health['summary']['total']} "
          f"({health['summary']['critical']} critical)")

    scores = [n["cei_score"] for n in cei.nodes]
    print(f"  CEI spread          {min(scores):.3f} – {max(scores):.3f} "
          f"(median {statistics.median(scores):.3f})")

    print("\n[verdict]")
    total_ms = sum(ms for _, ms in timings)
    problems = []
    if len(compressed) > 5 * 1024 * 1024:
        problems.append(f"gzipped payload {len(compressed) / 1024 / 1024:.1f} MiB "
                        "— approaching the 8 MiB ingest limit")
    for name, ms in timings:
        if ms > 5000:
            problems.append(f"{name} took {ms / 1000:.1f}s — too slow for a request")
    if len(snapshot["workloads"]) > 300:
        problems.append(
            f"{len(snapshot['workloads'])} nodes in the topology map — a "
            "force-directed D3 graph is unreadable and slow above ~300; the "
            "UI needs namespace filtering or aggregation"
        )
    if not problems:
        print("  No limits hit.")
    for p in problems:
        print(f"  ! {p}")
    print(f"\n  total analysis {total_ms:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
