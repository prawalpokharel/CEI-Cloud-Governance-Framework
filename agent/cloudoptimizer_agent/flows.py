"""
Hubble flow collection.

Reads observed network flows from Cilium's Hubble and reduces them to egress
edges. This is the data the Kubernetes API cannot provide: dependencies
declared in a manifest are intentions, flows are what actually happened.

## Why this is separate from the agent's normal collection

Flows arrive continuously and in volume — a busy cluster produces thousands
per second. The agent samples a bounded window rather than streaming, because
the question being answered ("which external destinations does this workload
talk to") is a set-membership question, not a traffic-accounting one. A
90-second window catches the destinations a workload contacts regularly and
misses the once-a-day batch job, which is the honest trade for not shipping a
flow-log pipeline.

## Requires Cilium with Hubble

Every function degrades to "unavailable" without it rather than failing.
Clusters running kindnet, Calico without eBPF, or a managed CNI produce no
flows, and the egress analysis is simply absent for them.

## VALIDATION STATUS

The Hubble flow schema this parses is stable and documented, and the parsing
is covered by tests using realistic records. It has **not** been validated
against a live Hubble instance: Cilium's datapath requires tc/clsact qdisc
support, which Docker Desktop's linuxkit kernel does not provide, so no local
Kubernetes cluster on this machine can run it. Validate on a real Linux
cluster before relying on the output.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections import defaultdict
from typing import Any, Iterable

log = logging.getLogger(__name__)

# Cilium reserved identities. 2 is "world" — anything outside the cluster,
# which is exactly what egress analysis is about.
IDENTITY_WORLD = 2
IDENTITY_HOST = 1
IDENTITY_REMOTE_NODE = 6
IDENTITY_KUBE_APISERVER = 7

# Reserved-identity labels, checked alongside the numeric identity because
# Hubble populates one or the other depending on version and flow type.
WORLD_LABELS = {"reserved:world", "reserved:unmanaged"}

DEFAULT_WINDOW_SECONDS = 90
DEFAULT_FLOW_LIMIT = 20000


class HubbleUnavailable(Exception):
    """Hubble is not present or not reachable."""


def hubble_binary() -> str | None:
    return os.environ.get("HUBBLE_PATH") or shutil.which("hubble")


def available() -> bool:
    return hubble_binary() is not None


def collect_flows(
    *,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    limit: int = DEFAULT_FLOW_LIMIT,
    server: str | None = None,
) -> list[dict]:
    """
    Read a bounded window of flows from Hubble Relay as JSON records.

    `hubble observe` is invoked rather than the gRPC API deliberately: the CLI
    is a supported interface with a stable output contract, and shelling out
    to a proven binary is the same pattern the image scanner uses for Trivy.
    Speaking gRPC would mean vendoring Cilium's protobufs and tracking their
    schema across releases.
    """
    binary = hubble_binary()
    if not binary:
        raise HubbleUnavailable(
            "hubble CLI not found. Egress analysis requires Cilium with "
            "Hubble; set HUBBLE_PATH if it is installed elsewhere."
        )

    command = [
        binary, "observe",
        "--output", "jsonpb",
        "--last", str(limit),
        "--since", f"{window_seconds}s",
    ]
    if server:
        command += ["--server", server]

    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=window_seconds + 60
        )
    except subprocess.TimeoutExpired as exc:
        raise HubbleUnavailable("hubble observe timed out") from exc
    except Exception as exc:
        raise HubbleUnavailable(f"hubble observe failed: {exc}") from exc

    if proc.returncode != 0:
        raise HubbleUnavailable(
            f"hubble observe failed: {(proc.stderr or '').strip()[:300]}"
        )

    return parse_flow_stream(proc.stdout.splitlines())


def parse_flow_stream(lines: Iterable[str]) -> list[dict]:
    """
    Parse newline-delimited JSON flow records.

    Malformed lines are skipped rather than aborting the batch. Hubble can
    interleave status messages with flow records, and losing a whole window
    because one line was not a flow would be a poor trade.
    """
    flows = []
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        flow = record.get("flow") or record
        if isinstance(flow, dict) and flow.get("source") is not None:
            flows.append(flow)
    return flows


def _workload_key(endpoint: dict) -> str | None:
    """
    Derive a workload key from a Hubble endpoint.

    Hubble reports the pod name, so the generated suffixes are stripped back
    to the Deployment name that every other part of the system keys on. A
    pod named `api-7d9f8b5c4-x2k9p` belongs to Deployment `api`.
    """
    namespace = endpoint.get("namespace")
    pod = endpoint.get("pod_name") or endpoint.get("podName")
    if not namespace or not pod:
        return None

    parts = pod.split("-")
    # Deployment pods end in <replicaset-hash>-<pod-suffix>; StatefulSet pods
    # end in a plain ordinal. Both are stripped conservatively: a name that
    # does not match the shape is left alone rather than truncated wrongly.
    if len(parts) >= 3 and len(parts[-1]) == 5 and len(parts[-2]) >= 5:
        name = "-".join(parts[:-2])
        kind = "Deployment"
    elif len(parts) >= 2 and parts[-1].isdigit():
        name = "-".join(parts[:-1])
        kind = "StatefulSet"
    else:
        name, kind = pod, "Pod"
    return f"{namespace}/{kind}/{name}"


def _is_external(destination: dict) -> bool:
    identity = destination.get("identity")
    if identity == IDENTITY_WORLD:
        return True
    labels = {str(l).lower() for l in (destination.get("labels") or [])}
    if labels & WORLD_LABELS:
        return True
    # An endpoint with no namespace and no pod is not a cluster workload.
    if identity in (IDENTITY_HOST, IDENTITY_REMOTE_NODE, IDENTITY_KUBE_APISERVER):
        return False
    return not destination.get("namespace") and not destination.get("pod_name")


def summarize_egress(flows: list[dict]) -> dict[str, Any]:
    """
    Reduce raw flows to per-workload external destinations.

    Only egress to destinations outside the cluster is retained. In-cluster
    flows are already covered by the dependency graph, and including them
    here would duplicate that with a noisier source.
    """
    by_workload: dict[str, dict[str, dict]] = defaultdict(dict)
    dropped: dict[str, int] = defaultdict(int)
    total = external = 0

    for flow in flows:
        total += 1
        direction = str(flow.get("traffic_direction") or flow.get("trafficDirection") or "")
        if direction.upper() != "EGRESS":
            continue

        destination = flow.get("destination") or {}
        if not _is_external(destination):
            continue
        external += 1

        source_key = _workload_key(flow.get("source") or {})
        if not source_key:
            continue

        ip_block = flow.get("IP") or flow.get("ip") or {}
        dest_ip = ip_block.get("destination") or ""
        names = flow.get("destination_names") or flow.get("destinationNames") or []
        # Prefer a DNS name: an IP tells an operator nothing, and cloud
        # provider IPs change under the same name.
        label = (names[0] if names else dest_ip) or "unknown"

        l4 = flow.get("l4") or {}
        port = None
        for proto in ("TCP", "UDP"):
            if proto in l4:
                port = (l4[proto] or {}).get("destination_port") or (l4[proto] or {}).get("destinationPort")
                break

        verdict = str(flow.get("verdict") or "").upper()
        if verdict == "DROPPED":
            dropped[source_key] += 1

        entry = by_workload[source_key].setdefault(
            label,
            {
                "destination": label,
                "ips": set(),
                "ports": set(),
                "flow_count": 0,
                "dropped_count": 0,
                "dns_resolved": bool(names),
            },
        )
        entry["flow_count"] += 1
        if dest_ip:
            entry["ips"].add(dest_ip)
        if port:
            entry["ports"].add(int(port))
        if verdict == "DROPPED":
            entry["dropped_count"] += 1

    workloads = {}
    for key, destinations in by_workload.items():
        workloads[key] = sorted(
            (
                {
                    **entry,
                    "ips": sorted(entry["ips"])[:5],
                    "ports": sorted(entry["ports"]),
                }
                for entry in destinations.values()
            ),
            key=lambda d: -d["flow_count"],
        )

    return {
        "flows_examined": total,
        "external_egress_flows": external,
        "workloads": workloads,
        "dropped_by_workload": dict(dropped),
    }


def collect_egress_summary(
    *, window_seconds: int = DEFAULT_WINDOW_SECONDS, server: str | None = None
) -> dict[str, Any]:
    """Collect and summarize, returning an availability marker on failure."""
    if not available():
        return {
            "available": False,
            "reason": (
                "Hubble is not present. Egress analysis requires Cilium with "
                "Hubble enabled; without it, dependencies are known only from "
                "declarations."
            ),
            "workloads": {},
        }
    try:
        flows = collect_flows(window_seconds=window_seconds, server=server)
    except HubbleUnavailable as exc:
        return {"available": False, "reason": str(exc), "workloads": {}}

    summary = summarize_egress(flows)
    summary["available"] = True
    summary["window_seconds"] = window_seconds
    return summary
