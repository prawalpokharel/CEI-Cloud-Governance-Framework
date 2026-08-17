"""
Single points of failure nobody labelled as one.

Kubernetes has no field that says "this is a single point of failure". It has
a dozen fields that, left at their defaults, quietly make one: a Deployment
whose replicas all landed on the same node, a PodDisruptionBudget that permits
every pod to be evicted at once, a workload with no readiness probe that takes
traffic the instant the process starts.

Each of these is individually boring. Every cluster has dozens, most of them
harmless, and that is precisely why they are ignored -- a linter that reports
all of them reports nothing.

## Combinations, not counts

What matters is coincidence with blast radius. "No PodDisruptionBudget" is
noise on a scratch job and an incident waiting for a node drain on a workload
thirty others depend on. So findings here are scored two ways:

* **Individually**, at low severity, so nothing is hidden.
* **In combination**, escalated hard. Three defaults that are each fine alone
  compound into a workload that will fail, take dependents with it, and give
  no warning first -- and the combination is the finding, not the parts.

This is the reliability analogue of the toxic-combination idea that cloud
security tooling uses for attack paths. The mechanism transfers; the graph
underneath it is a dependency graph rather than an exploit graph, so the
conclusion is about availability rather than compromise.

## Deliberately not duplicating health.py

That module reports what is failing now -- crash loops, OOM kills, pods stuck
Pending. This one reports what is *fine right now and structurally unable to
survive an ordinary disruption*. A workload can be perfectly healthy here and
still be the reason next month's node upgrade turns into an outage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blast_radius import (
    build_dependency_graph,
    compute_blast_radius,
    is_load_bearing,
)

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# A ConfigMap this many workloads read is shared infrastructure, whatever the
# manifest calls it. Editing one is a cluster-wide change that reviews as a
# one-line diff.
CONFIG_FAN_IN_THRESHOLD = 3


@dataclass
class Finding:
    kind: str
    severity: str
    workload_key: str | None
    namespace: str
    title: str
    detail: str
    evidence: dict = field(default_factory=dict)
    cei_score: float | None = None
    blast_radius: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "workload_key": self.workload_key,
            "namespace": self.namespace,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
            "cei_score": self.cei_score,
            "blast_radius": self.blast_radius,
        }


def _selector_matches(selector: dict, labels: dict) -> bool:
    """Kubernetes label selection: every key in the selector must match."""
    if not selector:
        return False
    return all(labels.get(key) == value for key, value in selector.items())


def _parse_quantity(value: str | None, replicas: int) -> int | None:
    """
    Resolve a PDB's minAvailable/maxUnavailable to a pod count.

    Both accept an integer or a percentage string. Percentages are resolved
    against the replica count the way the controller does, so "50%" of 3
    replicas is 1 (rounded down) for maxUnavailable and 2 (rounded up) for
    minAvailable -- Kubernetes rounds in whichever direction is safer for
    availability, and so does this.
    """
    if value is None:
        return None
    text = str(value).strip()
    try:
        if text.endswith("%"):
            pct = float(text[:-1])
            return int(replicas * pct / 100)
        return int(float(text))
    except ValueError:
        return None


def _index_pdbs(snapshot: dict, workloads: list[dict]) -> dict[str, dict]:
    """Map workload key -> the PDB governing it, if any."""
    index: dict[str, dict] = {}
    for pdb in snapshot.get("disruption_budgets") or []:
        selector = pdb.get("selector") or {}
        if not selector:
            continue
        for workload in workloads:
            if workload.get("namespace") != pdb.get("namespace"):
                continue
            if _selector_matches(selector, workload.get("pod_labels") or {}):
                index[workload["key"]] = pdb
    return index


def _index_hpas(snapshot: dict) -> dict[str, dict]:
    """Map workload key -> the HPA targeting it, if any."""
    index: dict[str, dict] = {}
    for hpa in snapshot.get("autoscalers") or []:
        kind, name = hpa.get("target_kind"), hpa.get("target_name")
        if not kind or not name:
            continue
        index[f"{hpa.get('namespace')}/{kind}/{name}"] = hpa
    return index


def _node_placement(snapshot: dict, workloads: list[dict]) -> dict[str, set[str]]:
    """Map workload key -> the set of nodes its pods are running on."""
    by_key = {w["key"]: w for w in workloads if w.get("key")}
    placement: dict[str, set[str]] = {key: set() for key in by_key}

    for pod in snapshot.get("pods") or []:
        node = pod.get("node_name")
        if not node:
            continue  # unscheduled; Pending is health.py's finding, not ours
        labels = pod.get("labels") or {}
        namespace = pod.get("namespace")
        for key, workload in by_key.items():
            if workload.get("namespace") != namespace:
                continue
            if _selector_matches(workload.get("pod_labels") or {}, labels):
                placement[key].add(node)
                break
    return placement


def _config_fan_in(workloads: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Map (namespace, configmap) -> the workloads that read it."""
    fan_in: dict[tuple[str, str], list[str]] = {}
    for workload in workloads:
        namespace = workload.get("namespace")
        refs = (workload.get("config_refs") or {}).get("config_maps") or []
        for name in refs:
            fan_in.setdefault((namespace, name), []).append(workload["key"])
    return fan_in


