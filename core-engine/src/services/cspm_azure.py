"""
Azure cloud posture checks (Phase 5).

Covers the misconfigurations that show up in real breach post-mortems:
publicly readable storage, security groups open to the internet, and standing
subscription-wide administrative access.

## Authentication

`DefaultAzureCredential`, which walks a chain of sources and uses whichever
answers first. In development that is the operator's `az login`, so nothing
needs a client secret to try this out — an evaluator who already has the Azure
CLI configured can run it immediately.

In production the same class picks up a managed identity or workload identity
with no code change. Requiring a client secret for local development would
mean either creating a service principal before you can see anything work, or
committing one somewhere it should not be.

Only `AZURE_SUBSCRIPTION_ID` is required.

## Read-only

Every client here is used for `list` and `get`. Reader on the subscription is
sufficient. Nothing is created, modified, or deleted, and no check needs a
permission beyond reading configuration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Ports where "open to the entire internet" is nearly always a finding rather
# than a design decision. HTTP/HTTPS are deliberately absent: a public web
# server is the point of a public web server.
SENSITIVE_PORTS = {
    22: "SSH",
    3389: "RDP",
    3306: "MySQL",
    5432: "PostgreSQL",
    1433: "MSSQL",
    27017: "MongoDB",
    6379: "Redis",
    9200: "Elasticsearch",
    2379: "etcd",
    5984: "CouchDB",
    11211: "Memcached",
}

# Source specifiers meaning "anywhere on the internet".
INTERNET_SOURCES = {"*", "0.0.0.0/0", "internet", "any", "::/0"}

# Roles that carry write access across an entire subscription.
PRIVILEGED_ROLES = {
    "Owner": "critical",
    "Contributor": "warning",
    "User Access Administrator": "critical",
}


class AzureUnavailable(Exception):
    """Not configured, or the credential chain produced nothing."""


@dataclass
class Finding:
    check: str
    severity: str
    resource: str
    resource_type: str
    title: str
    detail: str
    remediation: str
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "severity": self.severity,
            "resource": self.resource,
            "resource_type": self.resource_type,
            "title": self.title,
            "detail": self.detail,
            "remediation": self.remediation,
            "evidence": self.evidence,
        }


def _credential():
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        raise AzureUnavailable(
            "azure-identity is not installed. Install the cloud extras to "
            "enable Azure posture checks."
        ) from exc
    return DefaultAzureCredential()


def subscription_id() -> str:
    value = os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip()
    if not value:
        raise AzureUnavailable(
            "AZURE_SUBSCRIPTION_ID is not set. Run `az account show --query id "
            "-o tsv` to find it."
        )
    return value


def describe() -> dict[str, Any]:
    """Configuration state, safe to log. Never touches the network."""
    return {
        "subscription_id_present": bool(os.environ.get("AZURE_SUBSCRIPTION_ID")),
        "credential": "DefaultAzureCredential",
        "client_secret_required": False,
        "note": (
            "Uses az login locally and a managed identity in production, with "
            "no code change and no client secret."
        ),
    }


# --------------------------------------------------------------------------
# Checks — each is pure given its input, so they are testable without Azure
# --------------------------------------------------------------------------

def check_storage_account(account: Any) -> list[Finding]:
    """
    Public access, transport security, and TLS floor on one storage account.

    Written against a duck-typed object rather than the SDK model so the
    checks can be exercised against synthetic inputs. The logic is what has to
    be right; the SDK just supplies attributes.
    """
    findings: list[Finding] = []
    name = getattr(account, "name", "?")

    # allow_blob_public_access=None means "not set", which Azure treats as
    # allowed on older accounts. Absent is not the same as disabled, and
    # reading it as disabled would under-report the exact case that matters.
    public = getattr(account, "allow_blob_public_access", None)
    if public is not False:
        findings.append(Finding(
            check="storage_public_blob_access",
            severity="critical" if public is True else "warning",
            resource=name,
            resource_type="Microsoft.Storage/storageAccounts",
            title=f"{name} permits public blob access",
            detail=(
                "Containers in this account can be configured for anonymous "
                "read. Public storage is the single most common source of "
                "accidental data exposure."
                if public is True else
                "allowBlobPublicAccess is unset. Azure treats that as "
                "permitted on accounts created before the default changed, so "
                "unset is not the same as disabled."
            ),
            remediation=(
                f"az storage account update --name {name} "
                "--allow-blob-public-access false"
            ),
            evidence={"allow_blob_public_access": public},
        ))

    if getattr(account, "enable_https_traffic_only", True) is False:
        findings.append(Finding(
            check="storage_http_allowed",
            severity="critical",
            resource=name,
            resource_type="Microsoft.Storage/storageAccounts",
            title=f"{name} accepts unencrypted HTTP",
            detail="Data and access keys can be read in transit.",
            remediation=(
                f"az storage account update --name {name} "
                "--https-only true"
            ),
            evidence={"enable_https_traffic_only": False},
        ))

    tls = str(getattr(account, "minimum_tls_version", "") or "")
    if tls and tls < "TLS1_2":
        findings.append(Finding(
            check="storage_weak_tls",
            severity="warning",
            resource=name,
            resource_type="Microsoft.Storage/storageAccounts",
            title=f"{name} allows {tls.replace('_', '.')}",
            detail="TLS below 1.2 has known weaknesses and fails most audits.",
            remediation=(
                f"az storage account update --name {name} --min-tls-version TLS1_2"
            ),
            evidence={"minimum_tls_version": tls},
        ))

    network = getattr(account, "network_rule_set", None)
    if network is not None and str(getattr(network, "default_action", "")) .endswith("Allow"):
        findings.append(Finding(
            check="storage_no_network_restriction",
            severity="warning",
            resource=name,
            resource_type="Microsoft.Storage/storageAccounts",
            title=f"{name} accepts traffic from any network",
            detail=(
                "The default network rule is Allow, so the account is "
                "reachable from any IP that has a credential."
            ),
            remediation=(
                f"az storage account update --name {name} --default-action Deny"
            ),
            evidence={"default_action": "Allow"},
        ))

    return findings


def _rule_ports(rule: Any) -> list[str]:
    ports = list(getattr(rule, "destination_port_ranges", None) or [])
    single = getattr(rule, "destination_port_range", None)
    if single:
        ports.append(single)
    return [str(p) for p in ports]


def _matches_sensitive(port_spec: str) -> list[tuple[int, str]]:
    """
    Which sensitive ports a port specification covers.

    Handles `22`, `20-30`, and `*`. A wildcard exposes everything, which is
    worse than any single port and must not be skipped just because it does
    not parse as an integer.
    """
    if port_spec.strip() == "*":
        return sorted(SENSITIVE_PORTS.items())
    if "-" in port_spec:
        try:
            low, high = (int(x) for x in port_spec.split("-", 1))
        except ValueError:
            return []
        return sorted((p, n) for p, n in SENSITIVE_PORTS.items() if low <= p <= high)
    try:
        port = int(port_spec)
    except ValueError:
        return []
    return [(port, SENSITIVE_PORTS[port])] if port in SENSITIVE_PORTS else []


def check_network_security_group(nsg: Any) -> list[Finding]:
    """Inbound rules exposing sensitive ports to the internet."""
    findings: list[Finding] = []
    nsg_name = getattr(nsg, "name", "?")

    for rule in (getattr(nsg, "security_rules", None) or []):
        if str(getattr(rule, "direction", "")).lower() != "inbound":
            continue
        if str(getattr(rule, "access", "")).lower() != "allow":
            continue

        sources = [
            str(s).lower()
            for s in (
                list(getattr(rule, "source_address_prefixes", None) or [])
                + ([getattr(rule, "source_address_prefix", None)]
                   if getattr(rule, "source_address_prefix", None) else [])
            )
        ]
        if not any(s in INTERNET_SOURCES for s in sources):
            continue

        exposed: list[tuple[int, str]] = []
        for spec in _rule_ports(rule):
            exposed.extend(_matches_sensitive(spec))
        if not exposed:
            continue

        names = ", ".join(f"{name} ({port})" for port, name in sorted(set(exposed)))
        findings.append(Finding(
            check="nsg_sensitive_port_open",
            severity="critical",
            resource=f"{nsg_name}/{getattr(rule, 'name', '?')}",
            resource_type="Microsoft.Network/networkSecurityGroups",
            title=f"{nsg_name} exposes {names} to the internet",
            detail=(
                "An inbound Allow rule accepts traffic from any source "
                "address on an administrative or database port."
            ),
            remediation=(
                "Restrict the source to known ranges, or front the service "
                "with a bastion or private endpoint."
            ),
            evidence={
                "rule": getattr(rule, "name", None),
                "priority": getattr(rule, "priority", None),
                "ports": _rule_ports(rule),
                "sources": sources,
            },
        ))
    return findings


def check_role_assignments(
    assignments: list[Any], role_names: dict[str, str]
) -> list[Finding]:
    """
    Standing privileged access at subscription scope.

    Scope matters more than the role: Owner on one resource group is a normal
    delegation, Owner on the whole subscription is a blast radius. Only
    subscription-scoped assignments are reported, so ordinary delegation does
    not drown out the finding that matters.
    """
    findings: list[Finding] = []
    by_role: dict[str, list[Any]] = {}

    for assignment in assignments:
        scope = str(getattr(assignment, "scope", "") or "")
        # /subscriptions/<id> exactly — anything longer is narrower scope.
        if scope.count("/") != 2:
            continue
        role_id = str(getattr(assignment, "role_definition_id", "") or "")
        role = role_names.get(role_id.rsplit("/", 1)[-1], "")
        if role in PRIVILEGED_ROLES:
            by_role.setdefault(role, []).append(assignment)

    for role, holders in sorted(by_role.items()):
        principal_types = sorted({
            str(getattr(h, "principal_type", "Unknown")) for h in holders
        })
        findings.append(Finding(
            check="iam_subscription_privileged_role",
            severity=PRIVILEGED_ROLES[role],
            resource=f"subscription/{role}",
            resource_type="Microsoft.Authorization/roleAssignments",
            title=f"{len(holders)} principal(s) hold {role} on the whole subscription",
            detail=(
                f"{role} at subscription scope can act on every resource, "
                "including ones created later. Principal types: "
                f"{', '.join(principal_types)}."
            ),
            remediation=(
                "Scope these to the resource groups they actually need, or "
                "move them behind Privileged Identity Management so the "
                "access is time-bound rather than standing."
            ),
            evidence={
                "role": role,
                "count": len(holders),
                "principal_types": principal_types,
                "principal_ids": [
                    str(getattr(h, "principal_id", "?"))[:8] + "…" for h in holders[:10]
                ],
            },
        ))
    return findings


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def scan(subscription: str | None = None) -> dict[str, Any]:
    """
    Run every posture check against a subscription.

    A check that fails to enumerate is recorded as an error rather than as
    zero findings. Reporting "no public storage" because the storage API
    returned 403 is the same class of mistake as treating a failed
    vulnerability scan as a clean image.
    """
    subscription = subscription or subscription_id()
    credential = _credential()

    findings: list[Finding] = []
    errors: dict[str, str] = {}
    counts = {"storage_accounts": 0, "network_security_groups": 0, "role_assignments": 0}

    try:
        from azure.mgmt.storage import StorageManagementClient

        accounts = list(StorageManagementClient(credential, subscription).storage_accounts.list())
        counts["storage_accounts"] = len(accounts)
        for account in accounts:
            findings.extend(check_storage_account(account))
    except Exception as exc:
        errors["storage"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("Storage enumeration failed: %s", exc)

    try:
        from azure.mgmt.network import NetworkManagementClient

        groups = list(NetworkManagementClient(credential, subscription).network_security_groups.list_all())
        counts["network_security_groups"] = len(groups)
        for group in groups:
            findings.extend(check_network_security_group(group))
    except Exception as exc:
        errors["network"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("NSG enumeration failed: %s", exc)

    try:
        from azure.mgmt.authorization import AuthorizationManagementClient

        client = AuthorizationManagementClient(credential, subscription)
        assignments = list(client.role_assignments.list_for_subscription())
        counts["role_assignments"] = len(assignments)

        # Role definitions are needed to turn a definition GUID into a name.
        # Only the ones actually referenced are fetched.
        wanted = {
            str(a.role_definition_id).rsplit("/", 1)[-1]
            for a in assignments if getattr(a, "role_definition_id", None)
        }
        names: dict[str, str] = {}
        for definition in client.role_definitions.list(f"/subscriptions/{subscription}"):
            guid = str(definition.id).rsplit("/", 1)[-1]
            if guid in wanted:
                names[guid] = definition.role_name
        findings.extend(check_role_assignments(assignments, names))
    except Exception as exc:
        errors["authorization"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        log.warning("Role assignment enumeration failed: %s", exc)

    order = {"critical": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order.get(f.severity, 3), f.check))

    severities: dict[str, int] = {}
    for finding in findings:
        severities[finding.severity] = severities.get(finding.severity, 0) + 1

    return {
        "subscription_id": subscription,
        "summary": {
            "total": len(findings),
            "by_severity": severities,
            "resources_examined": counts,
            # Distinguishes "checked and clean" from "could not check". A
            # dashboard showing zero findings for an unreadable subscription
            # is worse than one showing an error.
            "checks_failed": sorted(errors),
        },
        "findings": [f.to_dict() for f in findings],
        "errors": errors,
        "credential": describe(),
    }
