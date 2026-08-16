"""
Dependency edge inference.

Validated in spikes/k8s_edge_inference against Google's Online Boutique,
whose call graph is publicly documented: 16/16 recall, 16/16 precision from
env-var references alone.

Sources, in descending confidence:

  1.0  service_selector  Service -> workload, by label selector. Declarative
                         and exact.
  1.0  ingress           Ingress -> Service backend. Declarative and exact.
  0.9  env_reference     Workload -> Service, from a container env var naming
                         it. Idiomatic and, on conventional deployments, near
                         perfect -- but an app that hardcodes DNS names in a
                         config file will not be seen.

Not available here: actual pod-to-pod traffic, which needs eBPF or a service
mesh. That arrives with Phase 6's egress analysis. Until then every edge
carries its confidence and source, so the UI can be honest about which
dependencies are observed versus inferred.
"""

from __future__ import annotations

from typing import Any

CONFIDENCE = {
    "service_selector": 1.0,
    "ingress": 1.0,
    "env_reference": 0.9,
}


def _selector_matches(selector: dict, labels: dict) -> bool:
    if not selector:
        return False
    return all(labels.get(key) == value for key, value in selector.items())


def map_services_to_workloads(
    services: list[dict], workloads: list[dict]
) -> dict[str, str]:
    """
    Resolve each Service to the workload backing it.

    Keyed by "namespace/service-name" and also by bare service name. The bare
    key exists because env vars usually reference a service without a
    namespace (``cartservice:7070``), relying on same-namespace DNS
    resolution. Ambiguous bare names -- the same service name in two
    namespaces -- are dropped from the bare index rather than guessed at.
    """
    qualified: dict[str, str] = {}
    bare_candidates: dict[str, set[str]] = {}

    for service in services:
        selector = service.get("selector") or {}
        if not selector:
            continue  # headless, ExternalName, or manually-managed endpoints
        for workload in workloads:
            if workload["namespace"] != service["namespace"]:
                continue
            if _selector_matches(selector, workload.get("pod_labels") or {}):
                key = f"{service['namespace']}/{service['name']}"
                qualified[key] = workload["key"]
                bare_candidates.setdefault(
                    service["name"].lower(), set()
                ).add(workload["key"])
                break

    for name, targets in bare_candidates.items():
        if len(targets) == 1:
            qualified[name] = next(iter(targets))

    return qualified


def infer_edges(
    workloads: list[dict],
    services: list[dict],
    ingresses: list[dict],
) -> list[dict]:
    """Build the dependency edge list for a snapshot."""
    service_to_workload = map_services_to_workloads(services, workloads)
    valid_keys = {workload["key"] for workload in workloads}
    edges: dict[tuple[str, str], dict] = {}

    def add(source: str, target: str, source_kind: str) -> None:
        if not source or not target or source == target:
            return
        if target not in valid_keys:
            return
        existing = edges.get((source, target))
        confidence = CONFIDENCE[source_kind]
        # Keep the highest-confidence explanation when several sources agree.
        if existing and existing["confidence"] >= confidence:
            return
        edges[(source, target)] = {
            "source": source,
            "target": target,
            "confidence": confidence,
            "source_kind": source_kind,
        }

    # Workload -> Service, from environment references.
    for workload in workloads:
        for reference in workload.get("service_references", []):
            name = reference["service"]
            namespace = reference.get("namespace") or workload["namespace"]
            target = (
                service_to_workload.get(f"{namespace}/{name}")
                or service_to_workload.get(name.lower())
            )
            if target:
                add(workload["key"], target, "env_reference")

    # Ingress -> Service backend. Modelled as an edge into the backing
    # workload from a synthetic ingress vertex so externally-reachable
    # entrypoints are visible in the graph.
    for ingress in ingresses:
        source = f"{ingress['namespace']}/Ingress/{ingress['name']}"
        for backend in ingress.get("backends", []):
            target = (
                service_to_workload.get(
                    f"{ingress['namespace']}/{backend['service']}"
                )
                or service_to_workload.get(str(backend["service"]).lower())
            )
            if target:
                add(source, target, "ingress")

    return sorted(edges.values(), key=lambda e: (e["source"], e["target"]))


def edge_summary(edges: list[dict]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for edge in edges:
        counts[edge["source_kind"]] = counts.get(edge["source_kind"], 0) + 1
    return {"total": len(edges), "by_source": counts}
