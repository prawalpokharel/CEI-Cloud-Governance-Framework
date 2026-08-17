"""
Egress analysis (Phase 6).

Answers "which workloads talk to unexpected places outside the cluster", using
observed flows rather than declarations.

## Why this needs flow data at all

Everything else in this product is derived from the Kubernetes API, which
describes intent: a Deployment declares an image, a Service declares a
selector, an env var names a dependency. None of that says whether a pod is
posting to an address nobody intended.

Flow data is the only source that does, which is why this is the one analysis
gated on a specific CNI.

## What counts as unexpected

Not "on a blocklist" — a blocklist only catches destinations someone already
thought of. Three signals that need no prior knowledge:

* **Unresolved IPs.** A workload contacting a bare IP with no DNS name behind
  it is either using hardcoded infrastructure or exfiltrating; both deserve a
  look.
* **Rarity.** A destination that one workload contacts and no other does is
  more interesting than a shared dependency every service uses. Package
  registries and telemetry endpoints appear everywhere; a single pod talking
  somewhere unique does not.
* **Dropped flows.** Traffic a NetworkPolicy already denied is either a
  misconfiguration or something trying to get out.

Ranked by CEI, so an unexpected destination from a workload half the cluster
depends on outranks the same destination from a scratch pod.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Destinations that appear in essentially every cluster. Present so ordinary
# platform traffic does not dominate a list meant to surface the unusual --
# not a security allowlist, and never used to suppress a dropped flow.
COMMON_EGRESS_SUFFIXES = (
    ".amazonaws.com", ".azure.com", ".googleapis.com", ".windows.net",
    "docker.io", ".docker.com", "ghcr.io", "quay.io", "gcr.io", ".pkg.dev",
    "pypi.org", ".pythonhosted.org", "registry.npmjs.org", "rubygems.org",
    "github.com", ".githubusercontent.com", "gitlab.com",
    "datadoghq.com", ".sentry.io", ".newrelic.com", ".grafana.net",
)

# Ports where egress to the open internet is worth a second look regardless
# of destination.
NOTABLE_EGRESS_PORTS = {
    22: "SSH",
    23: "Telnet",
    25: "SMTP",
    3389: "RDP",
    445: "SMB",
    1433: "MSSQL",
    3306: "MySQL",
    5432: "PostgreSQL",
    6379: "Redis",
    27017: "MongoDB",
    9001: "non-standard",
    4444: "commonly used by tooling for reverse shells",
}


@dataclass
class EgressFinding:
    kind: str
    severity: str
    workload_key: str
    destination: str
    title: str
    detail: str
    cei_score: float | None = None
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "workload_key": self.workload_key,
            "destination": self.destination,
            "title": self.title,
            "detail": self.detail,
            "cei_score": self.cei_score,
            "evidence": self.evidence,
        }


def _is_common(destination: str) -> bool:
    lowered = destination.lower().rstrip(".")
    return any(
        lowered.endswith(suffix) or lowered == suffix.lstrip(".")
        for suffix in COMMON_EGRESS_SUFFIXES
    )


def _looks_like_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return True
    return ":" in value and not value.replace(":", "").isalpha()


def analyze_egress(
    summary: dict,
    cei_by_workload: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """
    Turn a Hubble egress summary into ranked findings.

    ``summary`` is the output of the agent's flows.summarize_egress.
    """
    cei_by_workload = cei_by_workload or {}

    if not summary.get("available", False):
        return {
            "available": False,
            "reason": summary.get(
                "reason", "No flow data. Egress analysis requires Cilium with Hubble."
            ),
            "summary": {"total": 0},
            "findings": [],
        }

    workloads: dict[str, list[dict]] = summary.get("workloads") or {}

    # How many distinct workloads reach each destination. A destination one
    # workload uses is more interesting than one everything uses.
    reach_count: dict[str, int] = {}
    for destinations in workloads.values():
        for entry in destinations:
            reach_count[entry["destination"]] = reach_count.get(entry["destination"], 0) + 1

    findings: list[EgressFinding] = []

    for workload_key, destinations in workloads.items():
        cei = (cei_by_workload.get(workload_key) or {}).get("cei_score")

        for entry in destinations:
            destination = entry["destination"]
            ports = entry.get("ports") or []
            dropped = entry.get("dropped_count", 0)
            resolved = entry.get("dns_resolved", False)

            if dropped:
                findings.append(EgressFinding(
                    kind="egress_denied",
                    severity="warning",
                    workload_key=workload_key,
                    destination=destination,
                    title=f"Traffic to {destination} is being blocked",
                    detail=(
                        f"{dropped} flow(s) to this destination were denied by "
                        "policy. Either the workload needs access it does not "
                        "have, or something is attempting a connection it "
                        "should not."
                    ),
                    cei_score=cei,
                    evidence={"dropped": dropped, "ports": ports},
                ))

            notable = [NOTABLE_EGRESS_PORTS[p] for p in ports if p in NOTABLE_EGRESS_PORTS]
            if notable:
                findings.append(EgressFinding(
                    kind="egress_notable_port",
                    severity="critical" if not _is_common(destination) else "warning",
                    workload_key=workload_key,
                    destination=destination,
                    title=(
                        f"Outbound {', '.join(sorted(set(notable)))} to {destination}"
                    ),
                    detail=(
                        "Administrative and database protocols leaving the "
                        "cluster are rarely intentional. Confirm this is a "
                        "managed service the workload is supposed to reach."
                    ),
                    cei_score=cei,
                    evidence={"ports": ports, "flows": entry.get("flow_count")},
                ))

            if not resolved and _looks_like_ip(destination):
                findings.append(EgressFinding(
                    kind="egress_unresolved_ip",
                    severity="warning",
                    workload_key=workload_key,
                    destination=destination,
                    title=f"Traffic to a bare IP {destination}",
                    detail=(
                        "No DNS name was observed for this destination. That "
                        "means a hardcoded address — which breaks silently "
                        "when the provider changes it — or traffic "
                        "deliberately avoiding name resolution."
                    ),
                    cei_score=cei,
                    evidence={"ips": entry.get("ips"), "ports": ports},
                ))
                continue

            if (
                not _is_common(destination)
                and reach_count.get(destination, 0) == 1
                and resolved
            ):
                findings.append(EgressFinding(
                    kind="egress_unique_destination",
                    severity="info",
                    workload_key=workload_key,
                    destination=destination,
                    title=f"Only {workload_key.split('/')[-1]} contacts {destination}",
                    detail=(
                        "No other workload in the cluster reaches this "
                        "destination, and it is not a well-known registry or "
                        "cloud endpoint. Worth confirming it is intended."
                    ),
                    cei_score=cei,
                    evidence={
                        "flows": entry.get("flow_count"),
                        "ports": ports,
                        "reached_by_workloads": 1,
                    },
                ))

    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(
        key=lambda f: (order.get(f.severity, 3), -(f.cei_score or 0.0), f.destination)
    )

    severities: dict[str, int] = {}
    for finding in findings:
        severities[finding.severity] = severities.get(finding.severity, 0) + 1

    return {
        "available": True,
        "summary": {
            "total": len(findings),
            "by_severity": severities,
            "workloads_with_egress": len(workloads),
            "distinct_destinations": len(reach_count),
            "flows_examined": summary.get("flows_examined", 0),
            "window_seconds": summary.get("window_seconds"),
            "note": (
                "Derived from flows observed in a bounded window. A workload "
                "that contacts a destination less often than the window is "
                "wide will not appear."
            ),
        },
        "findings": [f.to_dict() for f in findings],
    }
