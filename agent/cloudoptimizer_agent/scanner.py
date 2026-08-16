"""
Container image vulnerability scanner.

Runs as a separate, opt-in CronJob rather than inside the agent. Three
reasons, in order of importance:

1. **Trust.** The agent's claim is that it is read-only, tiny, and cannot
   reach anything sensitive. A scanner needs to pull images, which means
   registry credentials, which means a materially larger blast radius. Fusing
   them would spend the agent's trust story to save a deployment.
2. **Footprint.** Trivy plus its vulnerability database is hundreds of
   megabytes. The agent is ~200 MB total and holds a steady ~50 MiB.
3. **Cadence.** Topology changes by the minute; a base image's CVE list
   changes by the day. Scanning every 60s would burn CPU and registry
   bandwidth for nothing.

Images are pulled and scanned **inside the customer's cluster**, using the
registry access that cluster already has. Only findings leave. Registry
credentials never do.

    python -m cloudoptimizer_agent.scanner
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from kubernetes import client, config as k8s_config

from .config import AgentConfig
from .transport import IngestError, Transport
from .version import AGENT_VERSION

log = logging.getLogger("cloudoptimizer_agent.scanner")

SCAN_SCHEMA_VERSION = 1

# Severities worth transmitting. LOW and UNKNOWN findings on a base image
# number in the hundreds, are almost never actioned, and would dominate the
# payload -- the counts are still reported so nothing is hidden.
REPORTED_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}

# Per-image ceiling. A stale base image can carry 300+ findings; past this
# the list stops being a work queue. Highest severity first, so the cap drops
# the least important.
MAX_VULNS_PER_IMAGE = 100


def _trivy_binary() -> str:
    binary = os.environ.get("TRIVY_PATH") or shutil.which("trivy")
    if not binary:
        raise RuntimeError(
            "trivy not found. The scanner image bundles it; set TRIVY_PATH if "
            "running outside that image."
        )
    return binary


def discover_images(core: client.CoreV1Api, config: AgentConfig) -> dict[str, list[str]]:
    """
    Map image reference -> the workloads running it.

    Read from running pods rather than from workload specs, because a pod's
    status carries the image actually running. A Deployment specifying
    `app:latest` tells you nothing about which build is live; the pod status
    resolves it.
    """
    images: dict[str, set[str]] = {}
    for pod in core.list_pod_for_all_namespaces().items:
        namespace = pod.metadata.namespace
        if not config.wants_namespace(namespace):
            continue

        owner = (pod.metadata.owner_references or [None])[0]
        owner_kind = getattr(owner, "kind", None)
        owner_name = getattr(owner, "name", None)
        # Pods are owned by ReplicaSets; strip the generated suffix to get
        # back to the Deployment name the rest of the system keys on.
        if owner_kind == "ReplicaSet" and owner_name:
            owner_kind, owner_name = "Deployment", owner_name.rsplit("-", 1)[0]
        workload_key = (
            f"{namespace}/{owner_kind}/{owner_name}"
            if owner_kind and owner_name
            else f"{namespace}/Pod/{pod.metadata.name}"
        )

        statuses = list(pod.status.container_statuses or [])
        specs = list(pod.spec.containers or [])
        for i, spec in enumerate(specs):
            # Prefer the resolved image from status; fall back to the spec for
            # a pod that has not started yet.
            reference = spec.image
            if i < len(statuses) and statuses[i].image:
                reference = statuses[i].image
            if reference:
                images.setdefault(reference, set()).add(workload_key)

    return {ref: sorted(keys) for ref, keys in images.items()}


def scan_image(reference: str, timeout: int = 600) -> dict:
    """Scan one image, returning a normalized result."""
    log.info("Scanning %s", reference)
    try:
        proc = subprocess.run(
            [
                _trivy_binary(), "image",
                "--format", "json",
                "--scanners", "vuln",
                "--quiet",
                "--timeout", f"{timeout}s",
                reference,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
    except subprocess.TimeoutExpired:
        return {"reference": reference, "scan_error": "scan timed out"}
    except Exception as exc:
        return {"reference": reference, "scan_error": str(exc)}

    if proc.returncode != 0:
        # A failed scan is recorded, never treated as a clean bill of health.
        # Private registries, rate limits, and deleted tags all land here, and
        # reporting zero vulnerabilities for them would be a lie.
        return {
            "reference": reference,
            "scan_error": (proc.stderr or "trivy failed").strip()[:500],
        }

    try:
        report = json.loads(proc.stdout)
    except Exception as exc:
        return {"reference": reference, "scan_error": f"unparseable output: {exc}"}

    return normalize_report(reference, report)


def normalize_report(reference: str, report: dict) -> dict:
    """Reduce a Trivy report to what the server needs."""
    metadata = report.get("Metadata") or {}
    os_info = metadata.get("OS") or {}
    digests = metadata.get("RepoDigests") or []

    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    fixable = 0
    findings = []

    for result in report.get("Results") or []:
        pkg_class = result.get("Class")
        for vuln in result.get("Vulnerabilities") or []:
            severity = (vuln.get("Severity") or "UNKNOWN").upper()
            counts[severity] = counts.get(severity, 0) + 1
            fixed_version = vuln.get("FixedVersion")
            if fixed_version:
                fixable += 1
            if severity not in REPORTED_SEVERITIES:
                continue

            findings.append({
                "id": vuln.get("VulnerabilityID"),
                "severity": severity,
                "cvss_score": _best_cvss(vuln.get("CVSS") or {}),
                "pkg_name": vuln.get("PkgName"),
                "installed_version": vuln.get("InstalledVersion"),
                "fixed_version": fixed_version,
                "pkg_class": pkg_class,
                "title": (vuln.get("Title") or "")[:500] or None,
                "primary_url": vuln.get("PrimaryURL"),
            })

    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    findings.sort(
        key=lambda f: (order.get(f["severity"], 9), -(f["cvss_score"] or 0.0))
    )
    truncated = max(0, len(findings) - MAX_VULNS_PER_IMAGE)

    return {
        "reference": reference,
        "digest": digests[0].split("@")[-1] if digests else None,
        "os_family": os_info.get("Family"),
        "os_name": os_info.get("Name"),
        "counts": counts,
        "fixable_count": fixable,
        "vulnerabilities": findings[:MAX_VULNS_PER_IMAGE],
        "truncated": truncated,
        "scan_error": None,
    }


def _best_cvss(cvss: dict) -> float | None:
    """
    Highest V3 score across scoring sources.

    Sources disagree -- NVD and a distro's security team routinely differ by
    several points on the same CVE. Taking the maximum is the conservative
    reading, which is the right default when the output is a security queue.
    """
    scores = [
        source.get("V3Score")
        for source in cvss.values()
        if isinstance(source, dict) and source.get("V3Score") is not None
    ]
    return max(scores) if scores else None


def run_scan(config: AgentConfig, transport: Transport | None) -> dict:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()

    core = client.CoreV1Api()
    namespace_uid = core.read_namespace(name="kube-system").metadata.uid

    images = discover_images(core, config)
    log.info("Found %d distinct image(s) to scan", len(images))

    results = []
    for reference, workload_keys in sorted(images.items()):
        result = scan_image(reference)
        result["workload_keys"] = workload_keys
        results.append(result)
        if result.get("scan_error"):
            log.warning("Scan failed for %s: %s", reference, result["scan_error"])
        else:
            counts = result["counts"]
            log.info(
                "  %s: %d critical, %d high, %d medium (%d fixable)",
                reference, counts["CRITICAL"], counts["HIGH"],
                counts["MEDIUM"], result["fixable_count"],
            )

    payload = {
        "schema_version": SCAN_SCHEMA_VERSION,
        "scanner_version": AGENT_VERSION,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "cluster": {"uid": namespace_uid},
        "images": results,
    }

    if transport is not None:
        transport.send_scan(json.dumps(payload, separators=(",", ":")).encode())
        log.info("Submitted %d image scan result(s)", len(results))
    return payload


def main() -> int:
    config = AgentConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    dry_run = "--dry-run" in sys.argv

    log.info("CloudOptimizer scanner %s starting", AGENT_VERSION)
    problems = config.validate()
    if problems and not dry_run:
        for problem in problems:
            log.error("%s", problem)
        return 2

    transport = (
        None
        if dry_run
        else Transport(
            config.endpoint,
            config.api_key,
            verify_tls=config.verify_tls,
            timeout=max(120, config.request_timeout_seconds),
            max_retries=config.max_retries,
        )
    )
    if dry_run:
        log.warning("Dry run: images will be scanned but results NOT transmitted")

    try:
        payload = run_scan(config, transport)
    except IngestError as exc:
        log.error("Scan submission failed: %s", exc)
        return 3
    except Exception:
        log.exception("Scan failed")
        return 1

    if dry_run:
        print(json.dumps(payload, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------------------------------------
# IaC misconfiguration scanning (Phase 5)
# --------------------------------------------------------------------------

# Trivy's config scanner covers Terraform, CloudFormation, Kubernetes
# manifests, Helm charts, and Dockerfiles with one engine. Adding a second
# tool for each format would multiply maintenance for findings that largely
# overlap.
IAC_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}


def scan_iac(path: str, timeout: int = 600) -> dict:
    """
    Scan a directory of infrastructure-as-code for misconfigurations.

    Runs against a checked-out repository, not against the cluster: the point
    is to catch a misconfiguration in the manifest that produced the cluster,
    where fixing it is a pull request rather than a live change.
    """
    log.info("Scanning IaC at %s", path)
    try:
        proc = subprocess.run(
            [
                _trivy_binary(), "config",
                "--format", "json",
                "--quiet",
                "--timeout", f"{timeout}s",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
    except subprocess.TimeoutExpired:
        return {"path": path, "scan_error": "scan timed out"}
    except Exception as exc:
        return {"path": path, "scan_error": str(exc)}

    # trivy config exits non-zero when findings exist, which is not an error.
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        return {
            "path": path,
            "scan_error": (proc.stderr or "trivy config failed").strip()[:500],
        }

    try:
        report = json.loads(proc.stdout)
    except Exception as exc:
        return {"path": path, "scan_error": f"unparseable output: {exc}"}

    return normalize_iac_report(path, report)


def normalize_iac_report(path: str, report: dict) -> dict:
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "UNKNOWN": 0}
    findings = []

    for result in report.get("Results") or []:
        target = result.get("Target")
        for issue in result.get("Misconfigurations") or []:
            severity = (issue.get("Severity") or "UNKNOWN").upper()
            counts[severity] = counts.get(severity, 0) + 1
            if severity not in IAC_SEVERITIES:
                continue
            # Only failures. Trivy reports passing checks too, and shipping
            # those would bury the failures they are meant to contrast with.
            if (issue.get("Status") or "").upper() == "PASS":
                continue

            findings.append({
                "id": issue.get("ID"),
                "severity": severity,
                "title": issue.get("Title"),
                "description": (issue.get("Description") or "")[:600] or None,
                "resolution": (issue.get("Resolution") or "")[:600] or None,
                "target": target,
                "start_line": (issue.get("CauseMetadata") or {}).get("StartLine"),
                "resource": (issue.get("CauseMetadata") or {}).get("Resource"),
                "primary_url": issue.get("PrimaryURL"),
                "service": (issue.get("CauseMetadata") or {}).get("Service"),
            })

    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}
    findings.sort(key=lambda f: order.get(f["severity"], 9))

    return {
        "path": path,
        "counts": counts,
        "findings": findings[:200],
        "truncated": max(0, len(findings) - 200),
        "scan_error": None,
    }
