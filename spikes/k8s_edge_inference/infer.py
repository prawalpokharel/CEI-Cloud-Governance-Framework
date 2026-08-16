"""
SPIKE — Kubernetes dependency-edge inference + CEI sanity check.

THIS IS NOT PRODUCT CODE. It exists to answer one question before the agent
is built around an assumption that might be wrong:

    Does CEI, computed over edges we can infer from read-only Kubernetes
    state, produce a ranking a human would agree with?

CEI is a graph algorithm. If the graph is wrong, every number downstream is
wrong -- and the Week 3 demo *is* the graph. One day spent here is cheap
against three weeks spent building on a bad premise.

METHOD
------
Target: Google's Online Boutique (microservices-demo), whose service
dependency graph is publicly documented, so there is a known-correct answer
to check the ranking against.

Edges are inferred only from what a read-only agent can see:

  1. Service -> workload, by label selector.
  2. Workload -> Service, by scanning container env var VALUES for references
     to in-cluster service names (`svc:port`, `svc.ns.svc.cluster.local`).
     This is the load-bearing heuristic and the main thing under test.
  3. Ingress -> Service backends.
  4. ownerReferences, for containment (not treated as dependency).

Deliberately NOT used: ConfigMap bodies (they routinely hold secrets, and
excluding them buys a much stronger RBAC story), and pod-to-pod traffic
(unavailable without eBPF or a service mesh; arrives in Phase 6).

Run:
    python spikes/k8s_edge_inference/infer.py --namespace default
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "core-engine"))

WORKLOAD_KINDS = ["deployments", "statefulsets", "daemonsets"]


# ---------------------------------------------------------------------------
# Cluster read
# ---------------------------------------------------------------------------

def kubectl(args: List[str]) -> dict:
    proc = subprocess.run(
        ["kubectl", *args, "-o", "json"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)} failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def fetch(namespace: str) -> dict:
    ns = ["-n", namespace] if namespace else ["--all-namespaces"]
    out = {"services": kubectl(["get", "services", *ns])["items"]}
    for kind in WORKLOAD_KINDS:
        out[kind] = kubectl(["get", kind, *ns])["items"]
    try:
        out["ingresses"] = kubectl(["get", "ingresses", *ns])["items"]
    except RuntimeError:
        out["ingresses"] = []

    # metrics-server is frequently absent (kind ships without it). Its absence
    # is a supported state, not an error -- record it and degrade.
    try:
        out["podmetrics"] = kubectl(["get", "podmetrics", *ns])["items"]
        out["metrics_available"] = True
    except RuntimeError:
        out["podmetrics"] = []
        out["metrics_available"] = False
    return out


# ---------------------------------------------------------------------------
# Resource parsing
# ---------------------------------------------------------------------------

def parse_cpu(v: Optional[str]) -> Optional[float]:
    """Kubernetes CPU quantity -> cores."""
    if not v:
        return None
    v = str(v)
    if v.endswith("n"):
        return float(v[:-1]) / 1e9
    if v.endswith("u"):
        return float(v[:-1]) / 1e6
    if v.endswith("m"):
        return float(v[:-1]) / 1000.0
    return float(v)


def parse_mem(v: Optional[str]) -> Optional[int]:
    """Kubernetes memory quantity -> bytes."""
    if not v:
        return None
    v = str(v)
    units = {
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
        "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
    }
    for suffix, mult in units.items():
        if v.endswith(suffix):
            return int(float(v[: -len(suffix)]) * mult)
    return int(float(v))


def workload_key(item: dict) -> str:
    md = item["metadata"]
    return f"{md['namespace']}/{item['kind']}/{md['name']}"


def short_name(key: str) -> str:
    return key.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Edge inference
# ---------------------------------------------------------------------------

# Matches "name:port", "http://name:port", "name.ns.svc.cluster.local",
# i.e. the shapes an in-cluster address actually takes in an env var.
_ADDR = re.compile(
    r"(?:https?://)?"
    r"(?P<host>[a-z0-9]([-a-z0-9]*[a-z0-9])?)"
    r"(?:\.(?P<ns>[a-z0-9-]+))?"
    r"(?:\.svc(?:\.cluster\.local)?)?"
    r"(?::(?P<port>\d+))?",
    re.IGNORECASE,
)


def selector_matches(selector: Dict[str, str], labels: Dict[str, str]) -> bool:
    if not selector:
        return False
    return all(labels.get(k) == v for k, v in selector.items())


def infer(state: dict) -> Tuple[List[dict], List[Tuple[str, str, float, str]], dict]:
    """Return (nodes, edges, diagnostics)."""
    workloads: List[dict] = []
    for kind in WORKLOAD_KINDS:
        workloads.extend(state.get(kind, []))

    # --- node table -------------------------------------------------------
    nodes = []
    by_key = {}
    for w in workloads:
        md, spec = w["metadata"], w["spec"]
        tmpl = spec.get("template", {}).get("spec", {})
        containers = tmpl.get("containers", [])

        cpu_req = sum(
            parse_cpu(c.get("resources", {}).get("requests", {}).get("cpu")) or 0.0
            for c in containers
        )
        mem_req = sum(
            parse_mem(c.get("resources", {}).get("requests", {}).get("memory")) or 0
            for c in containers
        )
        env_values = [
            str(e.get("value", ""))
            for c in containers
            for e in c.get("env", [])
            if e.get("value")
        ]

        key = workload_key(w)
        node = {
            "key": key,
            "name": md["name"],
            "namespace": md["namespace"],
            "kind": w["kind"],
            "labels": md.get("labels", {}) or {},
            "pod_labels": spec.get("template", {}).get("metadata", {}).get("labels", {}) or {},
            "replicas": spec.get("replicas", 1),
            "cpu_requested": cpu_req or None,
            "mem_requested": mem_req or None,
            "env_values": env_values,
        }
        nodes.append(node)
        by_key[key] = node

    # --- service -> workload ---------------------------------------------
    svc_to_workload: Dict[str, str] = {}
    svc_meta: Dict[str, dict] = {}
    for svc in state.get("services", []):
        md, spec = svc["metadata"], svc["spec"]
        sel = spec.get("selector") or {}
        svc_name = md["name"]
        svc_meta[svc_name] = {"namespace": md["namespace"], "selector": sel}
        if not sel:
            continue  # headless / ExternalName / no selector
        for n in nodes:
            if n["namespace"] == md["namespace"] and selector_matches(sel, n["pod_labels"]):
                svc_to_workload[svc_name] = n["key"]
                break

    # --- workload -> service, via env var references ----------------------
    edges: Set[Tuple[str, str, float, str]] = set()
    unresolved: List[Tuple[str, str]] = []
    known_services = set(svc_meta)

    for n in nodes:
        for raw in n["env_values"]:
            for token in re.split(r"[,\s;]+", raw):
                if not token:
                    continue
                m = _ADDR.match(token.strip())
                if not m:
                    continue
                host = (m.group("host") or "").lower()
                if host not in known_services:
                    if host and host not in ("localhost", "0", "127"):
                        unresolved.append((n["name"], host))
                    continue
                target = svc_to_workload.get(host)
                if not target or target == n["key"]:
                    continue
                edges.add((n["key"], target, 1.0, "env_service_ref"))

    # --- ingress -> service ----------------------------------------------
    for ing in state.get("ingresses", []):
        for rule in ing.get("spec", {}).get("rules", []):
            for path in rule.get("http", {}).get("paths", []):
                svc = path.get("backend", {}).get("service", {}).get("name")
                target = svc_to_workload.get(svc)
                if target:
                    edges.add((f"ingress/{ing['metadata']['name']}", target, 1.0, "ingress"))

    diagnostics = {
        "workload_count": len(nodes),
        "service_count": len(svc_meta),
        "services_mapped_to_workload": len(svc_to_workload),
        "edges_from_env": sum(1 for e in edges if e[3] == "env_service_ref"),
        "edges_from_ingress": sum(1 for e in edges if e[3] == "ingress"),
        "unresolved_env_hosts": sorted({h for _, h in unresolved})[:20],
        "metrics_available": state.get("metrics_available", False),
    }
    return nodes, sorted(edges), diagnostics


# ---------------------------------------------------------------------------
# CEI
# ---------------------------------------------------------------------------

def run_cei(nodes: List[dict], edges, metrics_available: bool) -> dict:
    """Feed the inferred graph through the real pipeline."""
    from src.engine import build_pipeline

    p = build_pipeline()

    # Without metrics-server there is no actual utilization. Requested CPU is
    # used as a stand-in so the shape of the pipeline can be exercised; this
    # is exactly the degraded mode the product must handle, and the numbers
    # it produces describe allocation, not usage.
    engine_nodes = []
    for n in nodes:
        cpu_pct = min(100.0, (n["cpu_requested"] or 0.1) * 100)
        mem_pct = min(100.0, ((n["mem_requested"] or 0) / (1024**3)) * 100)
        engine_nodes.append({
            "node_id": n["key"],
            "metrics": {
                "cpu_utilization": cpu_pct,
                "memory_utilization": mem_pct,
                "network_throughput": 0,
                "disk_io": 0,
            },
            "metadata": {
                "tier": "supporting",
                "type": n["kind"].lower(),
                "region": "in-cluster",
                "replicas": n["replicas"],
                "namespace": n["namespace"],
            },
        })

    engine_edges = [
        {"source": s, "target": t, "weight": w, "type": kind}
        for s, t, w, kind in edges
        if s in {n["key"] for n in nodes} and t in {n["key"] for n in nodes}
    ]

    telemetry = p.data_collector.collect(engine_nodes)
    graph = p.graph_constructor.build(telemetry, engine_edges)
    p.governance_store.load_policies({})
    risk = p.governance_store.compute_risk_factors(telemetry)
    weights = p.weight_recalibrator.get_current_weights()
    cei = p.cei_calculator.compute(graph, telemetry, risk, weights)
    osc = p.oscillation_detector.detect(telemetry)
    return {"cei": cei, "weights": weights, "oscillation": osc, "graph": graph}


# ---------------------------------------------------------------------------
# Ground truth
# ---------------------------------------------------------------------------
#
# Online Boutique's documented call graph. "high" = many in-cluster services
# depend on it, so its failure has wide blast radius. "low" = nothing in the
# cluster depends on it.
GROUND_TRUTH_HIGH = {
    "productcatalogservice", "cartservice", "currencyservice", "redis-cart",
}
# Only loadgenerator is unambiguously low: nothing depends on it, and it is
# not user-facing.
#
# frontend is deliberately NOT listed. It has in-degree 1 but out-degree 7,
# so it scores low on PageRank and high on degree/betweenness. Which answer
# is "right" depends on what centrality is meant to express -- internal blast
# radius (frontend failing breaks no other service) or user-facing importance
# (frontend failing means total outage). The current composite blends both.
# That is a product decision to make deliberately, not a bug to assert on.
GROUND_TRUTH_LOW = {
    "loadgenerator",
}

EXPECTED_EDGES = {
    ("frontend", "productcatalogservice"),
    ("frontend", "currencyservice"),
    ("frontend", "cartservice"),
    ("frontend", "recommendationservice"),
    ("frontend", "shippingservice"),
    ("frontend", "checkoutservice"),
    ("frontend", "adservice"),
    ("checkoutservice", "productcatalogservice"),
    ("checkoutservice", "shippingservice"),
    ("checkoutservice", "paymentservice"),
    ("checkoutservice", "emailservice"),
    ("checkoutservice", "currencyservice"),
    ("checkoutservice", "cartservice"),
    ("recommendationservice", "productcatalogservice"),
    ("cartservice", "redis-cart"),
    # loadgenerator drives traffic at the frontend via FRONTEND_ADDR. A real
    # dependency, and one the env-var heuristic correctly found before this
    # list did.
    ("loadgenerator", "frontend"),
}


def report(nodes, edges, diag, result) -> int:
    print("=" * 74)
    print("SPIKE: Kubernetes edge inference -> CEI")
    print("=" * 74)

    print("\n[1] Discovery")
    for k, v in diag.items():
        print(f"    {k:32s} {v}")

    print("\n[2] Inferred edges")
    named = {(short_name(s), short_name(t)) for s, t, _, _ in edges}
    for s, t in sorted(named):
        mark = "ok " if (s, t) in EXPECTED_EDGES else "?  "
        print(f"    {mark} {s} -> {t}")

    found = named & EXPECTED_EDGES
    missed = EXPECTED_EDGES - named
    spurious = named - EXPECTED_EDGES

    print("\n[3] Against the documented call graph")
    recall = len(found) / len(EXPECTED_EDGES) if EXPECTED_EDGES else 0
    precision = len(found) / len(named) if named else 0
    print(f"    recall    {len(found)}/{len(EXPECTED_EDGES)}  ({recall:.0%})")
    print(f"    precision {len(found)}/{len(named)}  ({precision:.0%})")
    if missed:
        print(f"    MISSED:   {sorted(missed)}")
    if spurious:
        print(f"    SPURIOUS: {sorted(spurious)}")

    print("\n[4] CEI ranking (higher = larger in-cluster blast radius)")
    ranked = sorted(
        result["cei"].items(), key=lambda kv: kv[1]["cei_score"], reverse=True
    )
    print(f"    {'workload':32s} {'CEI':>7s} {'centr':>7s} {'entropy':>8s} {'class':>9s}")
    for key, d in ranked:
        print(
            f"    {short_name(key):32s} {d['cei_score']:7.4f} "
            f"{d['centrality']:7.4f} {d['entropy']:8.4f} {d['classification']:>9s}"
        )

    print("\n[5] Verdict")
    order = [short_name(k) for k, _ in ranked]
    pos = {n: i for i, n in enumerate(order)}
    n_total = len(order)

    problems = []
    for name in GROUND_TRUTH_HIGH:
        if name in pos and pos[name] > n_total / 2:
            problems.append(f"{name} expected high-centrality, ranked {pos[name]+1}/{n_total}")
    for name in GROUND_TRUTH_LOW:
        if name in pos and pos[name] < n_total / 3:
            problems.append(f"{name} expected low-centrality, ranked {pos[name]+1}/{n_total}")

    osc = result["oscillation"]
    print(f"    oscillating nodes: {osc['oscillating_node_count']}/{osc['total_nodes']}"
          f"  suppression={osc['suppression_active']}")
    if osc["oscillation_ratio"] > 0.5:
        problems.append(
            f"oscillation detector flagged {osc['oscillation_ratio']:.0%} of workloads "
            "-- suppression would block every recommendation"
        )

    if problems:
        print("\n    PROBLEMS:")
        for p in problems:
            print(f"      - {p}")
    else:
        print("\n    Ranking is consistent with the documented call graph.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", default="default")
    ap.add_argument("--dump", help="write raw cluster state to this path")
    args = ap.parse_args()

    state = fetch(args.namespace)
    if args.dump:
        Path(args.dump).write_text(json.dumps(state, indent=1))

    nodes, edges, diag = infer(state)
    if not nodes:
        print(f"No workloads found in namespace {args.namespace!r}.")
        return 1
    result = run_cei(nodes, edges, diag["metrics_available"])
    return report(nodes, edges, diag, result)


if __name__ == "__main__":
    raise SystemExit(main())
