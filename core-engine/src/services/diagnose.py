"""
Incident diagnosis: from a sea of red to a ranked list of root causes.

The most expensive minutes in operations are the ones between "something is
wrong" and "we know what is wrong". Industry measurements put manual
investigation at 60-80% of MTTR; a single pod failure routinely fans out
into dozens of downstream alerts; and the standing advice from practitioners
is exactly the thing dashboards cannot do: *find the first broken dependency
path, not the loudest alert.*

That is a graph traversal, and this codebase owns a dependency graph whose
failure-propagation behaviour has been validated against controlled failure
(recall 1.0 against injected faults). This module runs the traversal.

## The algorithm

1. **Classify** every workload's health with graded evidence, not a boolean.
   OOMKilled and ImagePullBackOff are *self-caused* -- no dependency produces
   them. CrashLoopBackOff is ambiguous: services crash on startup when their
   database is away, so a crash loop only counts as self-evidence when
   nothing the workload depends on is also down. "Unready with no other
   evidence" is the weakest state and the most likely to be collateral.

2. **Localize.** In the unhealthy subgraph, follow dependency edges
   downstream. A workload whose unhealthy dependencies explain its state is
   collateral. The frontier -- unhealthy workloads with no unhealthy
   dependencies -- are the root candidates. This is the "first broken
   dependency path" computed rather than eyeballed.

3. **Order by onset.** The ingest rail stores per-snapshot readiness, so the
   failure's spread order is reconstructable: a true root goes down before
   its collateral. A "root" that went down *after* its dependents is marked
   suspect -- the real cause is likely something the graph cannot see, and
   saying so beats false confidence.

4. **Infer common external causes.** Two independent root candidates that
   share an observed external dependency (the database service, the identity
   provider) are usually not two incidents. The shared dependency is
   promoted to prime suspect -- which is precisely the conclusion that takes
   a human the longest to reach at 3am, because no single service's
   dashboard shows it.

5. **Correlate changes.** The previous snapshot is diffed at the root
   candidates first: an image that changed on a root candidate in the
   incident window is the lead. Eight in ten incidents follow a change;
   the change that matters is the one at the root, not the forty log lines
   of collateral.

6. **Assemble the brief.** Roots ranked by confidence and damage explained,
   collateral grouped under its root (those alerts need no separate
   investigation), owners to page from workload metadata, next actions from
   the recovery machinery, and the number that calms a 3am brain: how much
   of the cluster is NOT involved.

## What it refuses to do

No log parsing, no LLM guesses, no correlation-by-time-window alone. Every
claim in the output is derived from the observed graph, the observed health
states, and the observed history -- the same sources whose behaviour the
chaos harness validates. Where the graph cannot explain something, the
workload lands in `unexplained` with that exact word.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blast_radius import build_dependency_graph
from .external_deps import CATEGORY_WEIGHT, build_external_nodes

# Health states, weakest evidence first.
UNREADY = "unready"                  # fewer ready than desired; no other evidence
PENDING = "pending"                  # pods unschedulable
CRASH_LOOP = "crash_loop"            # restarts + failing exits
IMAGE_PULL = "image_pull_failure"    # self-caused by definition
OOM = "oom_killed"                   # self-caused by definition

# Evidence that a workload's failure originates with the workload itself,
# regardless of what else is down. An image that cannot be pulled or a
# container exceeding its memory limit is not something a dependency does to
# you.
SELF_CAUSE_STATES = {IMAGE_PULL, OOM}

CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}

_CRASH_REASONS = {"Error", "OOMKilled", "ContainerCannotRun", "DeadlineExceeded"}
_IMAGE_REASONS = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}


@dataclass
class Unhealthy:
    key: str
    states: list[str]
    ready: int | None
    desired: int | None
    evidence: dict = field(default_factory=dict)

    @property
    def self_caused(self) -> bool:
        return bool(set(self.states) & SELF_CAUSE_STATES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_key": self.key,
            "states": self.states,
            "ready": self.ready,
            "desired": self.desired,
            "evidence": self.evidence,
        }


def classify_health(snapshot: dict) -> dict[str, Unhealthy]:
    """
    Every unhealthy workload, with graded evidence.

    Pod-level signals are attributed to workloads by label match, the same
    way health.py does it -- and the same restart-count logic applies,
    because CrashLoopBackOff is a transient string a snapshot only sometimes
    catches.
    """
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}
    unhealthy: dict[str, Unhealthy] = {}

    def entry(key: str) -> Unhealthy:
        if key not in unhealthy:
            workload = workloads[key]
            unhealthy[key] = Unhealthy(
                key=key,
                states=[],
                ready=workload.get("replicas_ready"),
                desired=workload.get("replicas_desired"),
            )
        return unhealthy[key]

    for key, workload in workloads.items():
        desired = workload.get("replicas_desired")
        ready = workload.get("replicas_ready")
        if desired and ready is not None and ready < desired:
            entry(key).states.append(UNREADY)

    for pod in snapshot.get("pods") or []:
        labels = pod.get("labels") or {}
        namespace = pod.get("namespace")
        owner = None
        for key, workload in workloads.items():
            if workload.get("namespace") != namespace:
                continue
            selector = workload.get("pod_labels") or {}
            if selector and all(labels.get(k) == v for k, v in selector.items()):
                owner = key
                break
        if owner is None:
            continue

        waiting = set(pod.get("waiting_reasons") or [])
        terminated = set(pod.get("last_terminated_reasons") or [])
        restarts = pod.get("restart_count") or 0

        if waiting & _IMAGE_REASONS:
            e = entry(owner)
            if IMAGE_PULL not in e.states:
                e.states.append(IMAGE_PULL)
            e.evidence.setdefault("image_pull", []).append(pod.get("name"))
        if "OOMKilled" in terminated:
            e = entry(owner)
            if OOM not in e.states:
                e.states.append(OOM)
            e.evidence.setdefault("oom_pods", []).append(pod.get("name"))
        if "CrashLoopBackOff" in waiting or (
            restarts >= 3 and terminated & _CRASH_REASONS
        ):
            e = entry(owner)
            if CRASH_LOOP not in e.states:
                e.states.append(CRASH_LOOP)
            e.evidence.setdefault("crash_pods", []).append(pod.get("name"))
            e.evidence["max_restarts"] = max(
                e.evidence.get("max_restarts", 0), restarts
            )
        if pod.get("phase") == "Pending":
            e = entry(owner)
            if PENDING not in e.states:
                e.states.append(PENDING)

    return {k: u for k, u in unhealthy.items() if u.states}


def _onset_order(
    history_by_workload: dict[str, list[dict]] | None,
    unhealthy: dict[str, Unhealthy],
) -> dict[str, int]:
    """
    When each workload first became unhealthy, as an ordinal.

    From the per-snapshot readiness the ingest rail already stores: the
    first sample (scanning oldest to newest, from the most recent healthy
    stretch) where ready < desired. Ordinals rather than timestamps -- the
    comparison that matters is "before or after its dependency", and
    ordinals survive irregular sampling.

    A workload with no history, or unhealthy since the start of the window,
    gets ordinal 0 -- "unknown, possibly earliest", which is the assumption
    that never *creates* false confidence in a later suspect.
    """
    if not history_by_workload:
        return {key: 0 for key in unhealthy}

    onsets: dict[str, int] = {}
    for key in unhealthy:
        series = history_by_workload.get(key) or []
        onset = 0
        # Scan from the end backwards: find where the current unhealthy
        # stretch began.
        index = len(series)
        for sample in reversed(series):
            desired = sample.get("replicas_desired")
            ready = sample.get("replicas_ready")
            healthy = not desired or ready is None or ready >= desired
            if healthy:
                onset = index  # first unhealthy sample is the one after this
                break
            index -= 1
        else:
            onset = 0  # unhealthy for the whole recorded window
        onsets[key] = onset
    return onsets


def _changes_near(
    current: dict, previous: dict | None, keys: set[str]
) -> dict[str, list[dict]]:
    """
    What changed on these workloads between the last two snapshots.

    Root candidates are diffed first-class; everything else is noise until
    the roots are explained. Image changes (a release), replica changes (a
    scale event), and appearance (a new workload) are the three change kinds
    that precede most incidents.
    """
    if previous is None:
        return {}
    prev = {w["key"]: w for w in (previous.get("workloads") or []) if w.get("key")}
    curr = {w["key"]: w for w in (current.get("workloads") or []) if w.get("key")}

    changes: dict[str, list[dict]] = {}
    for key in keys:
        found = []
        before, after = prev.get(key), curr.get(key)
        if after is None:
            continue
        if before is None:
            found.append({"kind": "appeared",
                          "detail": "workload did not exist in the previous snapshot"})
        else:
            if sorted(before.get("images") or []) != sorted(after.get("images") or []):
                found.append({
                    "kind": "image_changed",
                    "detail": (
                        f"{', '.join(before.get('images') or ['?'])} -> "
                        f"{', '.join(after.get('images') or ['?'])}"
                    ),
                })
            if (before.get("replicas_desired") or 0) != (after.get("replicas_desired") or 0):
                found.append({
                    "kind": "replicas_changed",
                    "detail": (
                        f"{before.get('replicas_desired')} -> "
                        f"{after.get('replicas_desired')}"
                    ),
                })
        if found:
            changes[key] = found
    return changes


def diagnose(
    snapshot: dict,
    previous_snapshot: dict | None = None,
    history_by_workload: dict[str, list[dict]] | None = None,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Localize root causes among the currently unhealthy workloads.
    """
    cei_by_workload = cei_by_workload or {}
    workloads = {w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")}
    graph = build_dependency_graph(snapshot)
    unhealthy = classify_health(snapshot)

    total = len(workloads)
    if not unhealthy:
        return {
            "incident": False,
            "summary": (
                f"All {total} workloads are healthy. Nothing to diagnose."
            ),
            "roots": [], "external_suspects": [], "unexplained": [],
            "healthy_workloads": total, "unhealthy_workloads": 0,
        }

    unhealthy_keys = set(unhealthy)
    onsets = _onset_order(history_by_workload, unhealthy)

    # --- localization ------------------------------------------------------
    # Dependencies of u that are also unhealthy. Edges run source -> target
    # as "source depends on target", so successors are what u relies on.
    def unhealthy_dependencies(key: str) -> set[str]:
        if key not in graph:
            return set()
        return {
            t for t in graph.successors(key)
            if t in unhealthy_keys
        }

    roots: list[str] = []
    collateral_of: dict[str, str] = {}   # collateral key -> nearest root

    for key in unhealthy_keys:
        deps_down = unhealthy_dependencies(key)
        if unhealthy[key].self_caused:
            # OOM / image pull originate here whatever else is down.
            roots.append(key)
        elif not deps_down:
            roots.append(key)
        else:
            # Explained by a broken dependency -- walk to the frontier.
            collateral_of[key] = "?"  # resolved below

    # Attribute each collateral workload to the frontier root(s) reachable
    # through unhealthy dependencies. Nearest-first walk; a workload can be
    # downstream of two roots, in which case both claim it.
    def frontier_roots(start: str) -> set[str]:
        seen, stack, found = {start}, [start], set()
        while stack:
            node = stack.pop()
            deps = unhealthy_dependencies(node)
            if not deps and node in roots:
                found.add(node)
                continue
            self_rooted = node in roots and node != start
            if self_rooted:
                found.add(node)
                continue
            for dep in deps:
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
                if dep in roots:
                    found.add(dep)
        return found

    collateral_groups: dict[str, list[str]] = {r: [] for r in roots}
    unexplained: list[str] = []
    for key in list(collateral_of):
        attributed = frontier_roots(key)
        if attributed:
            for root in attributed:
                collateral_groups.setdefault(root, []).append(key)
            collateral_of[key] = sorted(attributed)[0]
        else:
            # A cycle of mutually-unhealthy workloads with no frontier: the
            # graph cannot say which came first. Onset ordering can.
            earliest = min(
                (k for k in unhealthy_keys if not unhealthy[k].self_caused),
                key=lambda k: onsets.get(k, 0),
                default=None,
            )
            if earliest == key:
                roots.append(key)
                collateral_groups.setdefault(key, [])
                del collateral_of[key]
            else:
                unexplained.append(key)

    # --- external common cause --------------------------------------------
    external_suspects: list[dict] = []
    egress = snapshot.get("egress")
    if egress and egress.get("available"):
        external = build_external_nodes(snapshot, egress)
        by_endpoint: dict[str, list[str]] = {}
        for node in external.values():
            touched = [r for r in roots if r in node.dependents]
            if touched:
                by_endpoint[node.endpoint] = touched
        for endpoint, touched_roots in sorted(
            by_endpoint.items(), key=lambda kv: -len(kv[1])
        ):
            node = external[endpoint]
            weight = CATEGORY_WEIGHT.get(node.category, 0.5)
            if len(touched_roots) >= 2:
                external_suspects.append({
                    "endpoint": endpoint,
                    "category": node.category,
                    "provider": node.provider,
                    "roots_depending_on_it": sorted(touched_roots),
                    "assessment": "prime_suspect",
                    "detail": (
                        f"{len(touched_roots)} independent root candidates all "
                        f"depend on this external {node.category}. Two "
                        "unrelated services rarely fail at once; a shared "
                        "dependency failing once explains both. Check its "
                        "status page before restarting anything in-cluster."
                    ),
                })
            elif weight >= 0.8:
                external_suspects.append({
                    "endpoint": endpoint,
                    "category": node.category,
                    "provider": node.provider,
                    "roots_depending_on_it": sorted(touched_roots),
                    "assessment": "possible",
                    "detail": (
                        f"The root candidate depends on this external "
                        f"{node.category}; its failure would look exactly "
                        "like this from inside the cluster."
                    ),
                })

    # --- change correlation -------------------------------------------------
    root_changes = _changes_near(snapshot, previous_snapshot, set(roots))

    # --- rank and dress the roots -------------------------------------------
    prime_external_roots = {
        r for s in external_suspects if s["assessment"] == "prime_suspect"
        for r in s["roots_depending_on_it"]
    }

    dressed_roots = []
    for root in roots:
        info = unhealthy[root]
        collateral = sorted(set(collateral_groups.get(root, [])))
        onset = onsets.get(root, 0)
        collateral_onsets = [onsets.get(c, 0) for c in collateral]
        onset_consistent = not collateral_onsets or onset <= min(collateral_onsets)

        # Confidence: self-cause evidence and a consistent timeline earn
        # high; a bare unready frontier is medium; a root that went down
        # AFTER its collateral, or one better explained by a shared external
        # dependency, drops to low with the reason stated.
        if root in prime_external_roots:
            confidence, reason = "low", (
                "shares a failing-pattern external dependency with other "
                "roots; the external service is the better suspect"
            )
        elif not onset_consistent:
            confidence, reason = "low", (
                "went unhealthy AFTER some of its dependents, which a true "
                "root does not do -- the real cause may be outside the graph"
            )
        elif info.self_caused:
            confidence, reason = "high", "self-caused failure state (OOM / image pull)"
        elif CRASH_LOOP in info.states:
            confidence, reason = "high", (
                "crash-looping with no unhealthy dependency to blame"
            )
        elif collateral:
            confidence, reason = "medium", (
                "frontier of the failure: unhealthy with healthy dependencies, "
                "and its dependents' failures are consistent with it"
            )
        else:
            confidence, reason = "medium", (
                "unhealthy with healthy dependencies; no collateral yet"
            )

        workload = workloads.get(root) or {}
        ownership = workload.get("ownership") or {}
        page = {
            k: v for k, v in ownership.items()
            if k in ("team", "owner", "owners", "slack", "contact", "email")
        }

        actions = []
        if root_changes.get(root):
            kinds = {c["kind"] for c in root_changes[root]}
            if "image_changed" in kinds:
                actions.append(
                    "A release landed on this workload in the last window -- "
                    "rolling it back is the fastest test of causality."
                )
        if IMAGE_PULL in info.states:
            actions.append(
                "Fix the image reference or registry access; no dependency "
                "work will help."
            )
        if OOM in info.states:
            actions.append(
                "Raise the memory limit or find the leak; OOM recurs on "
                "restart until one of those happens."
            )
        if CRASH_LOOP in info.states and not actions:
            actions.append(
                "Read this workload's logs first; its dependencies are "
                "healthy, so the answer is local."
            )
        if collateral:
            actions.append(
                f"Do not restart the {len(collateral)} collateral workload(s) "
                "-- they recover on their own when this does, and restarts "
                "add a reconnect storm to the incident."
            )

        dressed_roots.append({
            "workload_key": root,
            "confidence": confidence,
            "confidence_reason": reason,
            "health": info.to_dict(),
            "explains_workloads": len(collateral),
            "collateral": collateral,
            "onset_ordinal": onset,
            "recent_changes": root_changes.get(root, []),
            "page": page or None,
            "next_actions": actions,
            "cei_score": (cei_by_workload.get(root) or {}).get("cei_score"),
        })

    dressed_roots.sort(key=lambda r: (
        CONFIDENCE_ORDER.get(r["confidence"], 9),
        -r["explains_workloads"],
        -(r["cei_score"] or 0.0),
    ))

    involved = len(unhealthy_keys)
    summary_bits = [
        f"{involved} of {total} workloads are unhealthy",
        f"{len(dressed_roots)} root cause candidate(s)",
        f"{len(collateral_of)} collateral (no separate investigation needed)",
    ]
    if external_suspects and external_suspects[0]["assessment"] == "prime_suspect":
        summary_bits.append(
            f"prime suspect is OUTSIDE the cluster: "
            f"{external_suspects[0]['endpoint']}"
        )

    return {
        "incident": True,
        "summary": "; ".join(summary_bits) + ".",
        "roots": dressed_roots,
        "external_suspects": external_suspects,
        "unexplained": sorted(unexplained),
        "collateral_map": {k: v for k, v in sorted(collateral_of.items())},
        "healthy_workloads": total - involved,
        "unhealthy_workloads": involved,
        "method_note": (
            "Roots are the frontier of the failure in the observed dependency "
            "graph -- the first broken dependency path, not the loudest "
            "alert. Every claim derives from observed graph, health, and "
            "history; nothing is inferred from log text or time correlation "
            "alone."
        ),
    }


def incident_brief(result: dict, *, cluster_name: str = "cluster") -> str:
    """
    The diagnosis as a paste-ready incident brief.

    Written for the first message in the incident channel: what is actually
    broken, what is merely downstream, who is needed, what to do first, and
    what to leave alone.
    """
    if not result.get("incident"):
        return f"**{cluster_name}**: all workloads healthy. Nothing to diagnose."

    lines = [f"**Incident diagnosis — {cluster_name}**", "", result["summary"], ""]

    for suspect in result.get("external_suspects") or []:
        if suspect["assessment"] == "prime_suspect":
            lines.append(
                f"🚨 **Check {suspect['endpoint']} first** "
                f"({suspect.get('provider') or suspect['category']}): "
                f"{suspect['detail']}"
            )
            lines.append("")

    for i, root in enumerate(result.get("roots") or [], 1):
        name = root["workload_key"].split("/")[-1]
        icon = {"high": "🔴", "medium": "🟠", "low": "⚪"}[root["confidence"]]
        lines.append(
            f"{icon} **{i}. {name}** — {root['confidence']} confidence "
            f"({root['confidence_reason']})"
        )
        states = ", ".join(root["health"]["states"])
        lines.append(f"   state: {states} · explains {root['explains_workloads']} downstream")
        for change in root.get("recent_changes") or []:
            lines.append(f"   changed: {change['kind']} — {change['detail']}")
        if root.get("page"):
            contact = " · ".join(f"{k}: {v}" for k, v in root["page"].items())
            lines.append(f"   page: {contact}")
        for action in root.get("next_actions") or []:
            lines.append(f"   → {action}")
        if root["collateral"]:
            shown = ", ".join(c.split("/")[-1] for c in root["collateral"][:8])
            more = len(root["collateral"]) - 8
            lines.append(
                f"   collateral (leave alone): {shown}"
                + (f" +{more}" if more > 0 else "")
            )
        lines.append("")

    if result.get("unexplained"):
        names = ", ".join(u.split("/")[-1] for u in result["unexplained"])
        lines.append(f"❓ unexplained by the graph: {names} — investigate separately.")
        lines.append("")

    lines.append(
        f"_{result['healthy_workloads']} workloads are healthy and uninvolved._"
    )
    return "\n".join(lines)
