"""
Agent entrypoint.

    python -m cloudoptimizer_agent

Loop: collect the cluster, build a snapshot, POST it, sleep, repeat. Each
cycle is independent -- a failed cycle affects only that snapshot, and the
next one starts from a fresh read rather than from patched-up state.
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from kubernetes import config as k8s_config

from .buffer import SnapshotBuffer
from .collector import ClusterCollector, attach_service_references
from .config import AgentConfig
from .metrics import MetricsCollector, aggregate_to_workloads
from .snapshot import assert_no_secrets, build_snapshot, serialize
from .transport import IngestError, Transport
from .version import AGENT_VERSION

log = logging.getLogger("cloudoptimizer_agent")

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    log.info("Received signal %s, finishing current cycle then exiting", signum)
    _shutdown = True


def _load_kube_config() -> str:
    """
    In-cluster config when running as a pod, kubeconfig when running locally.

    Local execution matters: an operator evaluating the agent should be able
    to run it against their own kubeconfig before granting it a ServiceAccount.
    """
    try:
        k8s_config.load_incluster_config()
        return "in-cluster"
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        return "kubeconfig"


def run_cycle(
    collector: ClusterCollector,
    metrics: MetricsCollector,
    transport: Transport | None,
    seq: int,
    collect_metrics: bool,
    buffer: SnapshotBuffer | None = None,
) -> dict:
    nodes = collector.collect_nodes()
    services = collector.collect_services()
    workloads = collector.collect_workloads()
    pods = collector.collect_pods()
    ingresses = collector.collect_ingresses()
    network_policies = collector.collect_network_policies()
    disruption_budgets = collector.collect_disruption_budgets()
    autoscalers = collector.collect_autoscalers()

    # Resolve env references into service references, then drop the values.
    attach_service_references(workloads, services)

    metrics_available = False
    metrics_reason = "metrics collection disabled by configuration"
    if collect_metrics:
        pod_usage, metrics_available = metrics.collect_pod_metrics()
        metrics_reason = metrics.reason
        aggregate_to_workloads(pod_usage, pods, workloads)
    else:
        for workload in workloads:
            workload["cpu_cores_used"] = None
            workload["memory_bytes_used"] = None
            workload["pods_measured"] = 0

    snapshot = build_snapshot(
        seq=seq,
        cluster_uid=collector.cluster_fingerprint(),
        provider=collector.detect_provider(nodes),
        kubernetes_version=collector.kubernetes_version(),
        nodes=nodes,
        workloads=workloads,
        services=services,
        pods=pods,
        ingresses=ingresses,
        network_policies=network_policies,
        disruption_budgets=disruption_budgets,
        autoscalers=autoscalers,
        metrics_available=metrics_available,
        metrics_reason=metrics_reason,
    )

    payload = serialize(snapshot)
    assert_no_secrets(payload)

    summary = snapshot["summary"]
    log.info(
        "Collected seq=%d: %d nodes, %d workloads, %d services, %d pods, "
        "%d edges (%s), %.1f KiB, metrics=%s",
        seq,
        summary["nodes"],
        summary["workloads"],
        summary["services"],
        summary["pods"],
        summary["edges"]["total"],
        summary["edges"]["by_source"],
        len(payload) / 1024,
        "yes" if metrics_available else "no",
    )

    if transport is not None:
        # Clear any backlog first, so history is replayed in the order it was
        # observed rather than interleaved with the current snapshot.
        if buffer is not None and len(buffer):
            buffer.drain(transport.send_snapshot)

        try:
            response = transport.send_snapshot(payload)
        except IngestError as exc:
            # Fatal errors (bad key, duplicate cluster) will not resolve by
            # retrying later, so buffering them would fill the buffer with
            # snapshots that can never be delivered.
            if buffer is None or exc.fatal:
                raise
            buffer.add(payload)
            log.warning(
                "Ingest unavailable; buffered snapshot seq=%d "
                "(%d queued, %.1f KiB): %s",
                seq, len(buffer), buffer.nbytes / 1024, exc,
            )
            return {}

        status = response.get("status", "unknown")
        cluster_id = response.get("cluster_id", "?")

        if status == "accepted":
            log.info(
                "Ingested seq=%d: cluster=%s%s",
                seq,
                cluster_id,
                f", next interval {response['next_interval_seconds']}s"
                if response.get("next_interval_seconds")
                else "",
            )
        else:
            # Anything other than "accepted" means the server did not store
            # this snapshot. Logging it as success hid a bug in which every
            # snapshot was silently discarded while the agent reported
            # healthy -- never let a non-accepted status look like an ingest.
            log.warning(
                "Snapshot seq=%d NOT stored (status=%s, cluster=%s). "
                "The server accepted the request but did not record the "
                "snapshot.",
                seq, status, cluster_id,
            )
        return response
    return {}


def main() -> int:
    config = AgentConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # --dry-run collects and prints without transmitting. The first thing a
    # security-conscious operator will want is to see exactly what would be
    # sent before any of it leaves the cluster.
    dry_run = "--dry-run" in sys.argv

    log.info("CloudOptimizer agent %s starting", AGENT_VERSION)

    problems = config.validate()
    if problems and not dry_run:
        for problem in problems:
            log.error("%s", problem)
        return 2
    for problem in problems:
        log.warning("%s", problem)

    source = _load_kube_config()
    log.info("Kubernetes credentials loaded from %s", source)

    collector = ClusterCollector(config)
    metrics = MetricsCollector()
    transport = (
        None
        if dry_run
        else Transport(
            config.endpoint,
            config.api_key,
            verify_tls=config.verify_tls,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retries,
        )
    )
    if dry_run:
        log.warning("Dry run: snapshots will be collected but NOT transmitted")

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    buffer = SnapshotBuffer()

    seq = 0
    interval = config.interval_seconds

    while not _shutdown:
        seq += 1
        try:
            response = run_cycle(
                collector, metrics, transport, seq, config.collect_metrics,
                buffer,
            )
            # The server controls cadence, so it can back a noisy fleet off
            # without anyone editing a Helm value.
            requested = response.get("next_interval_seconds")
            if isinstance(requested, int) and requested >= 10:
                interval = requested
        except IngestError as exc:
            if exc.fatal:
                log.error("Fatal ingest error (HTTP %s): %s", exc.status, exc)
                log.error("Not retrying. Check the API key and endpoint.")
                return 3
            log.error("Ingest failed after retries: %s", exc)
        except Exception:
            # One bad cycle must not kill the pod: a transient API hiccup
            # should not require a restart to recover from.
            log.exception("Collection cycle failed; continuing")

        if config.once or dry_run:
            break

        for _ in range(interval):
            if _shutdown:
                break
            time.sleep(1)

    log.info("Agent stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
