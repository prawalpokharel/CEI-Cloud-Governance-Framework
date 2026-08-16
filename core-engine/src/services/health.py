"""
Kubernetes health diagnostics.

Detects the failure modes that account for most "why is my cluster broken"
questions, and ranks them by CEI so a crash loop in a workload half the
cluster depends on outranks one in a scratch namespace.

That ranking is the differentiator. Every Kubernetes dashboard can list
CrashLoopBackOff pods; the useful question is which of them matters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Severity is about consequence, not about how alarming the string looks.
SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    kind: str
    severity: str
    workload_key: str | None
    namespace: str
    title: str
    detail: str
    evidence: dict

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "workload_key": self.workload_key,
            "namespace": self.namespace,
            "title": self.title,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def _owner_key(pod: dict, workloads_by_name: dict) -> str | None:
    """
    Attribute a pod to its workload.

    Pods are owned by ReplicaSets, which are owned by Deployments, so the
    owner reference points one level short of what we want. Rather than
    resolving the ReplicaSet with another API call, the pod's labels are
    matched against workload selectors -- the agent already collected both.
    """
    namespace = pod.get("namespace")
    labels = pod.get("labels") or {}
    for key, workload in workloads_by_name.items():
        if workload.get("namespace") != namespace:
            continue
        selector = workload.get("pod_labels") or {}
        if selector and all(labels.get(k) == v for k, v in selector.items()):
            return key
    return None


def diagnose(snapshot: dict, cei_by_workload: dict | None = None) -> dict[str, Any]:
    """
    Produce ranked health findings for a cluster snapshot.

    ``cei_by_workload`` maps workload key -> CEI node result. When supplied,
    findings are ordered by severity and then by the CEI of the affected
    workload, which is what turns a list of symptoms into a queue of work.
    """
    pods = snapshot.get("pods") or []
    workloads = snapshot.get("workloads") or []
    by_key = {w["key"]: w for w in workloads if w.get("key")}

    findings: list[Finding] = []

    # ---- pod-level failures ------------------------------------------
    for pod in pods:
        namespace = pod.get("namespace", "default")
        name = pod.get("name", "?")
        owner = _owner_key(pod, by_key)
        waiting = pod.get("waiting_reasons") or []
        terminated = pod.get("last_terminated_reasons") or []
        restarts = pod.get("restart_count") or 0
        phase = pod.get("phase")

        # CrashLoopBackOff is a TRANSIENT state: a failing pod cycles through
        # Running -> Terminated -> Waiting(CrashLoopBackOff) -> Running, and a
        # 60s snapshot lands at an arbitrary point in that cycle. Detecting
        # only on the waiting reason made the most important health signal in
        # Kubernetes a coin flip -- an observed crash loop was reported as
        # Running with an empty waiting list.
        #
        # Restart count plus a failing termination reason is durable evidence:
        # it survives whichever phase the snapshot happened to catch.
        crash_reasons = {"Error", "OOMKilled", "ContainerCannotRun", "DeadlineExceeded"}
        looping = "CrashLoopBackOff" in waiting or (
            restarts >= 3 and any(r in crash_reasons for r in terminated)
        )
        if looping:
            observed = (
                "Kubernetes is backing off between attempts"
                if "CrashLoopBackOff" in waiting
                else f"last exit reason was {', '.join(terminated) or 'unknown'}"
            )
            findings.append(Finding(
                kind="crash_loop",
                severity="critical",
                workload_key=owner,
                namespace=namespace,
                title=f"{name} is crash looping",
                detail=(
                    f"The container has failed and restarted {restarts} times; "
                    f"{observed}. The workload is not reliably serving."
                ),
                evidence={
                    "pod": name,
                    "restarts": restarts,
                    "waiting_reasons": waiting,
                    "termination_reasons": terminated,
                },
            ))

        if "OOMKilled" in terminated:
            findings.append(Finding(
                kind="oom_killed",
                severity="critical",
                workload_key=owner,
                namespace=namespace,
                title=f"{name} was killed for exceeding its memory limit",
                detail=(
                    "The container hit its memory limit and was terminated. "
                    "Raise the limit or reduce consumption -- this recurs "
                    "until one of the two changes."
                ),
                evidence={"pod": name, "restarts": restarts},
            ))

        if "Pending" == phase:
            findings.append(Finding(
                kind="pending",
                severity="warning",
                workload_key=owner,
                namespace=namespace,
                title=f"{name} cannot be scheduled",
                detail=(
                    "The pod has stayed Pending, which usually means no node "
                    "satisfies its resource requests, node selector, or "
                    "affinity rules."
                ),
                evidence={"pod": name, "reasons": waiting},
            ))

        for reason in waiting:
            if reason in ("ImagePullBackOff", "ErrImagePull"):
                findings.append(Finding(
                    kind="image_pull_failure",
                    severity="critical",
                    workload_key=owner,
                    namespace=namespace,
                    title=f"{name} cannot pull its image",
                    detail=(
                        "The image is missing, the tag is wrong, or the "
                        "registry credentials are not valid for this cluster."
                    ),
                    evidence={"pod": name, "reason": reason},
                ))

        # Restarts without a current failure state and without an error exit:
        # the pod recovered, but something is killing it repeatedly. Reported
        # only when the crash-loop rule above did not already claim it, to
        # avoid two findings for one problem.
        if restarts >= 5 and not looping and phase == "Running":
            findings.append(Finding(
                kind="frequent_restarts",
                severity="warning",
                workload_key=owner,
                namespace=namespace,
                title=f"{name} has restarted {restarts} times",
                detail=(
                    "The pod is running now but has restarted repeatedly. "
                    "Check for a failing liveness probe or intermittent crash."
                ),
                evidence={"pod": name, "restarts": restarts},
            ))

    # ---- workload-level risks ----------------------------------------
    for key, workload in by_key.items():
        namespace = workload.get("namespace", "default")
        name = workload.get("name", key)
        desired = workload.get("replicas_desired")
        ready = workload.get("replicas_ready")

        if desired and ready is not None and ready < desired:
            findings.append(Finding(
                kind="under_replicated",
                severity="critical" if ready == 0 else "warning",
                workload_key=key,
                namespace=namespace,
                title=(
                    f"{name} has no ready replicas"
                    if ready == 0
                    else f"{name} has {ready} of {desired} replicas ready"
                ),
                detail=(
                    "No pod is serving traffic for this workload."
                    if ready == 0
                    else "The workload is running below its desired capacity."
                ),
                evidence={"ready": ready, "desired": desired},
            ))

        # Single replica is only a finding when something depends on the
        # workload. A one-replica batch job is fine; a one-replica service
        # that half the cluster calls is an outage waiting for a node drain.
        if desired == 1 and workload.get("kind") == "Deployment":
            dependents = _dependent_count(snapshot, key)
            if dependents > 0:
                findings.append(Finding(
                    kind="single_replica",
                    severity="warning",
                    workload_key=key,
                    namespace=namespace,
                    title=f"{name} runs a single replica",
                    detail=(
                        f"{dependents} workload(s) depend on it, so a node "
                        "drain, eviction, or rollout takes them down with it."
                    ),
                    evidence={"replicas": 1, "dependents": dependents},
                ))

        if not workload.get("cpu_cores_requested") and not workload.get(
            "memory_bytes_requested"
        ):
            findings.append(Finding(
                kind="no_resource_requests",
                severity="warning",
                workload_key=key,
                namespace=namespace,
                title=f"{name} declares no resource requests",
                detail=(
                    "The scheduler cannot place it predictably, it is evicted "
                    "first under node pressure, and its cost cannot be "
                    "attributed."
                ),
                evidence={},
            ))

    # ---- rank ---------------------------------------------------------
    cei_by_workload = cei_by_workload or {}

    def sort_key(finding: Finding):
        cei = cei_by_workload.get(finding.workload_key or "", {})
        # Negative so higher CEI sorts first within a severity band.
        return (
            SEVERITY_ORDER.get(finding.severity, 3),
            -(cei.get("cei_score") or 0.0),
            finding.namespace,
        )

    findings.sort(key=sort_key)

    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    return {
        "summary": {
            "total": len(findings),
            "critical": counts.get("critical", 0),
            "warning": counts.get("warning", 0),
            "info": counts.get("info", 0),
            "ranked_by_cei": bool(cei_by_workload),
        },
        "findings": [
            {
                **finding.to_dict(),
                "cei_score": (
                    cei_by_workload.get(finding.workload_key or "", {}).get(
                        "cei_score"
                    )
                ),
            }
            for finding in findings
        ],
    }


def _dependent_count(snapshot: dict, workload_key: str) -> int:
    """How many workloads point at this one."""
    return sum(
        1
        for edge in (snapshot.get("edges") or [])
        if edge.get("target") == workload_key
    )
