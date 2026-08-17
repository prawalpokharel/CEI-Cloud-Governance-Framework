"""
Cluster state collection.

Reads workloads, services, nodes, and routing objects through the Kubernetes
API and reduces them to a compact model. Deliberately narrow:

  * Secrets are never read. The RBAC role does not grant access to them, so
    an attempt would fail -- the omission is enforced, not merely intended.
  * ConfigMap *bodies* are never read either. References to a ConfigMap are
    visible in a pod spec without fetching its contents, and skipping the
    contents means "no secrets, no configmaps" is a claim the chart can make
    honestly. People put credentials in ConfigMaps constantly.
  * Environment variable values never leave this process. See redact.py.

Collection is list-based rather than watch-based. A watch would use less
bandwidth, but the agent sends full snapshots anyway, so a periodic list is
simpler, has no resync or event-ordering failure modes, and cannot drift out
of sync with reality after a missed event.
"""

from __future__ import annotations

import logging

from kubernetes import client
from kubernetes.client.rest import ApiException

from .config import AgentConfig
from .redact import extract_service_references, safe_env_summary

log = logging.getLogger(__name__)

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")

# Label and annotation keys that identify who owns a workload.
#
# A curated allowlist, not "all annotations". Annotations are a dumping
# ground: `kubectl.kubernetes.io/last-applied-configuration` holds the entire
# submitted manifest, environment variables included, so collecting
# annotations wholesale would smuggle out exactly the values redact.py exists
# to keep in. Everything here is a name or a team handle by construction.
OWNERSHIP_KEYS = (
    "owner", "owners", "team", "squad", "contact", "email", "slack",
    "app.kubernetes.io/part-of", "app.kubernetes.io/managed-by",
    "app.kubernetes.io/name", "app.kubernetes.io/component",
    "argocd.argoproj.io/instance", "meta.helm.sh/release-name",
)


def parse_cpu(value: str | None) -> float | None:
    """Kubernetes CPU quantity -> cores."""
    if not value:
        return None
    text = str(value)
    try:
        if text.endswith("n"):
            return float(text[:-1]) / 1e9
        if text.endswith("u"):
            return float(text[:-1]) / 1e6
        if text.endswith("m"):
            return float(text[:-1]) / 1000.0
        return float(text)
    except ValueError:
        return None


_MEM_UNITS = {
    "Ki": 1024, "Mi": 1024 ** 2, "Gi": 1024 ** 3, "Ti": 1024 ** 4,
    "Pi": 1024 ** 5, "K": 1000, "M": 1000 ** 2, "G": 1000 ** 3,
    "T": 1000 ** 4, "P": 1000 ** 5,
}


def parse_memory(value: str | None) -> int | None:
    """Kubernetes memory quantity -> bytes."""
    if not value:
        return None
    text = str(value)
    try:
        for suffix, multiplier in _MEM_UNITS.items():
            if text.endswith(suffix):
                return int(float(text[: -len(suffix)]) * multiplier)
        return int(float(text))
    except ValueError:
        return None


def ownership_metadata(labels: dict | None, annotations: dict | None) -> dict:
    """
    Extract the subset of labels and annotations that name an owner.

    Values are truncated: a team handle is short, and anything long is either
    a description or something that does not belong in an owner field.
    """
    found = {}
    for source in (labels or {}, annotations or {}):
        for key, value in source.items():
            if key in OWNERSHIP_KEYS and value:
                found[key] = str(value)[:120]
    return found


