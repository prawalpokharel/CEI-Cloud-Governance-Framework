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
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from .config import AgentConfig
from .redact import extract_service_references, safe_env_summary

log = logging.getLogger(__name__)

WORKLOAD_KINDS = ("Deployment", "StatefulSet", "DaemonSet")


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
                "replicas_ready": getattr(status, "ready_replicas", None)
                if kind != "DaemonSet"
                else getattr(status, "number_ready", None),
                # Fleet-wide, comparable to cpu_cores_used / memory_bytes_used.
                "cpu_cores_requested": (cpu_req * fleet) if cpu_req else None,
                "memory_bytes_requested": (mem_req * fleet) if mem_req else None,
                "cpu_cores_limit": (cpu_lim * fleet) if cpu_lim else None,
                "memory_bytes_limit": (mem_lim * fleet) if mem_lim else None,
                # Per pod, which is what a rightsizing change actually edits.
                "cpu_cores_requested_per_pod": cpu_req or None,
                "memory_bytes_requested_per_pod": mem_req or None,
                "env_summary": safe_env_summary(env_names),
                "_env_values": env_values,  # stripped before transmission
                "service_account": pod_spec.service_account_name,
                "node_selector": pod_spec.node_selector or {},
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
