"""
Reading a pull request as a set of changes to cluster objects.

A diff is a change to text. What matters for blast radius is the change to
*objects*: which workload, and which property of it. `replicas: 3` becoming
`replicas: 1` and a whitespace fix are the same size in a diff and nothing
alike in consequence.

So both sides of every changed file are parsed as Kubernetes objects and
compared field by field. Semantic rather than textual, which also means a
reformatted file with no real change produces no findings -- important,
because a tool that fires on `yamlfmt` gets turned off.

## Change kinds, and why these

The classification exists to answer "how does this fail", not to enumerate
YAML paths. Each kind below has a distinct failure mode:

* **deleted** -- everything downstream loses it at once.
* **selector changed** -- the worst of the set, and the reason this module is
  field-aware. Editing a Service selector or a Deployment's `matchLabels`
  produces no error, no failed rollout, and no unhealthy pod. Traffic simply
  stops arriving, and the old pods keep running and passing their probes.
* **replicas reduced** -- capacity goes away quietly and shows up as latency.
* **image changed** -- the ordinary case, and the one where knowing the
  dependents is most useful for staging a rollout.
* **resources reduced** -- OOMKills and CPU throttling, which surface as
  failures in the callers rather than here.
* **probe removed** -- traffic reaches pods that are not ready.
* **config data changed** -- reaches every workload mounting the object, with
  no rollout to make the connection visible.

## What is deliberately not parsed

Helm templates and anything else containing `{{ }}` are not valid YAML and
are reported as unparseable rather than guessed at. Rendering them needs
values files, a chart context, and a Helm binary; a wrong guess about a
templated manifest is worse than an honest gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

WORKLOAD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}
CONFIG_KINDS = {"ConfigMap", "Secret"}
ROUTING_KINDS = {"Service", "Ingress"}
POLICY_KINDS = {"PodDisruptionBudget", "HorizontalPodAutoscaler", "NetworkPolicy"}

RELEVANT_KINDS = WORKLOAD_KINDS | CONFIG_KINDS | ROUTING_KINDS | POLICY_KINDS

MANIFEST_SUFFIXES = (".yaml", ".yml")

# Kustomize's overlay file. Its `namespace:` field is applied to every
# resource the overlay builds, which is how the majority of manifests that
# omit metadata.namespace still land in the right place.
KUSTOMIZATION_NAMES = ("kustomization.yaml", "kustomization.yml", "Kustomization")

# Go template delimiters. Their presence means the file is a chart template
# and is not YAML until Helm has rendered it.
_TEMPLATED = re.compile(r"\{\{.*?\}\}", re.DOTALL)

# Ordered by consequence: the first matching kind names the change.
SEVERITY_BY_KIND = {
    "deleted": "critical",
    "selector_changed": "critical",
    "replicas_reduced": "high",
    "resources_reduced": "high",
    "probe_removed": "high",
    "config_data_changed": "high",
    "image_changed": "moderate",
    "replicas_increased": "low",
    "resources_increased": "low",
    "created": "low",
    "other_change": "low",
}


@dataclass
class ObjectChange:
    kind: str
    name: str
    namespace: str | None
    path: str
    change_kinds: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        return min(
            (SEVERITY_BY_KIND.get(k, "low") for k in self.change_kinds),
            key=lambda s: ["critical", "high", "moderate", "low"].index(s),
            default="low",
        )

    def workload_key(self, default_namespace: str = "default") -> str | None:
        """The key used everywhere else in the system, when this is a workload."""
        if self.kind not in WORKLOAD_KINDS:
            return None
        return f"{self.namespace or default_namespace}/{self.kind}/{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "path": self.path,
            "change_kinds": self.change_kinds,
            "details": self.details,
            "severity": self.severity,
        }


def is_manifest_path(path: str) -> bool:
    return path.lower().endswith(MANIFEST_SUFFIXES)


def is_kustomization_path(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in KUSTOMIZATION_NAMES


def directory_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def ancestor_directories(path: str) -> list[str]:
    """
    Directories from the file's own outward to the repository root.

    Ordered nearest-first because kustomize resolves the same way: the
    closest overlay wins over a base further up.
    """
    directory = directory_of(path)
    out = [directory]
    while "/" in directory:
        directory = directory.rsplit("/", 1)[0]
        out.append(directory)
    if "" not in out:
        out.append("")
    return out


def parse_kustomization_namespace(text: str) -> str | None:
    """Extract the `namespace:` an overlay applies to everything it builds."""
    if not text or looks_templated(text):
        return None
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict):
        return None
    namespace = document.get("namespace")
    return str(namespace) if namespace else None


def kustomize_namespaces(files: list[dict]) -> dict[str, str]:
    """Map directory -> namespace for every kustomization file supplied."""
    found: dict[str, str] = {}
    for entry in files:
        path = entry.get("path") or ""
        if not is_kustomization_path(path):
            continue
        text = entry.get("after") or entry.get("before") or ""
        namespace = parse_kustomization_namespace(text)
        if namespace:
            found[directory_of(path)] = namespace
    return found


def looks_templated(text: str) -> bool:
    return bool(_TEMPLATED.search(text or ""))


def parse_manifests(text: str) -> tuple[list[dict], str | None]:
    """
    Parse a possibly multi-document YAML file into Kubernetes objects.

    Returns (objects, error). A parse failure yields an error string rather
    than an exception: one malformed file in a large PR should not prevent
    analysis of the rest.
    """
    if not text or not text.strip():
        return [], None
    if looks_templated(text):
        return [], "contains Go template directives (Helm chart, not plain YAML)"
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError as exc:
        return [], f"YAML parse error: {str(exc).splitlines()[0][:160]}"

    objects = []
    for document in documents:
        if isinstance(document, dict) and document.get("kind") and document.get("metadata"):
            objects.append(document)
    return objects, None


def _identity(obj: dict) -> tuple[str, str, str | None]:
    metadata = obj.get("metadata") or {}
    return obj.get("kind"), metadata.get("name"), metadata.get("namespace")


def _pod_spec(obj: dict) -> dict:
    return ((obj.get("spec") or {}).get("template") or {}).get("spec") or {}


def _containers(obj: dict) -> list[dict]:
    return _pod_spec(obj).get("containers") or []


def _images(obj: dict) -> list[str]:
    return [c.get("image") for c in _containers(obj) if c.get("image")]


def _selector(obj: dict) -> Any:
    """
    The label selector, wherever this kind keeps it.

    Deployments nest it under spec.selector.matchLabels; Services put it flat
    at spec.selector. Both are the mechanism that binds an object to pods, and
    both break silently when edited.
    """
    spec = obj.get("spec") or {}
    if obj.get("kind") in ROUTING_KINDS:
        return spec.get("selector")
    selector = spec.get("selector") or {}
    if isinstance(selector, dict):
        return selector.get("matchLabels") or selector or None
    return selector


def _resource_totals(obj: dict) -> dict[str, dict]:
    return {
        c.get("name") or f"container-{i}": (c.get("resources") or {})
        for i, c in enumerate(_containers(obj))
    }


def _probe_names(obj: dict) -> set[tuple[str, str]]:
    found = set()
    for i, container in enumerate(_containers(obj)):
        name = container.get("name") or f"container-{i}"
        for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
            if container.get(probe):
                found.add((name, probe))
    return found


def _quantity(value: Any) -> float | None:
    """Coarse comparison of a Kubernetes quantity; used only for direction."""
    if value is None:
        return None
    text = str(value).strip()
    units = {
        "m": 1e-3, "n": 1e-9, "u": 1e-6, "k": 1e3, "K": 1e3,
        "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
        "M": 1e6, "G": 1e9, "T": 1e12,
    }
    for suffix in sorted(units, key=len, reverse=True):
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)]) * units[suffix]
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None


def compare(before: dict | None, after: dict | None, path: str) -> ObjectChange | None:
    """Classify what changed between two versions of one object."""
    source = after or before
    if source is None:
        return None
    kind, name, namespace = _identity(source)
    if kind not in RELEVANT_KINDS or not name:
        return None

    change = ObjectChange(kind=kind, name=name, namespace=namespace, path=path)

    if before is not None and after is None:
        change.change_kinds.append("deleted")
        change.details.append(f"{kind} {name} is removed by this change.")
        return change

    if before is None:
        change.change_kinds.append("created")
        change.details.append(f"{kind} {name} is new.")
        return change

    # --- selector ---------------------------------------------------------
    before_selector, after_selector = _selector(before), _selector(after)
    if before_selector != after_selector:
        change.change_kinds.append("selector_changed")
        change.details.append(
            f"Label selector changes from {before_selector} to {after_selector}. "
            "Kubernetes reports no error for this: existing pods keep running "
            "and passing their probes while traffic stops reaching them."
        )

    # --- replicas ---------------------------------------------------------
    before_replicas = (before.get("spec") or {}).get("replicas")
    after_replicas = (after.get("spec") or {}).get("replicas")
    if (
        isinstance(before_replicas, int) and isinstance(after_replicas, int)
        and before_replicas != after_replicas
    ):
        if after_replicas < before_replicas:
            change.change_kinds.append("replicas_reduced")
            change.details.append(
                f"Replicas drop from {before_replicas} to {after_replicas}"
                + (
                    " — to a single replica, so any disruption is a full outage."
                    if after_replicas == 1 else
                    " — to zero, which stops the workload entirely."
                    if after_replicas == 0 else "."
                )
            )
        else:
            change.change_kinds.append("replicas_increased")
            change.details.append(
                f"Replicas rise from {before_replicas} to {after_replicas}."
            )

    # --- images -----------------------------------------------------------
    before_images, after_images = _images(before), _images(after)
    if before_images != after_images:
        change.change_kinds.append("image_changed")
        change.details.append(
            f"Image changes from {', '.join(before_images) or 'none'} to "
            f"{', '.join(after_images) or 'none'}."
        )

    # --- resources --------------------------------------------------------
    before_resources, after_resources = _resource_totals(before), _resource_totals(after)
    for container, after_spec in after_resources.items():
        before_spec = before_resources.get(container)
        if before_spec is None or before_spec == after_spec:
            continue
        for section in ("requests", "limits"):
            for resource in ("cpu", "memory"):
                raw_old = (before_spec.get(section) or {}).get(resource)
                raw_new = (after_spec.get(section) or {}).get(resource)
                old, new = _quantity(raw_old), _quantity(raw_new)
                if old is None or new is None or new == old:
                    continue
                # Both directions are reported. An increase is not a risk, but
                # calling it "an unclassified change" when the direction is
                # known reads as though the analysis gave up.
                direction = "reduced" if new < old else "increased"
                kind_name = f"resources_{direction}"
                if kind_name not in change.change_kinds:
                    change.change_kinds.append(kind_name)
                change.details.append(
                    f"{container}: {section}.{resource} {direction} from "
                    f"{raw_old} to {raw_new}."
                )

    # --- probes -----------------------------------------------------------
    removed_probes = _probe_names(before) - _probe_names(after)
    if removed_probes:
        change.change_kinds.append("probe_removed")
        for container, probe in sorted(removed_probes):
            change.details.append(f"{container}: {probe} removed.")

    # --- config data ------------------------------------------------------
    if kind in CONFIG_KINDS:
        before_data = {**(before.get("data") or {}), **(before.get("stringData") or {})}
        after_data = {**(after.get("data") or {}), **(after.get("stringData") or {})}
        if before_data != after_data:
            change.change_kinds.append("config_data_changed")
            touched = sorted(
                set(before_data) ^ set(after_data)
                | {k for k in set(before_data) & set(after_data)
                   if before_data[k] != after_data[k]}
            )
            # Keys, never values: a Secret's contents must not be echoed into
            # a pull request comment, and a ConfigMap's often should not be
            # either.
            change.details.append(
                f"{len(touched)} key(s) changed: {', '.join(touched[:8])}"
                + (" …" if len(touched) > 8 else "")
            )

    if not change.change_kinds and before != after:
        change.change_kinds.append("other_change")
        change.details.append("Fields changed that this analysis does not classify.")

    return change if change.change_kinds else None


def diff_files(files: list[dict]) -> tuple[list[ObjectChange], list[dict]]:
    """
    Turn (path, before_text, after_text) records into object changes.

    Each file record needs ``path``, ``before`` and ``after`` (either may be
    None). Returns (changes, skipped) where skipped explains every file that
    could not be analysed -- silence about an unparseable manifest reads as a
    clean bill of health.
    """
    changes: list[ObjectChange] = []
    skipped: list[dict] = []

    for entry in files:
        path = entry.get("path") or ""
        if not is_manifest_path(path):
            continue

        before_objects, before_error = parse_manifests(entry.get("before") or "")
        after_objects, after_error = parse_manifests(entry.get("after") or "")
        error = after_error or before_error
        if error:
            skipped.append({"path": path, "reason": error})
            continue
        if not before_objects and not after_objects:
            continue

        before_index = {_identity(o): o for o in before_objects}
        after_index = {_identity(o): o for o in after_objects}

        for identity in sorted(
            set(before_index) | set(after_index),
            key=lambda i: tuple(str(part) for part in i),
        ):
            change = compare(
                before_index.get(identity), after_index.get(identity), path
            )
            if change:
                changes.append(change)

    order = ["critical", "high", "moderate", "low"]
    changes.sort(key=lambda c: (order.index(c.severity), c.kind, c.name))
    return changes, skipped