def config_references(pod_spec) -> dict[str, list[str]]:
    """
    Which ConfigMaps and Secrets a pod spec reads, by name only.

    Names, never contents -- the RBAC role grants no access to Secret bodies
    and the collector deliberately does not read ConfigMap bodies either. The
    reason to collect this at all is fan-in: a ConfigMap twelve workloads
    mount is a single point of failure that no Kubernetes view surfaces,
    because from each workload's side it looks like an ordinary reference.
    """
    config_maps: set[str] = set()
    secrets: set[str] = set()

    for container in (pod_spec.containers or []) + (pod_spec.init_containers or []):
        for source in (container.env_from or []):
            if getattr(source, "config_map_ref", None):
                config_maps.add(source.config_map_ref.name)
            if getattr(source, "secret_ref", None):
                secrets.add(source.secret_ref.name)
        for env in (container.env or []):
            value_from = getattr(env, "value_from", None)
            if not value_from:
                continue
            if getattr(value_from, "config_map_key_ref", None):
                config_maps.add(value_from.config_map_key_ref.name)
            if getattr(value_from, "secret_key_ref", None):
                secrets.add(value_from.secret_key_ref.name)

    for volume in (pod_spec.volumes or []):
        if getattr(volume, "config_map", None):
            config_maps.add(volume.config_map.name)
        if getattr(volume, "secret", None):
            secrets.add(volume.secret.secret_name)
        projected = getattr(volume, "projected", None)
        for source in (getattr(projected, "sources", None) or []):
            if getattr(source, "config_map", None):
                config_maps.add(source.config_map.name)
            if getattr(source, "secret", None):
                secrets.add(source.secret.name)

    return {
        "config_maps": sorted(n for n in config_maps if n),
        "secrets": sorted(n for n in secrets if n),
    }


def probe_coverage(containers) -> dict:
    """
    Probe presence per container, counted rather than reduced to a boolean.

    A workload where one of three containers has a readiness probe is not
    "has readiness probe" in any useful sense: traffic is routed to the pod as
    soon as the probed container is up, while the unprobed ones may still be
    starting. Counts keep that distinction visible.
    """
    containers = list(containers or [])
    return {
        "containers": len(containers),
        "with_readiness": sum(1 for c in containers if c.readiness_probe),
        "with_liveness": sum(1 for c in containers if c.liveness_probe),
        "with_startup": sum(1 for c in containers if c.startup_probe),
    }


def spread_policy(pod_spec) -> dict:
    """
    Whether anything stops every replica landing on one node.

    Three mechanisms do it -- topology spread constraints, pod anti-affinity,
    and (weakly) a node selector splitting the fleet. Presence is recorded
    rather than evaluated; whether the policy is *sufficient* depends on the
    observed placement, which is a server-side question.
    """
    affinity = getattr(pod_spec, "affinity", None)
    pod_anti = getattr(affinity, "pod_anti_affinity", None) if affinity else None

    required = list(
        getattr(pod_anti, "required_during_scheduling_ignored_during_execution", None) or []
    )
    preferred = list(
        getattr(pod_anti, "preferred_during_scheduling_ignored_during_execution", None) or []
    )
    constraints = list(getattr(pod_spec, "topology_spread_constraints", None) or [])

    return {
        "topology_spread_constraints": len(constraints),
        "topology_keys": sorted({c.topology_key for c in constraints if c.topology_key}),
        "anti_affinity_required": len(required),
        "anti_affinity_preferred": len(preferred),
        "priority_class": getattr(pod_spec, "priority_class_name", None),
    }


def workload_key(namespace: str, kind: str, name: str) -> str:
    """
    Stable identity across restarts.

    Pod names churn on every rollout; workload identity does not. Every
    server-side table keys on this.
    """
    return f"{namespace}/{kind}/{name}"


