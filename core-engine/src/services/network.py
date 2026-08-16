"""
Network segmentation analysis and NetworkPolicy generation.

Phase 6. The observation that makes this worth doing: writing a
least-privilege NetworkPolicy by hand requires knowing every service a
workload legitimately talks to. Nobody knows that, so in practice either no
policy is written, or one is written and then loosened until things stop
breaking.

The dependency graph already answers the question. Every edge is an observed
call; a policy allowing exactly those edges and nothing else is least
privilege derived from evidence rather than from memory.

## What this generates and what it does not

Generated: ingress and egress rules mirroring observed dependencies, plus DNS
egress, which every policy needs and every hand-written policy forgets.

NOT generated: anything to do with traffic the agent cannot see. Edges come
from Service selectors, Ingress backends, and service references in
environment variables -- not from packet capture. A dependency expressed only
at runtime (a hostname read from a database, a client library with a hardcoded
IP) produces no edge, so a policy built from this evidence would block it.

That is why every generated policy is emitted in **audit posture first**: the
recommendation is to observe, confirm nothing legitimate is missing, and only
then enforce. A tool that hands you a policy and says "apply this" is handing
you an outage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Ports every pod needs regardless of application dependencies. Omitting DNS
# is the single most common way a hand-written NetworkPolicy breaks a
# cluster: name resolution stops, and the failure looks like an application
# bug rather than a policy bug.
DNS_PORTS = [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}]

# Shared with the remediation policy engine — see services/constants.py for
# why there is only one definition.
from .constants import SYSTEM_NAMESPACES  # noqa: E402


@dataclass
class SegmentationFinding:
    kind: str
    severity: str
    workload_key: str | None
    namespace: str
    title: str
    detail: str
    cei_score: float | None = None
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "workload_key": self.workload_key,
            "namespace": self.namespace,
            "title": self.title,
            "detail": self.detail,
            "cei_score": self.cei_score,
            "evidence": self.evidence,
        }


def _covered_workloads(snapshot: dict) -> set[str]:
    """
    Workloads already selected by at least one NetworkPolicy.

    Matched on the policy's podSelector against workload pod labels. An empty
    selector selects every pod in the namespace, which is how default-deny is
    written and must not be mistaken for "selects nothing".
    """
    covered: set[str] = set()
    workloads = snapshot.get("workloads") or []

    for policy in snapshot.get("network_policies") or []:
        namespace = policy.get("namespace")
        selector = policy.get("pod_selector") or {}
        for workload in workloads:
            if workload.get("namespace") != namespace:
                continue
            if not selector:
                covered.add(workload["key"])  # empty selector = all pods
                continue
            labels = workload.get("pod_labels") or {}
            if all(labels.get(k) == v for k, v in selector.items()):
                covered.add(workload["key"])
    return covered


def analyze_segmentation(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Report where the cluster is unsegmented, ranked by what it exposes.

    A workload with no NetworkPolicy accepts traffic from every pod in the
    cluster. That matters in proportion to what it protects: an unsegmented
    scratch service is untidy, an unsegmented database is a lateral-movement
    path to everything it holds.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = snapshot.get("workloads") or []
    covered = _covered_workloads(snapshot)

    from .vulnerability import internet_reachable_workloads

    reachable = internet_reachable_workloads(snapshot)

    findings: list[SegmentationFinding] = []
    unprotected = 0

    for workload in workloads:
        key = workload["key"]
        namespace = workload.get("namespace", "default")
        if namespace in SYSTEM_NAMESPACES:
            continue

        cei = (cei_by_workload.get(key) or {}).get("cei_score")
        if key in covered:
            continue

        unprotected += 1
        is_reachable = key in reachable
        # Severity follows consequence. A high-CEI workload is one many others
        # depend on, so an attacker who reaches it reaches them; an
        # internet-reachable one is where an attacker starts.
        if (cei or 0) >= 0.6 or is_reachable:
            severity = "warning"
        else:
            severity = "info"

        detail = (
            "No NetworkPolicy selects this workload, so every pod in the "
            "cluster can open a connection to it."
        )
        if is_reachable:
            detail += (
                " It is also reachable from outside the cluster, which makes "
                "it a plausible entry point rather than only a lateral one."
            )

        findings.append(SegmentationFinding(
            kind="no_network_policy",
            severity=severity,
            workload_key=key,
            namespace=namespace,
            title=f"{workload.get('name', key)} is unsegmented",
            detail=detail,
            cei_score=cei,
            evidence={"internet_reachable": is_reachable},
        ))

    # Sort by consequence: severity band first, then blast radius.
    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(
        key=lambda f: (order.get(f.severity, 3), -(f.cei_score or 0.0))
    )

    considered = [
        w for w in workloads
        if (w.get("namespace") or "") not in SYSTEM_NAMESPACES
    ]
    coverage = (
        (len(considered) - unprotected) / len(considered) if considered else 1.0
    )

    return {
        "summary": {
            "workloads_considered": len(considered),
            "workloads_with_policy": len(considered) - unprotected,
            "workloads_unsegmented": unprotected,
            "coverage": round(coverage, 3),
            "policies_present": len(snapshot.get("network_policies") or []),
            "note": (
                "Coverage counts workloads selected by at least one "
                "NetworkPolicy. It does not judge whether those policies are "
                "correct -- a policy that allows everything counts as covered."
            ),
        },
        "findings": [f.to_dict() for f in findings],
    }


def generate_network_policy(
    snapshot: dict,
    workload_key: str,
    *,
    include_dns: bool = True,
) -> dict[str, Any] | None:
    """
    Build a least-privilege NetworkPolicy for one workload from observed edges.

    Ingress rules come from workloads that call this one; egress from
    workloads it calls. Both are derived from evidence, which is the point --
    and also the limitation, documented on the returned object rather than
    left for the operator to discover in an incident.
    """
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or [])}
    workload = workloads.get(workload_key)
    if workload is None:
        return None

    namespace = workload.get("namespace", "default")
    pod_labels = workload.get("pod_labels") or {}
    if not pod_labels:
        # Without labels there is nothing to select on. Emitting a policy with
        # an empty podSelector would silently apply to the whole namespace.
        return None

    edges = snapshot.get("edges") or []
    callers = [e["source"] for e in edges if e.get("target") == workload_key]
    callees = [e["target"] for e in edges if e.get("source") == workload_key]

    ingress_rules = []
    for caller_key in sorted(set(callers)):
        caller = workloads.get(caller_key)
        if caller is None:
            # An Ingress vertex, not a workload. Represented as a namespace
            # selector on the ingress controller rather than dropped, since
            # dropping it would generate a policy that blocks public traffic.
            ingress_rules.append({
                "from": [{
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "ingress-nginx"}
                    }
                }],
                "_comment": f"external traffic via {caller_key}",
            })
            continue
        caller_labels = caller.get("pod_labels") or {}
        if not caller_labels:
            continue
        ingress_rules.append({
            "from": [{
                "namespaceSelector": {
                    "matchLabels": {
                        "kubernetes.io/metadata.name": caller.get("namespace")
                    }
                },
                "podSelector": {"matchLabels": caller_labels},
            }],
            "_comment": f"observed dependency from {caller.get('name', caller_key)}",
        })

    egress_rules = []
    for callee_key in sorted(set(callees)):
        callee = workloads.get(callee_key)
        if callee is None:
            continue
        callee_labels = callee.get("pod_labels") or {}
        if not callee_labels:
            continue
        egress_rules.append({
            "to": [{
                "namespaceSelector": {
                    "matchLabels": {
                        "kubernetes.io/metadata.name": callee.get("namespace")
                    }
                },
                "podSelector": {"matchLabels": callee_labels},
            }],
            "_comment": f"observed dependency to {callee.get('name', callee_key)}",
        })

    if include_dns:
        # Always. A policy without DNS egress breaks name resolution, and the
        # resulting failure looks like an application bug.
        egress_rules.append({
            "to": [{
                "namespaceSelector": {
                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                },
                "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
            }],
            "ports": DNS_PORTS,
            "_comment": "DNS resolution — required by effectively every pod",
        })

    policy_types = []
    if ingress_rules:
        policy_types.append("Ingress")
    if egress_rules:
        policy_types.append("Egress")
    if not policy_types:
        policy_types = ["Ingress"]

    manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"{workload.get('name', 'workload')}-observed",
            "namespace": namespace,
            "annotations": {
                "cloudoptimizer.app/generated-from": "observed-dependencies",
                "cloudoptimizer.app/review-required": "true",
            },
        },
        "spec": {
            "podSelector": {"matchLabels": pod_labels},
            "policyTypes": policy_types,
            "ingress": ingress_rules,
            "egress": egress_rules,
        },
    }

    return {
        "workload_key": workload_key,
        "namespace": namespace,
        "manifest": manifest,
        "observed_callers": len(set(callers)),
        "observed_callees": len(set(callees)),
        "confidence": _confidence(snapshot, workload_key),
        "warning": (
            "Derived from dependencies the agent can observe: Service "
            "selectors, Ingress backends, and service references in "
            "environment variables. A dependency resolved only at runtime "
            "produces no edge and would be blocked by this policy. Apply it "
            "in a non-enforcing posture first, confirm nothing legitimate is "
            "denied, and only then enforce."
        ),
    }


def _confidence(snapshot: dict, workload_key: str) -> str:
    """
    How much to trust a generated policy for this workload.

    A workload with no observed edges is the dangerous case: the generated
    policy denies everything, which looks like a tidy least-privilege result
    and is actually just an absence of evidence.
    """
    edges = snapshot.get("edges") or []
    related = [
        e for e in edges
        if e.get("source") == workload_key or e.get("target") == workload_key
    ]
    if not related:
        return "none"
    low_confidence = sum(
        1 for e in related if (e.get("confidence") or 1.0) < 1.0
    )
    if low_confidence == len(related):
        return "inferred"
    return "observed"


def generate_all_policies(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    min_cei: float = 0.0,
) -> list[dict[str, Any]]:
    """
    Generate policies for every eligible workload, highest blast radius first.

    Workloads with no observed edges are skipped rather than given a
    deny-everything policy. "We found no dependencies" and "this workload has
    no dependencies" are different statements, and only one of them justifies
    generating a policy.
    """
    cei_by_workload = cei_by_workload or {}
    policies = []

    for workload in snapshot.get("workloads") or []:
        key = workload["key"]
        namespace = workload.get("namespace", "default")
        if namespace in SYSTEM_NAMESPACES:
            continue

        cei = (cei_by_workload.get(key) or {}).get("cei_score") or 0.0
        if cei < min_cei:
            continue

        policy = generate_network_policy(snapshot, key)
        if policy is None or policy["confidence"] == "none":
            continue
        policy["cei_score"] = round(cei, 4)
        policies.append(policy)

    policies.sort(key=lambda p: -(p.get("cei_score") or 0.0))
    return policies
