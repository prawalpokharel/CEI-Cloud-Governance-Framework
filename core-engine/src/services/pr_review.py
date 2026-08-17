"""
Pull-request blast radius: what this change will reach.

The product. Everything else in this codebase measures a cluster that already
exists; this measures a change that has not happened yet, at the only moment
when preventing it is cheap.

## The problem it addresses

Roughly eight in ten Kubernetes incidents follow a recent change, and the
changes that cause them are almost never the large ones. Large changes get
scrutiny. The outage comes from a one-line edit to a ConfigMap, a replica
count going from 3 to 1, a Service selector renamed during a tidy-up. Every
one of those reviews in under a minute because **diff size carries no
information about blast radius**, and a reviewer has nothing else to go on.

What is missing at review time is not more static analysis of the YAML. It is
the runtime dependency graph -- which is the one thing a repository cannot
know and a cluster already does.

## Why the runtime graph rather than code imports

Other tooling in this space builds cross-repository *code* dependency graphs
from imports and package manifests. That answers "who compiles against this".
The question here is "what talks to this in production", and the two diverge
constantly: a service that imports a shared library is not affected when that
library's *deployment* is scaled down, and a service that reaches another only
through a DNS name in an environment variable has no import to find.

The agent already observes the second graph. This puts it in front of a human
holding a merge button.

## Verdict, not gate

The check reports `neutral` rather than `failure` unless configured
otherwise. A tool that blocks merges on a heuristic gets bypassed within a
week and ignored thereafter; one that says "this reaches 31 workloads and
three of them have no PodDisruptionBudget" gets read. Blocking is available
per-repository for teams that want it, off by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import manifest_diff, resilience
from .blast_radius import (
    build_dependency_graph,
    compute_blast_radius,
    is_load_bearing,
)
from .git_provider import GitHubApp, GitProviderError

CHECK_NAME = "CloudOptimizer / blast radius"

# Embedded in the PR comment so re-analysis replaces rather than appends.
COMMENT_MARKER = "<!-- cloudoptimizer:blast-radius -->"

RISK_ORDER = ["critical", "high", "moderate", "low", "none"]

# Change kinds whose failure mode gives no signal at the time of the change.
# Called out separately because they are the ones a reviewer cannot catch by
# reading the diff more carefully.
SILENT_FAILURE_KINDS = {"selector_changed", "config_data_changed", "probe_removed"}


@dataclass
class ChangeImpact:
    change: manifest_diff.ObjectChange
    workload_key: str | None = None
    resolved: bool = False
    namespace_used: str | None = None
    namespace_source: str | None = None
    namespace_ambiguous: bool = False
    dependents: int = 0
    direct_dependents: list[str] = field(default_factory=list)
    user_facing: bool = False
    entry_points: list[str] = field(default_factory=list)
    centrality_fraction: float = 0.0
    cei_score: float | None = None
    load_bearing: bool = False
    risk: str = "low"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.change.to_dict(),
            "workload_key": self.workload_key,
            "resolved_in_cluster": self.resolved,
            "namespace_used": self.namespace_used,
            "namespace_source": self.namespace_source,
            "namespace_ambiguous": self.namespace_ambiguous,
            "dependents": self.dependents,
            "direct_dependents": self.direct_dependents,
            "user_facing": self.user_facing,
            "entry_points": self.entry_points,
            "centrality_fraction": round(self.centrality_fraction, 4),
            "cei_score": self.cei_score,
            "load_bearing": self.load_bearing,
            "risk": self.risk,
            "notes": self.notes,
        }


def _config_readers(snapshot: dict, name: str, namespace: str | None) -> list[str]:
    """Workloads mounting a given ConfigMap or Secret."""
    readers = []
    for workload in snapshot.get("workloads") or []:
        if namespace and workload.get("namespace") != namespace:
            continue
        refs = workload.get("config_refs") or {}
        if name in (refs.get("config_maps") or []) or name in (refs.get("secrets") or []):
            readers.append(workload["key"])
    return sorted(readers)


def _service_backends(snapshot: dict, name: str, namespace: str | None) -> list[str]:
    """Workloads a Service selects."""
    for service in snapshot.get("services") or []:
        if service.get("name") != name:
            continue
        if namespace and service.get("namespace") != namespace:
            continue
        selector = service.get("selector") or {}
        if not selector:
            continue
        return sorted(
            w["key"] for w in (snapshot.get("workloads") or [])
            if w.get("namespace") == service.get("namespace")
            and all((w.get("pod_labels") or {}).get(k) == v for k, v in selector.items())
        )
    return []


def resolve_namespace(
    change: manifest_diff.ObjectChange,
    snapshot: dict,
    *,
    kustomize: dict[str, str] | None = None,
    default_namespace: str = "default",
) -> tuple[str, str, bool]:
    """
    Work out which namespace a manifest actually lands in.

    Manifests routinely omit `metadata.namespace` and receive it from a
    kustomize overlay or the apply command. Assuming "default" resolves
    nothing on a cluster that uses real namespaces, and a workload that fails
    to resolve is reported as "not found" -- technically honest, useless to
    read, and indistinguishable from a genuinely new workload.

    Four strategies, most authoritative first. Returns
    (namespace, how, ambiguous).
    """
    if change.namespace:
        return change.namespace, "declared in the manifest", False

    # Nearest kustomize overlay wins, which is how kustomize itself resolves.
    for directory in manifest_diff.ancestor_directories(change.path):
        namespace = (kustomize or {}).get(directory)
        if namespace:
            return namespace, f"kustomization.yaml in {directory or 'repository root'}", False

    # Match against the cluster. If exactly one object of this kind and name
    # exists, that is the one being edited -- inferred from live state rather
    # than assumed, and far more often right than "default".
    matches = sorted({
        workload["namespace"]
        for workload in (snapshot.get("workloads") or [])
        if workload.get("name") == change.name and workload.get("kind") == change.kind
    })
    if change.kind in manifest_diff.CONFIG_KINDS:
        matches = sorted({
            workload["namespace"]
            for workload in (snapshot.get("workloads") or [])
            for name in ((workload.get("config_refs") or {}).get("config_maps") or [])
                       + ((workload.get("config_refs") or {}).get("secrets") or [])
            if name == change.name
        })
    if len(matches) == 1:
        return matches[0], "matched to the only object of this name in the cluster", False
    if len(matches) > 1:
        # Guessing here would attribute the change to the wrong namespace and
        # report a confident, wrong blast radius. Say so instead.
        return (
            default_namespace,
            f"ambiguous: {change.kind} {change.name} exists in "
            f"{', '.join(matches)}",
            True,
        )

    return default_namespace, "assumed (no namespace declared and no match in cluster)", False


def _at_least(risk: str, floor: str) -> str:
    """The more severe of the two."""
    return risk if RISK_ORDER.index(risk) <= RISK_ORDER.index(floor) else floor


def _at_most(risk: str, ceiling: str) -> str:
    """The less severe of the two."""
    return risk if RISK_ORDER.index(risk) >= RISK_ORDER.index(ceiling) else ceiling


def analyze_changes(
    snapshot: dict,
    changes: list[manifest_diff.ObjectChange],
    cei_by_workload: dict[str, dict] | None = None,
    *,
    default_namespace: str = "default",
    kustomize: dict[str, str] | None = None,
) -> list[ChangeImpact]:
    """Attach cluster impact to each parsed object change."""
    cei_by_workload = cei_by_workload or {}
    graph = build_dependency_graph(snapshot)
    impacts: list[ChangeImpact] = []

    for change in changes:
        impact = ChangeImpact(change=change)
        namespace, source, ambiguous = resolve_namespace(
            change, snapshot, kustomize=kustomize, default_namespace=default_namespace
        )
        impact.namespace_used = namespace
        impact.namespace_source = source
        impact.namespace_ambiguous = ambiguous
        if ambiguous:
            impact.notes.append(
                f"Namespace could not be determined — {source}. The manifest "
                "declares none and no kustomization.yaml in this pull request "
                "supplies one, so impact below is measured against "
                f"`{namespace}` and may be attributed to the wrong copy."
            )

        # Workloads resolve directly. ConfigMaps, Secrets and Services resolve
        # to the workloads they touch, which is where their blast radius
        # actually lives -- editing a ConfigMap is a change to every workload
        # that mounts it, and nothing in the diff says so.
        targets: list[str] = []
        if change.kind in manifest_diff.WORKLOAD_KINDS:
            key = f"{namespace}/{change.kind}/{change.name}"
            impact.workload_key = key
            if key in graph:
                targets = [key]
        elif change.kind in manifest_diff.CONFIG_KINDS:
            targets = _config_readers(snapshot, change.name, namespace)
            if targets:
                impact.notes.append(
                    f"{change.kind} {change.name} is mounted by {len(targets)} "
                    "workload(s). Nothing restarts on its own when it changes, "
                    "so the effect appears at the next unrelated rollout."
                )
        elif change.kind == "Service":
            targets = _service_backends(snapshot, change.name, namespace)
        elif change.kind in manifest_diff.POLICY_KINDS:
            targets = []

        if not targets:
            impact.risk = change.severity
            if change.kind in manifest_diff.WORKLOAD_KINDS:
                impact.notes.append(
                    f"`{namespace}/{change.kind}/{change.name}` is not in the "
                    f"current snapshot (namespace {source}). It may be new, in "
                    "a namespace the agent does not cover, or rendered from a "
                    "template. Impact could not be measured."
                )
            impacts.append(impact)
            continue

        impact.resolved = True

        # Union the blast radius over every workload the change reaches: one
        # ConfigMap edit can touch several, and the reviewer needs the total.
        affected: set[str] = set()
        entry_points: set[str] = set()
        direct: set[str] = set()
        centrality = 0.0
        best_cei = None

        for target in targets:
            radius = compute_blast_radius(snapshot, target, cei_by_workload, graph=graph)
            affected.update(a.key for a in radius.affected)
            entry_points.update(radius.entry_points)
            direct.update(radius.direct_dependents)
            centrality = max(centrality, radius.centrality_fraction)
            if is_load_bearing(radius):
                impact.load_bearing = True
            score = (cei_by_workload.get(target) or {}).get("cei_score")
            if score is not None:
                best_cei = score if best_cei is None else max(best_cei, score)

        # The changed workloads are themselves affected by their own change.
        affected.update(targets)

        impact.dependents = len(affected)
        impact.direct_dependents = sorted(direct)
        impact.entry_points = sorted(entry_points)
        impact.user_facing = bool(entry_points)
        impact.centrality_fraction = centrality
        impact.cei_score = best_cei

        # Risk starts from the kind of change and moves with what it reaches.
        # Both directions matter: the same edit is critical on a hub and
        # ordinary on a leaf, and a scheme that only escalates would mark
        # every replica reduction in the cluster as high.
        risk = change.severity
        if impact.user_facing:
            risk = _at_least(risk, "high")
        if impact.load_bearing:
            risk = (
                "critical" if change.severity in ("critical", "high")
                else _at_least(risk, "high")
            )
        elif not impact.user_facing and impact.dependents <= 1:
            # The change reaches only the object it edits. Still worth
            # reporting -- it can break itself -- but it cannot cascade, and
            # calling that critical is how a reviewer learns to skim the table.
            risk = _at_most(risk, "moderate")
        impact.risk = risk

        if set(change.change_kinds) & SILENT_FAILURE_KINDS:
            impact.notes.append(
                "This class of change fails without an error: no failed "
                "rollout, no unhealthy pod, nothing in the event stream. It "
                "will not be caught by watching the deploy."
            )

        impacts.append(impact)

    impacts.sort(key=lambda i: (
        RISK_ORDER.index(i.risk), -i.dependents, i.change.name
    ))
    return impacts


def review(
    snapshot: dict,
    files: list[dict],
    cei_by_workload: dict[str, dict] | None = None,
    *,
    default_namespace: str = "default",
    include_resilience: bool = True,
) -> dict[str, Any]:
    """
    Full analysis of a pull request against a cluster snapshot.

    ``files`` is a list of {path, before, after}. Pure: it performs no network
    calls, which is what makes it testable and what lets the same code back
    both the API endpoint and the GitHub integration.
    """
    changes, skipped = manifest_diff.diff_files(files)
    # Overlays supplied alongside the manifests decide the namespace for any
    # manifest that omits one.
    kustomize = manifest_diff.kustomize_namespaces(files)
    impacts = analyze_changes(
        snapshot,
        changes,
        cei_by_workload,
        default_namespace=default_namespace,
        kustomize=kustomize,
    )

    # Latent weaknesses in the workloads this PR touches. A replica reduction
    # on something that already has no PodDisruptionBudget is a different
    # conversation from the same change on something well protected.
    fragile_touched: list[dict] = []
    if include_resilience and impacts:
        touched = {i.workload_key for i in impacts if i.workload_key}
        touched |= {d for i in impacts for d in i.direct_dependents}
        if touched:
            findings = resilience.analyze(snapshot, cei_by_workload)
            fragile_touched = [
                f for f in findings["fragile_workloads"]
                if f["workload_key"] in touched
            ]

    by_risk: dict[str, int] = {}
    for impact in impacts:
        by_risk[impact.risk] = by_risk.get(impact.risk, 0) + 1

    highest = min(
        (i.risk for i in impacts), key=lambda r: RISK_ORDER.index(r), default="none"
    )
    total_affected = len({
        key
        for impact in impacts
        for key in ([impact.workload_key] if impact.workload_key else []) + impact.direct_dependents
    })

    return {
        "verdict": highest,
        "summary": {
            "objects_changed": len(impacts),
            "by_risk": by_risk,
            "workloads_touched": total_affected,
            "user_facing": any(i.user_facing for i in impacts),
            "unresolved": sum(1 for i in impacts if not i.resolved),
            "namespace_ambiguous": sum(1 for i in impacts if i.namespace_ambiguous),
            "files_skipped": len(skipped),
        },
        "impacts": [i.to_dict() for i in impacts],
        "fragile_workloads_touched": fragile_touched,
        "skipped_files": skipped,
    }


# -- rendering ---------------------------------------------------------------

_RISK_ICON = {
    "critical": "🔴", "high": "🟠", "moderate": "🟡", "low": "⚪", "none": "⚪",
}


def render_markdown(result: dict) -> str:
    """Render the review as the PR comment body."""
    summary = result["summary"]
    impacts = result["impacts"]
    lines = [COMMENT_MARKER, "## Blast radius", ""]

    if not impacts:
        lines.append(
            "No Kubernetes object changes detected in this pull request."
        )
        if summary["files_skipped"]:
            lines.append("")
            lines.append(
                f"_{summary['files_skipped']} file(s) could not be parsed; "
                "see the check output._"
            )
        return "\n".join(lines)

    verdict = result["verdict"]
    lines.append(
        f"{_RISK_ICON[verdict]} **{verdict.title()}** — "
        f"{summary['objects_changed']} object(s) changed, reaching "
        f"{summary['workloads_touched']} workload(s)."
        + (" Includes a user-facing path." if summary["user_facing"] else "")
    )
    lines.append("")

    lines.append("| Risk | Object | Change | Reaches | Notes |")
    lines.append("|---|---|---|---|---|")
    for impact in impacts[:25]:
        change_kinds = ", ".join(
            k.replace("_", " ") for k in impact["change_kinds"]
        )
        reach = (
            "not in cluster" if not impact["resolved_in_cluster"]
            else f"{impact['dependents']} workload(s)"
            + (" · **ingress**" if impact["user_facing"] else "")
        )
        note = "; ".join(impact["details"][:1]) or ""
        lines.append(
            f"| {_RISK_ICON[impact['risk']]} {impact['risk']} "
            f"| `{impact['kind']}/{impact['name']}` "
            f"| {change_kinds} | {reach} | {note[:160]} |"
        )
    if len(impacts) > 25:
        lines.append(f"| | _+{len(impacts) - 25} more_ | | | |")
    lines.append("")

    # The detail that justifies the verdict, for the ones that matter.
    serious = [i for i in impacts if i["risk"] in ("critical", "high")]
    if serious:
        lines.append("### Why")
        lines.append("")
        for impact in serious[:8]:
            lines.append(f"**`{impact['kind']}/{impact['name']}`**")
            for detail in impact["details"][:3]:
                lines.append(f"- {detail}")
            for note in impact["notes"][:2]:
                lines.append(f"- {note}")
            if impact["direct_dependents"]:
                shown = ", ".join(
                    f"`{k.split('/')[-1]}`" for k in impact["direct_dependents"][:6]
                )
                more = len(impact["direct_dependents"]) - 6
                lines.append(
                    f"- Directly depended on by: {shown}"
                    + (f" and {more} more" if more > 0 else "")
                )
            if impact["entry_points"]:
                names = ", ".join(
                    f"`{e.split('/')[-1]}`" for e in impact["entry_points"]
                )
                lines.append(f"- Reachable from ingress: {names}")
            lines.append("")

    fragile = result.get("fragile_workloads_touched") or []
    if fragile:
        lines.append("### Already fragile before this change")
        lines.append("")
        for entry in fragile[:5]:
            lines.append(
                f"- `{entry['workload_key']}` — {', '.join(entry['weaknesses'])} "
                f"({entry['dependents']} dependent(s))"
            )
        lines.append("")

    if result.get("skipped_files"):
        lines.append("<details><summary>Files not analysed</summary>")
        lines.append("")
        for entry in result["skipped_files"][:20]:
            lines.append(f"- `{entry['path']}` — {entry['reason']}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append(
        "_Impact is computed from the live dependency graph observed in your "
        "cluster, not from the manifests in this repository. Dependencies that "
        "are never declared — a hostname hardcoded in application code — are "
        "not visible._"
    )
    return "\n".join(lines)


def check_conclusion(result: dict, *, blocking: bool = False) -> str:
    """
    GitHub check conclusion for a review.

    Neutral by default even at critical risk. A check that fails the build on
    a heuristic gets bypassed within a week and ignored thereafter; one that
    reports clearly gets read. Teams that want enforcement opt into it.
    """
    if result["verdict"] in ("critical", "high") and blocking:
        return "failure"
    if result["summary"]["objects_changed"] == 0:
        return "neutral"
    return "success" if result["verdict"] in ("low", "none") else "neutral"


# -- GitHub integration ------------------------------------------------------


def review_pull_request(
    app: GitHubApp,
    repo: str,
    number: int,
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    default_namespace: str = "default",
    post_comment: bool = True,
    post_check: bool = True,
    blocking: bool = False,
) -> dict[str, Any]:
    """
    Fetch a pull request, analyse it, and publish the result.

    Both sides of every changed manifest are fetched at the base and head
    commits rather than reconstructed from the patch. The patch is a fragment
    with a few lines of context, and a semantic comparison needs whole
    documents -- a `replicas:` line in a hunk says nothing about which object
    in a multi-document file it belongs to.
    """
    pull = app.get_pull_request(repo, number)
    if pull is None:
        raise GitProviderError(f"Pull request {repo}#{number} not found.")

    changed = app.pull_request_files(repo, number)

    # Kustomize overlays are usually NOT in the diff -- someone edits a
    # Deployment, not the kustomization.yaml above it -- so they have to be
    # fetched separately or every manifest without an explicit namespace
    # resolves by guesswork.
    files = []
    seen_overlays: set[str] = set()
    for entry in changed:
        if not manifest_diff.is_manifest_path(entry["path"]):
            continue
        for directory in manifest_diff.ancestor_directories(entry["path"]):
            if directory in seen_overlays:
                continue
            seen_overlays.add(directory)
            for name in manifest_diff.KUSTOMIZATION_NAMES:
                overlay_path = f"{directory}/{name}" if directory else name
                blob = app.get_file(repo, overlay_path, ref=pull["head_sha"])
                if blob:
                    files.append({
                        "path": overlay_path,
                        "before": None,
                        "after": blob["content"],
                    })
                    break

    for entry in changed:
        if not manifest_diff.is_manifest_path(entry["path"]):
            continue
        before = after = None
        if entry["status"] != "added":
            source = entry.get("previous_path") or entry["path"]
            blob = app.get_file(repo, source, ref=pull["base_sha"])
            before = blob["content"] if blob else None
        if entry["status"] != "removed":
            blob = app.get_file(repo, entry["path"], ref=pull["head_sha"])
            after = blob["content"] if blob else None
        files.append({"path": entry["path"], "before": before, "after": after})

    result = review(
        snapshot, files, cei_by_workload, default_namespace=default_namespace
    )
    result["pull_request"] = {
        "repo": repo, "number": number, "title": pull["title"],
        "head_sha": pull["head_sha"],
    }

    body = render_markdown(result)
    published = {"comment": False, "check": False}

    if post_comment:
        app.upsert_pull_request_comment(repo, number, body, marker=COMMENT_MARKER)
        published["comment"] = True

    if post_check:
        summary = result["summary"]
        app.create_check_run(
            repo,
            pull["head_sha"],
            name=CHECK_NAME,
            conclusion=check_conclusion(result, blocking=blocking),
            title=(
                f"{result['verdict'].title()} — "
                f"{summary['workloads_touched']} workload(s) reached"
            ),
            summary=body,
        )
        published["check"] = True

    result["published"] = published
    return result