class ClusterCollector:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.core = client.CoreV1Api()
        self.apps = client.AppsV1Api()
        self.networking = client.NetworkingV1Api()
        self.version_api = client.VersionApi()

    # -- identity ----------------------------------------------------------

    def cluster_fingerprint(self) -> str:
        """
        The kube-system namespace UID.

        Conventional stable cluster identifier: it is created with the
        cluster, never changes, and is readable without elevated permissions.
        Node names, API server URLs, and cloud instance IDs all change.
        """
        try:
            ns = self.core.read_namespace(name="kube-system")
            return ns.metadata.uid or ""
        except ApiException as exc:
            log.warning("Could not read kube-system namespace UID: %s", exc)
            return ""

    def kubernetes_version(self) -> str | None:
        try:
            info = self.version_api.get_code()
            return f"{info.major}.{info.minor}".replace("+", "")
        except Exception:
            return None

    def detect_provider(self, nodes: list[dict]) -> str:
        """
        Infer the platform from node labels and providerID.

        Best-effort only; reported so the UI can label the cluster and so
        provider-specific guidance can be shown. "unknown" is an acceptable
        answer and must not block anything.
        """
        for node in nodes:
            provider_id = (node.get("provider_id") or "").lower()
            if provider_id.startswith("aws"):
                return "eks"
            if provider_id.startswith("azure"):
                return "aks"
            if provider_id.startswith("gce"):
                return "gke"
            if provider_id.startswith("kind"):
                return "kind"
            labels = node.get("labels", {})
            if any(k.startswith("eks.amazonaws.com/") for k in labels):
                return "eks"
            if any(k.startswith("kubernetes.azure.com/") for k in labels):
                return "aks"
            if any(k.startswith("cloud.google.com/gke-") for k in labels):
                return "gke"
            if "node-role.kubernetes.io/control-plane" in labels and not provider_id:
                return "k3s" if "k3s" in str(labels) else "other"
        return "unknown"

    # -- collection --------------------------------------------------------

    def collect_nodes(self) -> list[dict]:
        out = []
        for node in self.core.list_node().items:
            allocatable = node.status.allocatable or {}
            capacity = node.status.capacity or {}
            conditions = {
                c.type: c.status for c in (node.status.conditions or [])
            }
            info = node.status.node_info
            out.append({
                "name": node.metadata.name,
                "uid": node.metadata.uid,
                "labels": node.metadata.labels or {},
                "provider_id": node.spec.provider_id,
                "unschedulable": bool(node.spec.unschedulable),
                "ready": conditions.get("Ready") == "True",
                "allocatable_cpu_cores": parse_cpu(allocatable.get("cpu")),
                # GPUs, where the device plugin advertises them. Integer
                # count only -- MIG slices and NVLink topology are not in the
                # Kubernetes API, and the fragmentation analysis says so.
                "allocatable_gpus": int(allocatable.get("nvidia.com/gpu") or 0),
                "allocatable_memory_bytes": parse_memory(allocatable.get("memory")),
                "capacity_cpu_cores": parse_cpu(capacity.get("cpu")),
                "capacity_memory_bytes": parse_memory(capacity.get("memory")),
                "kubelet_version": getattr(info, "kubelet_version", None),
                "os_image": getattr(info, "os_image", None),
                "architecture": getattr(info, "architecture", None),
                "instance_type": (node.metadata.labels or {}).get(
                    "node.kubernetes.io/instance-type"
                ),
                "region": (node.metadata.labels or {}).get(
                    "topology.kubernetes.io/region"
                ),
                "zone": (node.metadata.labels or {}).get(
                    "topology.kubernetes.io/zone"
                ),
            })
        return out

    def collect_services(self) -> list[dict]:
        out = []
        for svc in self.core.list_service_for_all_namespaces().items:
            ns = svc.metadata.namespace
            if not self.config.wants_namespace(ns):
                continue
            out.append({
                "name": svc.metadata.name,
                "namespace": ns,
                "uid": svc.metadata.uid,
                "type": svc.spec.type,
                "selector": svc.spec.selector or {},
                "ports": [
                    {"port": p.port, "target_port": str(p.target_port),
                     "protocol": p.protocol, "name": p.name}
                    for p in (svc.spec.ports or [])
                ],
                "cluster_ip": svc.spec.cluster_ip,
            })
        return out

    def _workloads_of(self, kind: str, items) -> list[dict]:
        out = []
        for item in items:
            ns = item.metadata.namespace
            if not self.config.wants_namespace(ns):
                continue

            spec = item.spec
            pod_spec = spec.template.spec
            containers = pod_spec.containers or []

            cpu_req = mem_req = 0.0
            cpu_lim = mem_lim = 0.0
            gpu_req = 0
            env_names: list[str] = []
            env_values: list[str] = []
            images = []

            for container in containers:
                images.append(container.image)
                resources = container.resources
                requests = (resources.requests or {}) if resources else {}
                limits = (resources.limits or {}) if resources else {}
                cpu_req += parse_cpu(requests.get("cpu")) or 0.0
                mem_req += parse_memory(requests.get("memory")) or 0
                cpu_lim += parse_cpu(limits.get("cpu")) or 0.0
                mem_lim += parse_memory(limits.get("memory")) or 0
                gpu_req += int(requests.get("nvidia.com/gpu") or limits.get("nvidia.com/gpu") or 0)
                for env in (container.env or []):
                    env_names.append(env.name)
                    # Values are held only in this process, for reference
                    # extraction. They are never placed on the snapshot.
                    if env.value:
                        env_values.append(env.value)

            status = item.status
            replicas_desired = (
                getattr(spec, "replicas", None)
                if kind != "DaemonSet"
                else getattr(status, "desired_number_scheduled", None)
            )

            # Requests are declared per pod, but metrics are summed across
            # every pod in the workload. Reporting one per-pod and the other
            # fleet-wide made them incomparable: a 3-replica deployment
            # appeared to use 3x more of its request than it did, understating
            # waste and — once cost is attached — understating spend by the
            # replica count.
            #
            # Both are reported fleet-wide. The per-pod values are kept
            # alongside because rightsizing acts on the pod spec, not the
            # fleet.
            fleet = max(1, replicas_desired or 1)
            out.append({
                "key": workload_key(ns, kind, item.metadata.name),
                "name": item.metadata.name,
                "namespace": ns,
                "kind": kind,
                "uid": item.metadata.uid,
                "labels": item.metadata.labels or {},
                "pod_labels": (spec.template.metadata.labels or {})
                if spec.template.metadata else {},
                "images": images,
                "replicas_desired": replicas_desired,
                # Kubernetes OMITS readyReplicas when zero pods are ready --
                # the API returns no field rather than 0. Passing the None
                # through made a fully-down workload indistinguishable from
                # one whose readiness is unknown, and every consumer that
                # treated None as "assume serving" then read a dead workload
                # as healthy. 0 is the truthful value whenever the status
                # object itself was present.
                "replicas_ready": (
                    (getattr(status, "ready_replicas", None) or 0)
                    if kind != "DaemonSet"
                    else (getattr(status, "number_ready", None) or 0)
                ) if status is not None else None,
                # Fleet-wide, comparable to cpu_cores_used / memory_bytes_used.
                "cpu_cores_requested": (cpu_req * fleet) if cpu_req else None,
                "memory_bytes_requested": (mem_req * fleet) if mem_req else None,
                "cpu_cores_limit": (cpu_lim * fleet) if cpu_lim else None,
                "memory_bytes_limit": (mem_lim * fleet) if mem_lim else None,
                # Per pod, which is what a rightsizing change actually edits.
                "gpus_requested_per_pod": gpu_req or 0,
                "cpu_cores_requested_per_pod": cpu_req or None,
                "memory_bytes_requested_per_pod": mem_req or None,
                "env_summary": safe_env_summary(env_names),
                "_env_values": env_values,  # stripped before transmission
                "service_account": pod_spec.service_account_name,
                "node_selector": pod_spec.node_selector or {},
                # Resilience signals. Collected so the server can answer "what
                # stops this from being a single point of failure" without a
                # second round trip to the cluster.
                "ownership": ownership_metadata(
                    item.metadata.labels, item.metadata.annotations
                ),
                "probes": probe_coverage(containers),
                "spread": spread_policy(pod_spec),
                "config_refs": config_references(pod_spec),
            })
        return out

    def collect_workloads(self) -> list[dict]:
        workloads = []
        workloads += self._workloads_of(
            "Deployment", self.apps.list_deployment_for_all_namespaces().items
        )
        workloads += self._workloads_of(
            "StatefulSet", self.apps.list_stateful_set_for_all_namespaces().items
        )
        workloads += self._workloads_of(
            "DaemonSet", self.apps.list_daemon_set_for_all_namespaces().items
        )
        return workloads

    def collect_disruption_budgets(self) -> list[dict]:
        """
        PodDisruptionBudgets, with the field that actually matters.

        ``disruptions_allowed`` is computed by the controller against live pod
        health, and it is the only number that says what the PDB will do right
        now. A budget of ``minAvailable: 1`` on a single-replica Deployment
        reports 0 allowed disruptions -- it does not protect the workload, it
        blocks every node drain, and the cluster upgrade that stalls three
        months later is never traced back to it.
        """
        return self._optional_list(
            "poddisruptionbudgets",
            lambda: client.PolicyV1Api().list_pod_disruption_budget_for_all_namespaces().items,
            self._pdb_record,
        )

    @staticmethod
    def _pdb_record(item) -> dict:
        spec, status = item.spec, item.status
        selector = getattr(spec.selector, "match_labels", None) or {} if spec.selector else {}
        return {
            "name": item.metadata.name,
            "namespace": item.metadata.namespace,
            "selector": selector,
            "min_available": str(spec.min_available) if spec.min_available is not None else None,
            "max_unavailable": str(spec.max_unavailable) if spec.max_unavailable is not None else None,
            "disruptions_allowed": getattr(status, "disruptions_allowed", None),
            "current_healthy": getattr(status, "current_healthy", None),
            "desired_healthy": getattr(status, "desired_healthy", None),
            "expected_pods": getattr(status, "expected_pods", None),
        }

    def collect_autoscalers(self) -> list[dict]:
        """
        HorizontalPodAutoscalers and whether they are currently pinned.

        A HPA sitting at ``current == max`` is not autoscaling; it ran out of
        room and is silently absorbing load it cannot shed. That reads as
        healthy on every dashboard right up until it does not.
        """
        return self._optional_list(
            "horizontalpodautoscalers",
            lambda: client.AutoscalingV2Api().list_horizontal_pod_autoscaler_for_all_namespaces().items,
            self._hpa_record,
        )

    @staticmethod
    def _hpa_record(item) -> dict:
        spec, status = item.spec, item.status
        target = spec.scale_target_ref

        # What the autoscaler is watching, not just its bounds. An HPA scaling
        # a workload on its own CPU while that workload's real failure mode is
        # an upstream dependency is watching the wrong signal, and the only
        # way to detect that server-side is to know the signal.
        metrics = []
        for metric in (getattr(spec, "metrics", None) or []):
            metric_type = getattr(metric, "type", None)
            entry = {"type": metric_type}
            resource = getattr(metric, "resource", None)
            if resource is not None:
                entry["resource"] = getattr(resource, "name", None)
            metrics.append(entry)

        return {
            "name": item.metadata.name,
            "namespace": item.metadata.namespace,
            "target_kind": getattr(target, "kind", None),
            "target_name": getattr(target, "name", None),
            "min_replicas": spec.min_replicas,
            "max_replicas": spec.max_replicas,
            "current_replicas": getattr(status, "current_replicas", None),
            "desired_replicas": getattr(status, "desired_replicas", None),
            # Empty list means "no metrics field", which Kubernetes treats as
            # CPU at 80% -- reported as-is and interpreted server-side.
            "metrics": metrics,
        }

    def _optional_list(self, resource: str, fetch, to_record) -> list[dict]:
        """
        Collect a resource the agent can run without.

        These collections were added after the chart shipped, so an agent
        upgraded ahead of its ClusterRole will be denied. Losing one signal is
        an acceptable outcome; failing the whole snapshot -- and with it CEI,
        cost, and health for the entire cluster -- is not. A 403 is logged once
        per cycle at warning level with the fix, and the rest proceeds.
        """
        try:
            items = fetch()
        except ApiException as exc:
            if exc.status in (403, 404):
                log.warning(
                    "%s not collected (HTTP %s). Upgrade the agent's ClusterRole "
                    "to grant get/list/watch on %s; other analysis is unaffected.",
                    resource, exc.status, resource,
                )
            else:
                log.warning("%s not collected: %s", resource, exc)
            return []
        except Exception as exc:  # API group absent on very old clusters
            log.warning("%s not collected: %s", resource, exc)
            return []

        out = []
        for item in items:
            if not self.config.wants_namespace(item.metadata.namespace):
                continue
            out.append(to_record(item))
        return out

    def collect_pods(self) -> list[dict]:
        """
        Pod-level state, kept deliberately thin.

        Only what health diagnostics need (Phase 2): scheduling state,
        restarts, and waiting reasons such as CrashLoopBackOff. Full pod specs
        would multiply snapshot size for data the workload view already has.
        """
        out = []
        for pod in self.core.list_pod_for_all_namespaces().items:
            ns = pod.metadata.namespace
            if not self.config.wants_namespace(ns):
                continue
            statuses = pod.status.container_statuses or []
            restarts = sum(s.restart_count or 0 for s in statuses)
            waiting = [
                s.state.waiting.reason
                for s in statuses
                if s.state and s.state.waiting and s.state.waiting.reason
            ]
            terminated = [
                s.last_state.terminated.reason
                for s in statuses
                if s.last_state
                and s.last_state.terminated
                and s.last_state.terminated.reason
            ]
            owner = (pod.metadata.owner_references or [None])[0]
            out.append({
                "name": pod.metadata.name,
                "namespace": ns,
                "phase": pod.status.phase,
                "node_name": pod.spec.node_name,
                "labels": pod.metadata.labels or {},
                "restart_count": restarts,
                "waiting_reasons": waiting,
                "last_terminated_reasons": terminated,
                "owner_kind": getattr(owner, "kind", None),
                "owner_name": getattr(owner, "name", None),
                "ready": all(
                    (s.ready for s in statuses),
                ) if statuses else False,
            })
        return out

    def collect_ingresses(self) -> list[dict]:
        out = []
        try:
            items = self.networking.list_ingress_for_all_namespaces().items
        except ApiException as exc:
            log.debug("Ingresses unavailable: %s", exc)
            return out
        for ing in items:
            ns = ing.metadata.namespace
            if not self.config.wants_namespace(ns):
                continue
            backends = []
            for rule in (ing.spec.rules or []):
                http = getattr(rule, "http", None)
                for path in (getattr(http, "paths", None) or []):
                    svc = getattr(path.backend, "service", None)
                    if svc is not None:
                        backends.append({"service": svc.name, "host": rule.host})
            out.append({
                "name": ing.metadata.name,
                "namespace": ns,
                "backends": backends,
            })
        return out

    def collect_network_policies(self) -> list[dict]:
        out = []
        try:
            items = self.networking.list_network_policy_for_all_namespaces().items
        except ApiException as exc:
            log.debug("NetworkPolicies unavailable: %s", exc)
            return out
        for np in items:
            ns = np.metadata.namespace
            if not self.config.wants_namespace(ns):
                continue
            out.append({
                "name": np.metadata.name,
                "namespace": ns,
                "pod_selector": (np.spec.pod_selector.match_labels or {})
                if np.spec.pod_selector else {},
                "policy_types": np.spec.policy_types or [],
            })
        return out


def attach_service_references(
    workloads: list[dict], services: list[dict]
) -> None:
    """
    Resolve each workload's env values into service references, then discard
    the values.

    Mutates in place and removes the ``_env_values`` key, so a caller that
    forgets to strip it cannot leak anything: after this runs the values are
    simply not present on the object.
    """
    known = {svc["name"].lower() for svc in services}
    for workload in workloads:
        values = workload.pop("_env_values", [])
        references = extract_service_references(values, known)
        # Sorted by an explicit key: namespace is optional, and comparing
        # None against a string raises in Python 3.
        workload["service_references"] = [
            {"service": name, "namespace": namespace}
            for name, namespace in sorted(
                references, key=lambda ref: (ref[0], ref[1] or "")
            )
        ]
