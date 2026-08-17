"""
Cross-cluster convergence: whether multi-cloud is actually independent.

An organisation running on EKS and AKS believes it has bought independence.
Whether it actually has is a measurable question, and the answer is usually
no: both estates authenticate against the same identity tenant, resolve
through the same DNS provider, sit behind the same CDN, and bill through the
same payment API. October 2025 made the case twice in nine days -- an Azure
Front Door failure and a us-east-1 DNS failure each took down customers who
had "redundancy" that shared the failing dependency.

Within one cluster, `external_deps` finds workloads converging on a shared
endpoint. This module asks the same question across the fleet: which external
dependencies are reached from more than one cluster -- and above all, from
clusters on *different providers*, where the whole point of the second
provider was independence from the first.

## Sources, by strength

* **Observed egress**, where clusters run Hubble: the strong source, actual
  traffic.
* **Image registries**, always available: every workload's image references
  name the registries the fleet pulls from. Weak signal -- it bites on pull,
  not on every request -- but it is real (a registry outage during a rolling
  restart is an incident) and it is the one external dependency visible
  without any flow data.

Each dependency carries its provenance, so a finding built only on registry
data cannot masquerade as one built on observed traffic.

## The independence score

For the pair of clusters asked about: 1 minus the category-weighted overlap
of their external dependency sets. 1.0 means nothing shared; 0 means every
weighted dependency is common to both. Weighted by the same category weights
used everywhere else, so a shared identity provider costs far more
independence than a shared container registry -- which matches how the
outages actually go.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .external_deps import CATEGORY_WEIGHT, build_external_nodes, classify

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class SharedDependency:
    endpoint: str
    category: str
    provider: str | None
    clusters: list[str] = field(default_factory=list)
    cluster_providers: list[str] = field(default_factory=list)
    source: str = "egress"  # "egress" | "registry"
    workloads_total: int = 0

    @property
    def weight(self) -> float:
        return CATEGORY_WEIGHT.get(self.category, CATEGORY_WEIGHT["unknown"])

    @property
    def cross_provider(self) -> bool:
        return len(set(self.cluster_providers)) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "category": self.category,
            "provider": self.provider,
            "clusters": sorted(self.clusters),
            "cluster_providers": sorted(set(self.cluster_providers)),
            "cross_provider": self.cross_provider,
            "source": self.source,
            "workloads_total": self.workloads_total,
            "category_weight": self.weight,
        }


def _registry_endpoints(snapshot: dict) -> dict[str, int]:
    """
    Registries the cluster pulls from, from image references.

    The one external dependency visible without flow data. Counted per
    workload so fan-in is meaningful.
    """
    registries: dict[str, int] = {}
    for workload in snapshot.get("workloads") or []:
        seen: set[str] = set()
        for image in workload.get("images") or []:
            reference = str(image).split("@")[0]
            if "/" not in reference:
                # `nginx:1.25` -- no path at all is Docker Hub shorthand, and
                # its colon is a tag, not a registry port. Testing the first
                # segment for ":" here misread every bare tagged image as a
                # registry named after itself.
                seen.add("docker.io")
                continue
            first = reference.split("/")[0]
            # With a path present, the first segment is a registry only when
            # it looks like a host; `library/nginx` is Docker Hub too.
            if "." in first or ":" in first or first == "localhost":
                seen.add(first.lower().split(":")[0])
            else:
                seen.add("docker.io")
        for host in seen:
            registries[host] = registries.get(host, 0) + 1
    return registries


def collect_cluster_dependencies(
    cluster_name: str,
    provider: str,
    snapshot: dict,
) -> list[dict[str, Any]]:
    """External dependencies of one cluster, from whatever sources it has."""
    found: dict[str, dict[str, Any]] = {}

    egress = snapshot.get("egress")
    if egress and egress.get("available"):
        for node in build_external_nodes(snapshot, egress).values():
            found[node.endpoint] = {
                "endpoint": node.endpoint,
                "category": node.category,
                "provider": node.provider,
                "source": "egress",
                "workloads": len(node.dependents),
            }

    for host, count in _registry_endpoints(snapshot).items():
        if host in found:
            continue
        category, provider_name = classify(host)
        found[host] = {
            "endpoint": host,
            "category": category if category != "unknown" else "registry",
            "provider": provider_name,
            "source": "registry",
            "workloads": count,
        }

    return sorted(found.values(), key=lambda d: d["endpoint"])


def analyze(clusters: list[dict]) -> dict[str, Any]:
    """
    Fleet-wide convergence.

    ``clusters`` is a list of {name, provider, snapshot}. Findings are about
    dependencies reached from MORE THAN ONE cluster; single-cluster
    convergence is external_deps' job and is not repeated here.
    """
    if len(clusters) < 2:
        return {
            "available": False,
            "reason": (
                "Fleet analysis needs at least two clusters with snapshots. "
                "Single-cluster convergence is reported per cluster under "
                "/external-dependencies."
            ),
            "shared_dependencies": [],
            "findings": [],
        }

    shared: dict[str, SharedDependency] = {}
    per_cluster: dict[str, list[dict]] = {}
    egress_clusters = 0

    for cluster in clusters:
        name = cluster.get("name") or "unnamed"
        provider = (cluster.get("provider") or "unknown").lower()
        snapshot = cluster.get("snapshot") or {}
        if (snapshot.get("egress") or {}).get("available"):
            egress_clusters += 1

        dependencies = collect_cluster_dependencies(name, provider, snapshot)
        per_cluster[name] = dependencies
        for dep in dependencies:
            entry = shared.get(dep["endpoint"])
            if entry is None:
                entry = SharedDependency(
                    endpoint=dep["endpoint"],
                    category=dep["category"],
                    provider=dep["provider"],
                    source=dep["source"],
                )
                shared[dep["endpoint"]] = entry
            entry.clusters.append(name)
            entry.cluster_providers.append(provider)
            entry.workloads_total += dep["workloads"]
            # Observed traffic outranks a registry inference for the same host.
            if dep["source"] == "egress":
                entry.source = "egress"

    multi = [d for d in shared.values() if len(d.clusters) > 1]
    findings = []
    for dep in multi:
        if dep.cross_provider and dep.weight >= 0.8:
            severity = "critical"
        elif dep.cross_provider or dep.weight >= 0.8:
            severity = "warning"
        else:
            severity = "info"
        label = dep.provider or dep.category
        findings.append({
            "kind": "fleet_shared_dependency",
            "severity": severity,
            "endpoint": dep.endpoint,
            "title": (
                f"{len(dep.clusters)} clusters share {dep.endpoint}"
                + (f" ({label})" if label else "")
                + (" — across providers" if dep.cross_provider else "")
            ),
            "detail": (
                (
                    "These clusters run on different cloud providers, and the "
                    "point of that arrangement was surviving a provider "
                    "failure. This dependency is common to both sides, so for "
                    "any incident involving it, the fleet is one failure "
                    "domain wearing two logos."
                    if dep.cross_provider else
                    "Multiple clusters reach this endpoint. An incident here "
                    "is a fleet-wide incident, not a cluster-scoped one."
                )
                + (
                    " (Derived from image references only -- it bites on "
                    "image pull, not on every request.)"
                    if dep.source == "registry" else ""
                )
            ),
            "evidence": dep.to_dict(),
        })

    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f["severity"], 9),
        -f["evidence"]["category_weight"],
        f["endpoint"],
    ))

    return {
        "available": True,
        "summary": {
            "clusters": len(clusters),
            "clusters_with_egress": egress_clusters,
            "shared_dependencies": len(multi),
            "cross_provider_shared": sum(1 for d in multi if d.cross_provider),
            "note": (
                None if egress_clusters == len(clusters) else
                f"{len(clusters) - egress_clusters} cluster(s) have no egress "
                "data, so their external dependencies are visible only through "
                "image registries. The real overlap is at least what is shown, "
                "not at most."
            ),
        },
        "shared_dependencies": sorted(
            (d.to_dict() for d in multi),
            key=lambda d: (-d["category_weight"] * len(d["clusters"]), d["endpoint"]),
        ),
        "findings": findings,
        "per_cluster": {
            name: len(dependencies) for name, dependencies in per_cluster.items()
        },
    }


def independence_score(
    cluster_a: dict, cluster_b: dict
) -> dict[str, Any]:
    """
    How independent two clusters actually are: 1 - weighted shared fraction.

    The number a CTO can put in a board deck, with the list of what to fix
    attached. Weighted union in the denominator, weighted intersection in the
    numerator, so a shared identity provider costs more independence than a
    shared registry -- matching how the outages actually go.
    """
    a = {d["endpoint"]: d for d in collect_cluster_dependencies(
        cluster_a.get("name") or "a", cluster_a.get("provider") or "unknown",
        cluster_a.get("snapshot") or {},
    )}
    b = {d["endpoint"]: d for d in collect_cluster_dependencies(
        cluster_b.get("name") or "b", cluster_b.get("provider") or "unknown",
        cluster_b.get("snapshot") or {},
    )}

    def weight(dep: dict) -> float:
        return CATEGORY_WEIGHT.get(dep["category"], CATEGORY_WEIGHT["unknown"])

    union = {**a, **b}
    if not union:
        return {
            "score": None,
            "reason": "Neither cluster has visible external dependencies.",
            "shared": [],
        }

    shared_keys = set(a) & set(b)
    shared_weight = sum(weight(a[k]) for k in shared_keys)
    union_weight = sum(weight(d) for d in union.values())
    score = 1.0 - (shared_weight / union_weight if union_weight else 0.0)

    return {
        "score": round(score, 4),
        "interpretation": (
            "1.0 means no shared external dependencies; 0 means every "
            "weighted dependency is common to both clusters."
        ),
        "shared": sorted(
            (
                {
                    "endpoint": k,
                    "category": a[k]["category"],
                    "provider": a[k]["provider"],
                    "weight": weight(a[k]),
                    "source": a[k]["source"],
                }
                for k in shared_keys
            ),
            key=lambda d: -d["weight"],
        ),
        "unique_to_a": len(set(a) - shared_keys),
        "unique_to_b": len(set(b) - shared_keys),
    }


# --------------------------------------------------------------------------
# Phase C: provider substrate knowledge base
# --------------------------------------------------------------------------

# Which cloud a SaaS provider itself runs on. The point: an organisation on
# AWS that "diversified" identity to Auth0 is still on AWS twice -- Auth0
# runs there. Every entry is public knowledge, coarse (providers migrate,
# and run multi-region), and marked assumed: this is a lead for an
# architecture review, not an observation.
PROVIDER_SUBSTRATE: dict[str, str] = {
    "Auth0": "aws",
    "Okta": "aws",
    "Clerk": "aws",
    "MongoDB Atlas": "multi",     # customer-chosen; often the same cloud
    "Redis Cloud": "multi",
    "Stripe": "aws",
    "Braintree": "aws",
    "Twilio": "aws",
    "SendGrid": "aws",
    "Mailgun": "aws",
    "Datadog": "multi",
    "Sentry": "gcp",
    "New Relic": "aws",
    "Grafana Cloud": "multi",
    "Honeycomb": "aws",
    "Docker Hub": "aws",
}

_PROVIDER_ALIASES = {"eks": "aws", "aks": "azure", "gke": "gcp"}


def substrate_overlaps(clusters: list[dict]) -> list[dict[str, Any]]:
    """
    Where an external dependency's own substrate is a cluster's cloud.

    Catches the second-order concentration the endpoint analysis cannot: the
    dependency's hostname says Auth0, and Auth0's substrate says AWS, so the
    AWS cluster's "external" identity provider shares a failure domain with
    the cluster itself -- a regional AWS event can take both.

    Provenance on every finding is "assumed_public_knowledge". Nothing here
    is observed traffic, and reporting it at the same confidence as an
    observed edge would poison the trust the observed edges earned.
    """
    findings = []
    for cluster in clusters:
        cloud = _PROVIDER_ALIASES.get(
            (cluster.get("provider") or "").lower(),
            (cluster.get("provider") or "").lower(),
        )
        if cloud not in ("aws", "azure", "gcp"):
            continue
        for dep in collect_cluster_dependencies(
            cluster.get("name") or "?", cloud, cluster.get("snapshot") or {}
        ):
            substrate = PROVIDER_SUBSTRATE.get(dep.get("provider") or "")
            if substrate == cloud:
                findings.append({
                    "kind": "substrate_overlap",
                    "severity": "warning" if dep["category"] in ("identity", "database", "dns") else "info",
                    "cluster": cluster.get("name"),
                    "endpoint": dep["endpoint"],
                    "provider": dep["provider"],
                    "shared_substrate": substrate,
                    "provenance": "assumed_public_knowledge",
                    "detail": (
                        f"{dep['provider']} itself runs on {substrate.upper()}, "
                        f"which is also this cluster's cloud. The dependency "
                        "that looks external shares the cluster's own regional "
                        "failure domain -- diversification that is not. Coarse "
                        "public knowledge, not observed traffic: treat as a "
                        "lead for an architecture review."
                    ),
                })
    return findings