def analyze(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Find latent single points of failure, ranked by what they would take down.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = [w for w in (snapshot.get("workloads") or []) if w.get("key")]

    if not workloads:
        return {"summary": {"total": 0}, "findings": [], "fragile_workloads": []}

    graph = build_dependency_graph(snapshot)
    pdbs = _index_pdbs(snapshot, workloads)
    hpas = _index_hpas(snapshot)
    placement = _node_placement(snapshot, workloads)

    findings: list[Finding] = []
    fragile: list[dict] = []

    for workload in workloads:
        key = workload["key"]
        namespace = workload.get("namespace") or "default"
        kind = workload.get("kind")
        replicas = workload.get("replicas_desired") or 0
        cei = (cei_by_workload.get(key) or {}).get("cei_score")

        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        central = is_load_bearing(radius)

        def add(kind_: str, severity: str, title: str, detail: str, evidence: dict):
            findings.append(Finding(
                kind=kind_, severity=severity, workload_key=key, namespace=namespace,
                title=title, detail=detail, evidence=evidence,
                cei_score=cei, blast_radius=radius.total_affected,
            ))

        weaknesses: list[str] = []

        # --- placement ----------------------------------------------------
        # DaemonSets run one pod per node by definition; "all on one node"
        # describes a single-node cluster, not a misconfiguration.
        nodes = placement.get(key) or set()
        if kind != "DaemonSet" and replicas > 1 and len(nodes) == 1:
            weaknesses.append("all replicas on one node")
            spread = workload.get("spread") or {}
            has_policy = (
                spread.get("topology_spread_constraints", 0) > 0
                or spread.get("anti_affinity_required", 0) > 0
            )
            add(
                "single_node_placement",
                "warning" if central else "info",
                f"All {replicas} replicas of {workload.get('name')} are on one node",
                (
                    "The replica count implies redundancy that the placement "
                    "does not provide. Losing this node loses the whole "
                    "workload."
                    + (
                        " A spread policy exists but is not being honoured -- "
                        "check whether it is `whenUnsatisfiable: ScheduleAnyway`, "
                        "which permits the scheduler to ignore it, or whether "
                        "the cluster has too few eligible nodes."
                        if has_policy else
                        " No topology spread constraint or pod anti-affinity is "
                        "set, so the scheduler is free to co-locate them."
                    )
                ),
                {"replicas": replicas, "nodes": sorted(nodes),
                 "spread_policy_present": has_policy},
            )

        # --- disruption budget --------------------------------------------
        pdb = pdbs.get(key)
        if pdb is None:
            if replicas > 1 and central and kind != "DaemonSet":
                weaknesses.append("no PodDisruptionBudget")
                add(
                    "missing_disruption_budget",
                    "warning",
                    f"{workload.get('name')} has no PodDisruptionBudget",
                    (
                        "Nothing prevents a node drain from evicting every "
                        "replica simultaneously. Routine maintenance -- a "
                        "cluster upgrade, a node pool rotation -- becomes an "
                        "outage for everything downstream."
                    ),
                    {"replicas": replicas, "dependents": radius.total_affected},
                )
        else:
            allowed = pdb.get("disruptions_allowed")
            max_unavailable = _parse_quantity(pdb.get("max_unavailable"), replicas)
            min_available = _parse_quantity(pdb.get("min_available"), replicas)

            if allowed == 0 and (pdb.get("expected_pods") or 0) > 0:
                weaknesses.append("PDB blocks all voluntary eviction")
                add(
                    "disruption_budget_blocks_drain",
                    "warning",
                    f"PodDisruptionBudget {pdb.get('name')} allows zero disruptions",
                    (
                        "The budget currently permits no voluntary eviction at "
                        "all, so `kubectl drain` on any node holding one of "
                        "these pods will hang indefinitely. This does not "
                        "protect the workload -- it blocks maintenance, and the "
                        "cluster upgrade that stalls months from now is rarely "
                        "traced back to it."
                    ),
                    {"min_available": pdb.get("min_available"),
                     "max_unavailable": pdb.get("max_unavailable"),
                     "current_healthy": pdb.get("current_healthy"),
                     "desired_healthy": pdb.get("desired_healthy")},
                )
            elif replicas and (
                (max_unavailable is not None and max_unavailable >= replicas)
                or (min_available is not None and min_available <= 0)
            ):
                weaknesses.append("PDB permits full eviction")
                add(
                    "disruption_budget_permits_full_eviction",
                    "warning",
                    f"PodDisruptionBudget {pdb.get('name')} permits every replica to go at once",
                    (
                        "The budget is satisfied even with zero pods running, "
                        "so it provides no protection while appearing on every "
                        "audit as though it does."
                    ),
                    {"replicas": replicas,
                     "min_available": pdb.get("min_available"),
                     "max_unavailable": pdb.get("max_unavailable")},
                )

        # --- readiness ------------------------------------------------------
        probes = workload.get("probes") or {}
        containers = probes.get("containers", 0)
        with_readiness = probes.get("with_readiness", 0)
        if containers and with_readiness < containers and central:
            weaknesses.append("incomplete readiness probes")
            add(
                "missing_readiness_probe",
                "warning",
                f"{workload.get('name')} takes traffic before it is ready",
                (
                    f"{containers - with_readiness} of {containers} containers "
                    "have no readiness probe. Kubernetes adds the pod to the "
                    "Service endpoints as soon as the process starts, so every "
                    "rollout serves errors for as long as startup takes -- to "
                    "dependents that have no reason to expect it."
                ),
                {"containers": containers, "with_readiness": with_readiness},
            )

        # --- autoscaling ----------------------------------------------------
        hpa = hpas.get(key)
        if hpa:
            current = hpa.get("current_replicas") or 0
            maximum = hpa.get("max_replicas") or 0
            minimum = hpa.get("min_replicas") or 0
            if maximum and current >= maximum:
                severity = "warning" if central else "info"
                if minimum >= maximum:
                    title = f"HorizontalPodAutoscaler {hpa.get('name')} cannot scale"
                    detail = (
                        f"minReplicas equals maxReplicas ({minimum}), so the "
                        "autoscaler has no range to work in. It is a fixed "
                        "replica count wearing an autoscaler's name, and it "
                        "will not respond to load."
                    )
                else:
                    title = f"HorizontalPodAutoscaler {hpa.get('name')} is pinned at its ceiling"
                    detail = (
                        f"Running at maxReplicas ({maximum}) with no headroom "
                        "left. The workload is absorbing load it can no longer "
                        "shed, which reads as healthy on a dashboard right up "
                        "until it stops being true."
                    )
                weaknesses.append("autoscaler has no headroom")
                add("autoscaler_at_ceiling", severity, title, detail,
                    {"current": current, "min": minimum, "max": maximum})

        # --- the combination ------------------------------------------------
        # Two or more compounding weaknesses on something with dependents is
        # the finding this module exists for.
        if len(weaknesses) >= 2 and central:
            fragile.append({
                "workload_key": key,
                "namespace": namespace,
                "weaknesses": weaknesses,
                "dependents": radius.total_affected,
                "user_facing": bool(radius.entry_points),
                "cei_score": cei,
                "centrality_fraction": round(radius.centrality_fraction, 4),
            })
            findings.append(Finding(
                kind="fragile_critical_workload",
                severity="critical",
                workload_key=key,
                namespace=namespace,
                title=(
                    f"{workload.get('name')} is load-bearing and has "
                    f"{len(weaknesses)} compounding weaknesses"
                ),
                detail=(
                    f"{'; '.join(weaknesses)}. "
                    + radius.headline
                    + " Each of these is individually unremarkable and would be "
                    "approved without comment. Together they describe a "
                    "workload that cannot survive an ordinary node drain and "
                    "will take its dependents with it."
                ),
                evidence={
                    "weaknesses": weaknesses,
                    "dependents": radius.total_affected,
                    "user_facing": bool(radius.entry_points),
                    "entry_points": radius.entry_points,
                    "centrality_fraction": round(radius.centrality_fraction, 4),
                },
                cei_score=cei,
                blast_radius=radius.total_affected,
            ))

    # --- cluster-level: shared configuration --------------------------------
    for (namespace, name), readers in sorted(_config_fan_in(workloads).items()):
        if len(readers) < CONFIG_FAN_IN_THRESHOLD:
            continue
        affected_cei = sum(
            (cei_by_workload.get(k) or {}).get("cei_score") or 0.0 for k in readers
        )
        findings.append(Finding(
            kind="shared_config_fan_in",
            severity="warning" if len(readers) >= CONFIG_FAN_IN_THRESHOLD * 2 else "info",
            workload_key=None,
            namespace=namespace or "default",
            title=f"ConfigMap {name} is read by {len(readers)} workloads",
            detail=(
                "A change to this ConfigMap is a change to every workload that "
                "mounts it, but it reviews as a one-line edit to a single "
                "object and nothing in Kubernetes shows the fan-out. Nothing "
                "restarts on its own either, so the effect appears at the next "
                "unrelated rollout, by which time the edit is no longer a "
                "suspect."
            ),
            evidence={"config_map": name, "readers": sorted(readers)},
            cei_score=round(affected_cei, 4) or None,
            blast_radius=len(readers),
        ))

    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 9),
        -f.blast_radius,
        -(f.cei_score or 0.0),
        f.workload_key or "",
        f.kind,
    ))
    fragile.sort(key=lambda f: (-f["dependents"], -(f["cei_score"] or 0.0)))

    by_severity: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1
        by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1

    return {
        "summary": {
            "total": len(findings),
            "by_severity": by_severity,
            "by_kind": by_kind,
            "fragile_workloads": len(fragile),
            "workloads_examined": len(workloads),
            "resilience_data_available": bool(
                snapshot.get("disruption_budgets") is not None
                or snapshot.get("autoscalers") is not None
            ),
        },
        "fragile_workloads": fragile,
        "findings": [f.to_dict() for f in findings],
    }
