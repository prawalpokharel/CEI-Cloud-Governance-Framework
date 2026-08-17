"""
Validating CEI against controlled failure.

Every number this product reports about blast radius is derived from the
dependency graph. Every evaluation of those numbers has, so far, also been
derived from the dependency graph. That is circular: a metric computed from a
graph, checked against the same graph, can only confirm that the arithmetic is
self-consistent. It cannot establish that the graph corresponds to how the
system actually fails, which is the claim that matters.

Chaos engineering breaks the circle. Kill a workload and watch what actually
stops working. The measurement comes from the cluster's observed behaviour and
has no dependency on the graph, the CEI weights, or any inference this
codebase makes. Prediction and ground truth are then genuinely independent,
and the correlation between them is a real result rather than a restatement.

## The protocol

For each candidate workload:

1. **Predict.** Compute its blast radius before touching anything. The
   predicted set is frozen at this point and never revised afterwards --
   revising it once results are in is how an evaluation becomes a fit.
2. **Perturb.** Kill its pods with a bounded, reversible experiment.
3. **Measure.** Observe which *other* workloads lose readiness during the
   window. The target itself is excluded from its own measured impact; it was
   killed deliberately and counting it inflates every score.
4. **Recover.** Wait for the cluster to return to its pre-experiment state
   before the next run, so one experiment's damage is not attributed to the
   next.

## Reading the result

Reported per experiment as precision and recall, and across experiments as
Spearman rank correlation between predicted and measured blast-radius size.
Rank correlation is the headline because the product's claim is ordinal --
"these are the workloads that can take your system down, worst first" -- and
getting the order right is what that claim requires. Predicting that four
workloads fail when three do is a good result if the ranking holds.

Both error directions are reported rather than a single accuracy figure, since
they mean opposite things. A false positive is a dependency that exists in
configuration but carries no traffic -- the graph is right and the workload is
idle. A false negative is a real dependency the graph never saw, which is the
serious one: it means the map is missing an edge.

## What this cannot establish

Pod deletion is not the only way a workload fails, and it is the gentlest.
Kubernetes reschedules quickly, so a dependent with retries may never register
an outage the graph correctly predicted. Results therefore understate recall
in exactly one direction, which is stated rather than corrected for.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .blast_radius import build_dependency_graph, compute_blast_radius

# Chaos Mesh is the default target: PodChaos needs no eBPF, no tc, and no
# kernel features beyond what any conformant cluster provides, so it runs
# where NetworkChaos and Cilium's datapath cannot.
CHAOS_MESH_API = "chaos-mesh.org/v1alpha1"
LITMUS_API = "litmuschaos.io/v1alpha1"

# Default experiment window. Long enough for a dependent's health check to
# fail and be observed, short enough that a real cluster tolerates it.
DEFAULT_DURATION_SECONDS = 60

# How many of a workload's pods to take out. All of them: a partial outage on
# a replicated workload tests the replica count, not the dependency, and every
# dependent correctly survives -- which measures nothing about reach.
DEFAULT_MODE = "all"


@dataclass
class Prediction:
    workload_key: str
    predicted: set[str]
    severity: str
    user_facing: bool
    centrality_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_key": self.workload_key,
            "predicted": sorted(self.predicted),
            "predicted_count": len(self.predicted),
            "severity": self.severity,
            "user_facing": self.user_facing,
            "centrality_fraction": round(self.centrality_fraction, 4),
        }


@dataclass
class ExperimentResult:
    workload_key: str
    predicted: set[str] = field(default_factory=set)
    measured: set[str] = field(default_factory=set)
    duration_seconds: int = DEFAULT_DURATION_SECONDS
    note: str | None = None

    @property
    def true_positives(self) -> set[str]:
        return self.predicted & self.measured

    @property
    def false_positives(self) -> set[str]:
        return self.predicted - self.measured

    @property
    def false_negatives(self) -> set[str]:
        return self.measured - self.predicted

    @property
    def precision(self) -> float | None:
        return (
            len(self.true_positives) / len(self.predicted)
            if self.predicted else None
        )

    @property
    def recall(self) -> float | None:
        return (
            len(self.true_positives) / len(self.measured)
            if self.measured else None
        )

    @property
    def jaccard(self) -> float | None:
        union = self.predicted | self.measured
        return len(self.true_positives) / len(union) if union else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workload_key": self.workload_key,
            "predicted": sorted(self.predicted),
            "measured": sorted(self.measured),
            "true_positives": sorted(self.true_positives),
            # A dependency that exists in configuration but carries no traffic.
            "false_positives": sorted(self.false_positives),
            # A real dependency the graph never saw. The serious direction.
            "false_negatives": sorted(self.false_negatives),
            "precision": None if self.precision is None else round(self.precision, 4),
            "recall": None if self.recall is None else round(self.recall, 4),
            "jaccard": None if self.jaccard is None else round(self.jaccard, 4),
            "duration_seconds": self.duration_seconds,
            "note": self.note,
        }


def predict(
    snapshot: dict,
    workload_keys: list[str] | None = None,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    limit: int = 10,
) -> list[Prediction]:
    """
    Freeze predictions before any experiment runs.

    Called first and never again. A prediction revised after results arrive is
    not a prediction.
    """
    graph = build_dependency_graph(snapshot)
    if workload_keys is None:
        workload_keys = [
            key for key, data in graph.nodes(data=True) if data.get("is_workload")
        ]

    predictions = []
    for key in workload_keys:
        radius = compute_blast_radius(snapshot, key, cei_by_workload, graph=graph)
        if not radius.exists:
            continue
        predictions.append(Prediction(
            workload_key=key,
            predicted={a.key for a in radius.affected},
            severity=radius.severity,
            user_facing=bool(radius.entry_points),
            centrality_fraction=radius.centrality_fraction,
        ))

    predictions.sort(key=lambda p: (-len(p.predicted), p.workload_key))
    return predictions[:limit]


def chaos_mesh_experiment(
    workload: dict,
    *,
    name: str | None = None,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    namespace: str | None = None,
) -> dict[str, Any]:
    """
    A Chaos Mesh PodChaos manifest targeting one workload.

    `pod-failure` rather than `pod-kill`, because of what is being measured.
    A killed pod is rescheduled within seconds, and a dependent's readiness
    probe -- which typically needs two consecutive failures at a several
    second period -- never observes the outage. The measurement then reads
    zero impact for a dependency that is entirely real, which looks like the
    prediction was wrong when it was the instrument that was too slow.

    `pod-failure` holds the target unavailable for the whole window, so a
    dependent that genuinely cannot serve without it has time to say so. Use
    `pod-kill` to exercise the recovery path; use this to measure reach.

    The selector is the workload's own pod labels, scoped to its namespace, so
    the experiment cannot reach anything the operator did not target.
    """
    workload_namespace = namespace or workload.get("namespace") or "default"
    labels = workload.get("pod_labels") or {}
    if not labels:
        raise ValueError(
            f"{workload.get('key')} has no pod labels; a chaos experiment "
            "without a selector would target the entire namespace."
        )

    return {
        "apiVersion": CHAOS_MESH_API,
        "kind": "PodChaos",
        "metadata": {
            "name": name or f"cei-{workload.get('name')}"[:63],
            "namespace": workload_namespace,
            "labels": {"app.kubernetes.io/managed-by": "cloudoptimizer"},
            "annotations": {
                "cloudoptimizer.io/purpose": (
                    "Blast-radius validation. Measures actual impact against "
                    "the CEI prediction."
                ),
                "cloudoptimizer.io/target": str(workload.get("key")),
            },
        },
        "spec": {
            "action": "pod-failure",
            "mode": DEFAULT_MODE,
            "duration": f"{duration_seconds}s",
            "selector": {
                "namespaces": [workload_namespace],
                "labelSelectors": labels,
            },
        },
    }


def litmus_experiment(
    workload: dict,
    *,
    name: str | None = None,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
) -> dict[str, Any]:
    """
    The Litmus equivalent, for clusters standardised on it.

    Same target, same action; only the CRD differs. Offered so the validation
    protocol does not require adopting a second chaos platform.
    """
    namespace = workload.get("namespace") or "default"
    labels = workload.get("pod_labels") or {}
    if not labels:
        raise ValueError(f"{workload.get('key')} has no pod labels to select on.")
    selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))

    return {
        "apiVersion": LITMUS_API,
        "kind": "ChaosEngine",
        "metadata": {
            "name": name or f"cei-{workload.get('name')}"[:63],
            "namespace": namespace,
        },
        "spec": {
            "appinfo": {
                "appns": namespace,
                "applabel": selector,
                "appkind": (workload.get("kind") or "deployment").lower(),
            },
            "chaosServiceAccount": "litmus-admin",
            "experiments": [{
                "name": "pod-delete",
                "spec": {"components": {"env": [
                    {"name": "TOTAL_CHAOS_DURATION", "value": str(duration_seconds)},
                    {"name": "FORCE", "value": "false"},
                ]}},
            }],
        },
    }


def _ready_state(snapshot: dict) -> dict[str, bool]:
    """
    Which workloads were serving, from a snapshot.

    Readiness rather than pod existence: a workload with three replicas and
    one ready pod is degraded, not healthy, and a workload whose pods are
    Running but failing their readiness probe is not serving at all.
    """
    state = {}
    for workload in snapshot.get("workloads") or []:
        key = workload.get("key")
        if not key:
            continue
        desired = workload.get("replicas_desired")
        ready = workload.get("replicas_ready")
        if desired is None:
            state[key] = ready is None or ready > 0
        elif ready is None:
            # Old agents pass None through when Kubernetes omits readyReplicas
            # -- which it does precisely when ZERO pods are ready. Treating
            # None as healthy therefore read fully-down workloads as serving,
            # and the first live experiment measured zero impact everywhere.
            # None from a current agent means the status object was absent;
            # either way "not known to be serving" is the only safe reading,
            # and the asymmetric handling in measure_impact() keeps it from
            # producing phantom impact.
            state[key] = False
        else:
            state[key] = ready >= desired
    return state


def measure_impact(
    baseline: dict, during: dict, target_key: str
) -> tuple[set[str], list[str]]:
    """
    Which workloads lost readiness while the experiment ran.

    Returns (affected, caveats).

    Only workloads that were healthy in the baseline and degraded during the
    experiment count. A workload already broken beforehand tells us nothing
    about the target, and counting it would credit the prediction for damage
    it did not cause.

    The target is excluded from its own impact: it was killed deliberately.
    """
    before = _ready_state(baseline)
    after = _ready_state(during)
    caveats: list[str] = []

    affected = set()
    for key, was_ready in before.items():
        if key == target_key:
            continue
        if key not in after:
            caveats.append(f"{key} vanished between snapshots; not counted")
            continue
        # Only healthy -> degraded counts. The asymmetry is what makes the
        # conservative _ready_state default safe: a workload with unknown
        # readiness in the BASELINE is excluded here (was_ready is False), so
        # uncertainty can suppress a measurement but never fabricate one.
        if was_ready and not after[key]:
            affected.add(key)

    unhealthy_before = [k for k, ready in before.items() if not ready and k != target_key]
    if unhealthy_before:
        caveats.append(
            f"{len(unhealthy_before)} workload(s) were already unhealthy before "
            "the experiment and were excluded from the measurement."
        )
    return affected, caveats


def _spearman(a: list[float], b: list[float]) -> float | None:
    """
    Spearman rank correlation, with tie-aware ranking.

    Written out rather than pulled from scipy: it is twenty lines, and adding
    scipy to a container that ships to customer clusters for one statistic is
    a poor trade.
    """
    n = len(a)
    if n < 3 or len(b) != n:
        return None

    def rank(values: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[order[j + 1]] == values[order[i]]:
                j += 1
            # Ties share the average of the ranks they span; without this a
            # run of equal values gets an arbitrary order that fabricates
            # correlation.
            shared = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = shared
            i = j + 1
        return ranks

    ra, rb = rank(a), rank(b)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den = math.sqrt(
        sum((x - mean_a) ** 2 for x in ra) * sum((y - mean_b) ** 2 for y in rb)
    )
    return None if den == 0 else max(-1.0, min(1.0, num / den))


def evaluate(results: list[ExperimentResult]) -> dict[str, Any]:
    """
    Aggregate experiments into the validation result.

    Rank correlation is the headline: the product's claim is ordinal, so
    getting the order right is what it requires. Predicting four failures
    where three occur is a good result if the ranking holds.
    """
    if not results:
        return {"experiments": 0, "note": "No experiments were run."}

    predicted_sizes = [float(len(r.predicted)) for r in results]
    measured_sizes = [float(len(r.measured)) for r in results]
    correlation = _spearman(predicted_sizes, measured_sizes)

    precisions = [r.precision for r in results if r.precision is not None]
    recalls = [r.recall for r in results if r.recall is not None]

    total_fp = sum(len(r.false_positives) for r in results)
    total_fn = sum(len(r.false_negatives) for r in results)

    return {
        "experiments": len(results),
        "rank_correlation": None if correlation is None else round(correlation, 4),
        "rank_correlation_note": (
            "Spearman correlation between predicted and measured blast-radius "
            "size. The measurement is taken from observed cluster behaviour "
            "and does not use the dependency graph, the CEI weights, or any "
            "inference this system makes, so it is independent of the "
            "prediction rather than derived from it."
            if correlation is not None else
            "At least three experiments are needed for a rank correlation."
        ),
        "mean_precision": round(sum(precisions) / len(precisions), 4) if precisions else None,
        "mean_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "total_predicted": int(sum(predicted_sizes)),
        "total_measured": int(sum(measured_sizes)),
        "false_positives": total_fp,
        "false_negatives": total_fn,
        "interpretation": {
            "false_positive": (
                "Predicted to fail and did not. Usually a dependency that "
                "exists in configuration but carries no traffic, or a "
                "dependent whose retries covered the outage. The graph is "
                "right and the edge is idle."
            ),
            "false_negative": (
                "Failed without being predicted. The serious direction: the "
                "dependency graph is missing an edge, which means something "
                "real is invisible to every analysis built on it."
            ),
        },
        "limitations": (
            "Pod deletion is the gentlest failure mode. Kubernetes reschedules "
            "quickly and a dependent with retries may never register an outage "
            "the graph correctly predicted, so recall is understated in one "
            "direction. Results are a lower bound on agreement, not a point "
            "estimate."
        ),
        "results": [r.to_dict() for r in results],
    }


def plan(
    snapshot: dict,
    cei_by_workload: dict[str, dict] | None = None,
    *,
    limit: int = 5,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    platform: str = "chaos-mesh",
) -> dict[str, Any]:
    """
    A runnable validation plan: predictions plus the manifests that test them.
    """
    workloads = {
        w["key"]: w for w in (snapshot.get("workloads") or []) if w.get("key")
    }
    predictions = predict(snapshot, None, cei_by_workload, limit=limit)
    builder = litmus_experiment if platform == "litmus" else chaos_mesh_experiment

    experiments = []
    for prediction in predictions:
        workload = workloads.get(prediction.workload_key)
        if workload is None:
            continue
        try:
            manifest = builder(workload, duration_seconds=duration_seconds)
        except ValueError as exc:
            experiments.append({
                "workload_key": prediction.workload_key,
                "skipped": str(exc),
            })
            continue
        experiments.append({
            "workload_key": prediction.workload_key,
            "prediction": prediction.to_dict(),
            "manifest": manifest,
        })

    return {
        "platform": platform,
        "duration_seconds": duration_seconds,
        "experiments": experiments,
        "protocol": [
            "Capture a baseline snapshot and confirm the cluster is healthy.",
            "Apply one experiment manifest.",
            "Capture a snapshot during the experiment window.",
            "Delete the experiment and wait for full recovery before the next.",
            "Feed baseline and during-snapshots to measure_impact(), then "
            "evaluate() across all runs.",
        ],
        "warning": (
            "These manifests delete pods. Run them against a staging cluster "
            "or during an agreed window; they are bounded and reversible but "
            "they are real failures."
        ),
    }
