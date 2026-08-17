"""
The critical dependency nobody owns.

Every cluster accumulates them. Someone stood a service up by hand during an
incident three years ago, it worked, and it is still there. It has no team
label, no Helm release, no Argo application, and an image tag of `latest`
pointing at a digest nobody can reconstruct. Half a dozen workloads have since
been built against it.

Nothing about this is *broken*, which is why no monitoring system reports it.
It fails the day it needs to be changed: at 3am, by whoever is on call, with
no source manifest to read, no owner to page, and no way to rebuild the image
that is running.

## The three signals

* **Unowned.** No label or annotation names a team, a contact, or a parent
  application. Nobody is paged for it because nobody is listed.
* **Unmanaged.** No Helm release, no Argo instance, no `managed-by`. It exists
  only as live cluster state -- it was applied by hand, and there is no source
  of truth to redeploy from. This is the strongest of the three, because it
  means the object cannot be recovered if it is lost.
* **Unreproducible.** A mutable image tag, or no tag at all. Whatever is
  running cannot be identified, and pulling the same tag tomorrow may not
  produce the same thing.

## Why the combination is what matters

Each signal alone is normal. Plenty of well-run workloads lack a team label;
plenty of hand-applied test deployments are harmless. What is not normal is
all three landing on something a large part of the cluster depends on -- and
because the individual signals are so common, no lint rule that reports them
separately will ever be read.

Scored against blast radius for exactly that reason: an orphan nothing depends
on is trivia, and the same orphan under half the cluster is the thing that
turns a routine failure into a long one.
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

# Keys that name a human or a team. `part-of` counts: it does not name a
# person, but it attaches the workload to an application that someone owns,
# which is enough to start from.
OWNER_KEYS = (
    "owner", "owners", "team", "squad", "contact", "email", "slack",
    "app.kubernetes.io/part-of",
)

# Keys that prove something outside the cluster declares this object. Their
# absence means the live object is the only copy.
MANAGER_KEYS = (
    "app.kubernetes.io/managed-by",
    "meta.helm.sh/release-name",
    "argocd.argoproj.io/instance",
)

# Tags that can point at different content over time.
MUTABLE_TAGS = {"latest", "main", "master", "stable", "edge", "dev", "prod"}

# Namespaces whose workloads are managed by the cluster provider. They are
# genuinely unowned by the operator and flagging them is pure noise.
SYSTEM_NAMESPACES = {
    "kube-system", "kube-public", "kube-node-lease", "local-path-storage",
    "gatekeeper-system", "istio-system", "cert-manager",
}


@dataclass
class Finding:
    kind: str
    severity: str
    workload_key: str
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


def parse_image(image: str) -> dict[str, Any]:
    """
    Split an image reference into repository, tag, and digest.

    Written out rather than regexed because the ambiguous case needs a
    comment: a colon in the reference is a tag *or* a registry port, and
    `localhost:5000/app` has no tag at all. The colon only introduces a tag
    when it appears after the final slash.
    """
    reference = (image or "").strip()
    if not reference:
        return {"repository": "", "tag": None, "digest": None, "pinned": False}

    digest = None
    if "@" in reference:
        reference, digest = reference.split("@", 1)

    last_segment = reference.rsplit("/", 1)[-1]
    if ":" in last_segment:
        name, tag = last_segment.rsplit(":", 1)
        prefix = reference[: len(reference) - len(last_segment)]
        repository, tag = prefix + name, tag
    else:
        repository, tag = reference, None

    return {
        "repository": repository,
        "tag": tag,
        "digest": digest,
        # A digest pins content regardless of what the tag says.
        "pinned": bool(digest),
    }


def image_is_reproducible(image: str) -> tuple[bool, str | None]:
    """
    Whether ``image`` identifies one specific artefact.

    Returns (reproducible, reason_if_not).
    """
    parsed = parse_image(image)
    if parsed["pinned"]:
        return True, None
    tag = parsed["tag"]
    if not tag:
        return False, "no tag, which resolves to `latest` and may change at any time"
    if tag.lower() in MUTABLE_TAGS:
        return False, f"the tag `{tag}` is mutable and may point somewhere else tomorrow"
    return True, None


def _owner_signals(workload: dict) -> dict[str, Any]:
    ownership = workload.get("ownership") or {}
    owners = {k: v for k, v in ownership.items() if k in OWNER_KEYS}
    managers = {k: v for k, v in ownership.items() if k in MANAGER_KEYS}
    return {
        "owners": owners,
        "managers": managers,
        "has_owner": bool(owners),
        "has_manager": bool(managers),
    }


def analyze(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    include_system_namespaces: bool = False,
) -> dict[str, Any]:
    """
    Find critical workloads with no owner, no source of truth, or no
    identifiable image.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = [w for w in (snapshot.get("workloads") or []) if w.get("key")]
    if not workloads:
        return {"summary": {"total": 0}, "findings": [], "orphans": []}

    graph = build_dependency_graph(snapshot)
    findings: list[Finding] = []
    orphans: list[dict] = []
    # Reported alongside the findings: if most of the cluster carries no owner
    # label, the finding is "this convention is not in use here", not a list of
    # two hundred individually broken workloads.
    labelled = 0
    considered = 0

    for workload in workloads:
        key = workload["key"]
        namespace = workload.get("namespace") or "default"
        if namespace in SYSTEM_NAMESPACES and not include_system_namespaces:
            continue

        considered += 1
        signals = _owner_signals(workload)
        if signals["has_owner"]:
            labelled += 1

        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        cei = (cei_by_workload.get(key) or {}).get("cei_score")
        if not is_load_bearing(radius):
            continue

        images = workload.get("images") or []
        unreproducible = []
        for image in images:
            ok, reason = image_is_reproducible(image)
            if not ok:
                unreproducible.append({"image": image, "reason": reason})

        gaps: list[str] = []

        def add(kind: str, severity: str, title: str, detail: str, evidence: dict):
            findings.append(Finding(
                kind=kind, severity=severity, workload_key=key, namespace=namespace,
                title=title, detail=detail, evidence=evidence,
                cei_score=cei, blast_radius=radius.total_affected,
            ))

        if not signals["has_owner"]:
            gaps.append("no owner")
            add(
                "unowned_critical_workload", "warning",
                f"{workload.get('name')} is load-bearing and has no owner",
                (
                    "No label or annotation names a team, contact, or parent "
                    "application. " + radius.headline + " When this fails, "
                    "whoever is on call has nothing to tell them who to wake."
                ),
                {"searched_keys": list(OWNER_KEYS),
                 "dependents": radius.total_affected},
            )

        if not signals["has_manager"]:
            gaps.append("no source of truth")
            add(
                "unmanaged_critical_workload", "warning",
                f"{workload.get('name')} exists only as live cluster state",
                (
                    "No Helm release, Argo application, or `managed-by` label. "
                    "Nothing outside the cluster declares this workload, so "
                    "there is no manifest to read when it needs changing and "
                    "nothing to redeploy from if it is deleted. The live "
                    "object is the only copy."
                ),
                {"searched_keys": list(MANAGER_KEYS),
                 "dependents": radius.total_affected},
            )

        if unreproducible:
            gaps.append("unidentifiable image")
            reasons = "; ".join(u["reason"] for u in unreproducible if u["reason"])
            add(
                "unreproducible_image", "warning",
                f"{workload.get('name')} runs an image that cannot be identified",
                (
                    f"{reasons}. Nothing records which build is actually "
                    "running, so a rollback has no target and a rebuild is a "
                    "guess. Pin by digest, or use an immutable tag."
                ),
                {"images": unreproducible},
            )

        if len(gaps) >= 2:
            orphans.append({
                "workload_key": key,
                "namespace": namespace,
                "gaps": gaps,
                "dependents": radius.total_affected,
                "user_facing": bool(radius.entry_points),
                "cei_score": cei,
                "centrality_fraction": round(radius.centrality_fraction, 4),
            })
            findings.append(Finding(
                kind="orphaned_critical_dependency",
                severity="critical",
                workload_key=key,
                namespace=namespace,
                title=f"{workload.get('name')} is a critical dependency nobody owns",
                detail=(
                    f"{', '.join(gaps)}. " + radius.headline + " This is the "
                    "workload that turns a routine failure into a long one: "
                    "there is nobody listed to page, no manifest to consult, "
                    "and no way to reproduce what is running. None of that is "
                    "visible until the night it matters."
                ),
                evidence={
                    "gaps": gaps,
                    "owners": signals["owners"],
                    "managers": signals["managers"],
                    "images": images,
                    "dependents": radius.total_affected,
                    "user_facing": bool(radius.entry_points),
                    "entry_points": radius.entry_points,
                },
                cei_score=cei,
                blast_radius=radius.total_affected,
            ))

    findings.sort(key=lambda f: (
        SEVERITY_ORDER.get(f.severity, 9),
        -f.blast_radius,
        -(f.cei_score or 0.0),
        f.workload_key,
    ))
    orphans.sort(key=lambda o: (-o["dependents"], -(o["cei_score"] or 0.0)))

    by_severity: dict[str, int] = {}
    for finding in findings:
        by_severity[finding.severity] = by_severity.get(finding.severity, 0) + 1

    coverage = labelled / considered if considered else 0.0
    return {
        "summary": {
            "total": len(findings),
            "by_severity": by_severity,
            "orphans": len(orphans),
            "workloads_examined": considered,
            "ownership_coverage": round(coverage, 3),
            # Distinguishes "these workloads are neglected" from "this cluster
            # does not label ownership anywhere", which are different problems
            # with different fixes.
            "note": (
                "Fewer than a quarter of workloads carry any ownership label, "
                "so these findings likely reflect a convention that is not in "
                "use rather than individually neglected workloads. Adopting "
                "app.kubernetes.io/part-of or a team label cluster-wide is the "
                "prerequisite fix."
                if coverage < 0.25 and considered >= 4
                else None
            ),
        },
        "orphans": orphans,
        "findings": [f.to_dict() for f in findings],
    }
