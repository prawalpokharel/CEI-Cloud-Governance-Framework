"""
metrics-server integration.

Actual CPU/memory usage comes from the metrics.k8s.io aggregated API, which
is NOT installed by default -- kind ships without it, and plenty of managed
clusters have it disabled. Its absence is a supported state, not an error.

When it is missing the agent reports usage as null rather than zero. Zero is
indistinguishable from a genuinely idle workload, and a "this pod uses no
CPU, consider removing it" recommendation derived from a missing metrics
endpoint would be actively dangerous.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from kubernetes import client
from kubernetes.client.rest import ApiException

from .collector import parse_cpu, parse_memory

log = logging.getLogger(__name__)


class MetricsCollector:
    def __init__(self):
        self.api = client.CustomObjectsApi()
        self.available: bool | None = None
        self.reason: str | None = None

    def collect_pod_metrics(self) -> tuple[dict, bool]:
        """
        Return ({namespace/pod: {cpu_cores, memory_bytes}}, available).

        Failure is downgraded to "unavailable" rather than raised: the agent
        must keep reporting topology even when metrics are missing, because
        the dependency graph is useful on its own.
        """
        try:
            raw = self.api.list_cluster_custom_object(
                group="metrics.k8s.io", version="v1beta1", plural="pods"
            )
        except ApiException as exc:
            self.available = False
            if exc.status == 404:
                self.reason = (
                    "metrics-server is not installed. CPU and memory usage "
                    "will be unavailable; install it with "
                    "`kubectl apply -f https://github.com/kubernetes-sigs/"
                    "metrics-server/releases/latest/download/components.yaml`"
                )
            elif exc.status == 403:
                self.reason = (
                    "the agent's role is not permitted to read metrics.k8s.io"
                )
            else:
                self.reason = f"metrics.k8s.io returned HTTP {exc.status}"
            log.warning("Metrics unavailable: %s", self.reason)
            return {}, False
        except Exception as exc:
            self.available = False
            self.reason = f"metrics collection failed: {exc}"
            log.warning("Metrics unavailable: %s", self.reason)
            return {}, False

        usage: dict[str, dict] = {}
        for item in raw.get("items", []):
            meta = item.get("metadata", {})
            key = f"{meta.get('namespace')}/{meta.get('name')}"
            cpu = 0.0
            memory = 0
            for container in item.get("containers", []):
                container_usage = container.get("usage", {})
                cpu += parse_cpu(container_usage.get("cpu")) or 0.0
                memory += parse_memory(container_usage.get("memory")) or 0
            usage[key] = {"cpu_cores": cpu, "memory_bytes": memory}

        self.available = True
        self.reason = None
        return usage, True


def aggregate_to_workloads(
    pod_usage: dict, pods: list[dict], workloads: list[dict]
) -> None:
    """
    Roll pod-level usage up to the workload that owns it, in place.

    Pods are matched to workloads by label selector rather than by owner
    reference chain. A Deployment owns a ReplicaSet which owns the Pod, so
    following ownerReferences would need a second API call per pod purely to
    resolve the intermediate object.
    """
    by_namespace: dict[str, list[dict]] = defaultdict(list)
    for pod in pods:
        by_namespace[pod["namespace"]].append(pod)

    for workload in workloads:
        selector = workload.get("pod_labels") or {}
        if not selector:
            workload["cpu_cores_used"] = None
            workload["memory_bytes_used"] = None
            workload["pods_measured"] = 0
            continue

        cpu_total = 0.0
        mem_total = 0
        measured = 0
        for pod in by_namespace.get(workload["namespace"], []):
            labels = pod.get("labels") or {}
            if not all(labels.get(k) == v for k, v in selector.items()):
                continue
            entry = pod_usage.get(f"{pod['namespace']}/{pod['name']}")
            if entry is None:
                continue
            cpu_total += entry["cpu_cores"]
            mem_total += entry["memory_bytes"]
            measured += 1

        # None, not zero: no measurement is a different fact from measured
        # zero, and only one of them justifies a downsizing recommendation.
        workload["cpu_cores_used"] = cpu_total if measured else None
        workload["memory_bytes_used"] = mem_total if measured else None
        workload["pods_measured"] = measured
