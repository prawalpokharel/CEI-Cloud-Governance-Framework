"""
What your recovery needs that your steady state does not.

AWS's own resilience doctrine -- static stability -- says a system should
survive the unavailability of its dependencies without making changes. Its
corollary is the part nobody audits: control planes (the APIs that create,
modify, and scale things) fail more often than data planes (the things
themselves), and *recovery is control-plane work*. Running pods keep serving
through a control-plane outage; launching, scaling, and rescheduling do not.

October 2025 was the live demonstration: workloads stayed healthy for hours
while nothing could be launched, scaled, or failed over, and every runbook
that began "scale up the standby" was a runbook that no longer worked.

This module audits the *recovery paths* of a cluster and classifies what each
one needs at the moment it fires. The distinction being drawn, per workload:

* **Statically stable** -- recovery needs only what the cluster already
  holds: capacity is pre-provisioned, images are pinned and cached, replicas
  already running.
* **Cluster control plane** -- needs the API server and scheduler (any pod
  replacement does). Kubernetes' control plane is in the failure domain the
  operator controls, so this is noted, not flagged.
* **External control plane** -- needs the cloud provider's APIs or an
  external service (node provisioning, registry pulls) to work. This is the
  dependency AWS's doctrine says to design out, and each instance is a
  finding.

Everything here is computed from the snapshot: no cloud credentials, no new
agent permissions. The checks are deliberately the checkable subset --
imagePullPolicy and node-pool autoscaler configuration are not collected, so
what is knowable is stated and what is assumed is labelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blast_radius import build_dependency_graph, compute_blast_radius, is_load_bearing
from .ownership import image_is_reproducible

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    kind: str
    severity: str
    workload_key: str | None
    title: str
    detail: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "workload_key": self.workload_key,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def _free_capacity(snapshot: dict) -> tuple[float, float]:
    """(free cpu cores, free memory bytes) across schedulable ready nodes."""
    cpu_total = 0.0
    mem_total = 0.0
    for node in snapshot.get("nodes") or []:
        if node.get("unschedulable") or not node.get("ready", True):
            continue
        cpu_total += node.get("allocatable_cpu_cores") or 0.0
        mem_total += node.get("allocatable_memory_bytes") or 0

    cpu_requested = 0.0
    mem_requested = 0.0
    for workload in snapshot.get("workloads") or []:
        cpu_requested += workload.get("cpu_cores_requested") or 0.0
        mem_requested += workload.get("memory_bytes_requested") or 0

    return max(0.0, cpu_total - cpu_requested), max(0.0, mem_total - mem_requested)


def scale_up_headroom(snapshot: dict) -> list[dict[str, Any]]:
    """
    For each HPA: can it scale to max with the capacity already in the
    cluster, or does reaching max require provisioning nodes?

    The distinction is the whole point. Scaling within existing capacity is a
    data-plane operation plus the cluster's own scheduler. Scaling that needs
    new nodes needs the cloud provider's control plane -- which is exactly
    what tends to be down in the incidents where you most need to scale.
    """
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}
    free_cpu, free_mem = _free_capacity(snapshot)

    audits = []
    for hpa in snapshot.get("autoscalers") or []:
        target_key = (
            f"{hpa.get('namespace')}/{hpa.get('target_kind')}/{hpa.get('target_name')}"
        )
        workload = workloads.get(target_key)
        if workload is None:
            continue
        current = hpa.get("current_replicas") or workload.get("replicas_desired") or 0
        maximum = hpa.get("max_replicas") or current
        additional = max(0, maximum - current)
        if additional == 0:
            continue

        cpu_per_pod = workload.get("cpu_cores_requested_per_pod") or 0.0
        mem_per_pod = workload.get("memory_bytes_requested_per_pod") or 0
        cpu_needed = additional * cpu_per_pod
        mem_needed = additional * mem_per_pod

        fits = cpu_needed <= free_cpu and mem_needed <= free_mem
        audits.append({
            "workload_key": target_key,
            "hpa": hpa.get("name"),
            "additional_replicas_to_max": additional,
            "cpu_needed": round(cpu_needed, 3),
            "mem_needed_bytes": int(mem_needed),
            "fits_in_current_capacity": fits,
            "requests_declared": bool(cpu_per_pod or mem_per_pod),
        })
    return audits


def analyze(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Audit recovery paths and score static stability.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = [w for w in (snapshot.get("workloads") or []) if w.get("key")]
    if not workloads:
        return {"summary": {"total": 0}, "findings": [], "static_stability": None}

    graph = build_dependency_graph(snapshot)
    findings: list[Finding] = []

    # --- scale-up paths that need node provisioning -----------------------
    headroom = scale_up_headroom(snapshot)
    for audit_entry in headroom:
        if audit_entry["fits_in_current_capacity"]:
            continue
        key = audit_entry["workload_key"]
        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        findings.append(Finding(
            kind="scale_up_needs_node_provisioning",
            severity="warning" if not is_load_bearing(radius) else "critical",
            workload_key=key,
            title=(
                f"Scaling {key.split('/')[-1]} to its HPA maximum needs "
                "capacity the cluster does not have"
            ),
            detail=(
                f"Reaching maxReplicas requires "
                f"{audit_entry['additional_replicas_to_max']} more pod(s), and "
                "the current node pool cannot hold them. That scale-up "
                "depends on provisioning nodes, which is a cloud control-plane "
                "operation -- the class of API most likely to be impaired "
                "during a regional incident, which is when this workload is "
                "most likely to need its maximum. Pre-provisioned headroom "
                "makes the same scale-up a data-plane action."
            ),
            evidence=audit_entry,
        ))

    # --- per-workload recovery classification -----------------------------
    statically_stable = 0
    load_bearing_total = 0
    for workload in workloads:
        key = workload["key"]
        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        load_bearing = is_load_bearing(radius)
        if load_bearing:
            load_bearing_total += 1

        external_recovery_deps: list[str] = []

        # Restart pulls from the registry unless the image is pinned. A
        # mutable tag defaults imagePullPolicy to Always, so every pod
        # replacement re-resolves the tag against the registry -- putting an
        # external service on the recovery path of a workload that was
        # otherwise self-contained.
        mutable = []
        for image in workload.get("images") or []:
            reproducible, _ = image_is_reproducible(image)
            if not reproducible:
                mutable.append(image)
        if mutable:
            external_recovery_deps.append("registry (mutable image tags)")
            if load_bearing:
                findings.append(Finding(
                    kind="recovery_depends_on_registry",
                    severity="warning",
                    workload_key=key,
                    title=(
                        f"Every pod replacement of {workload.get('name')} "
                        "goes through the registry"
                    ),
                    detail=(
                        "Mutable image tags default imagePullPolicy to "
                        "Always, so each restart re-resolves the tag against "
                        "the registry. A registry outage then blocks recovery "
                        "of a workload that is otherwise self-contained -- "
                        "and registry outages cluster with exactly the "
                        "regional incidents that cause restarts. Pinning by "
                        "digest removes the registry from the recovery path."
                    ),
                    evidence={"images": mutable},
                ))

        if not external_recovery_deps:
            statically_stable += 1

    # --- the score ---------------------------------------------------------
    total = len(workloads)
    score = statically_stable / total if total else None

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.title))
    by_severity = {
        s: sum(1 for f in findings if f.severity == s)
        for s in ("critical", "warning", "info")
        if any(f.severity == s for f in findings)
    }

    return {
        "summary": {
            "total": len(findings),
            "by_severity": by_severity,
            "note": (
                "Computed from the snapshot alone. What is checkable is "
                "checked (capacity headroom vs HPA maxima, image pinning); "
                "what is not collected (imagePullPolicy overrides, node-pool "
                "autoscaler configuration, pre-pulled image caches) is not "
                "guessed at. The score is therefore an upper bound on static "
                "stability, not a certificate of it."
            ),
        },
        "static_stability": {
            "score": round(score, 4) if score is not None else None,
            "statically_stable_workloads": statically_stable,
            "workloads_total": total,
            "load_bearing_total": load_bearing_total,
            "interpretation": (
                "Fraction of workloads whose pod-replacement path needs "
                "nothing outside the cluster. 1.0 means every restart is a "
                "data-plane operation."
            ),
        },
        "scale_up_headroom": headroom,
        "findings": [f.to_dict() for f in findings],
    }
