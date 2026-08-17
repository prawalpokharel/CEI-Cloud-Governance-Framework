"""
Phase 5 (Azure CSPM) and Phase 6 (egress analysis).

The Azure check logic is tested against synthetic resources because the
subscription available for development is empty. The credential chain and
enumeration were verified live; the checks themselves need inputs that
subscription does not contain.

The egress analysis is tested against realistic Hubble summaries. It has NOT
been validated against live flow data — see agent/cloudoptimizer_agent/flows.py.
"""

from __future__ import annotations

import types

import pytest

from src.services.cspm_azure import (
    check_network_security_group,
    check_role_assignments,
    check_storage_account,
    describe,
)
from src.services.egress import analyze_egress


def obj(**kw):
    return types.SimpleNamespace(**kw)


# --------------------------------------------------------------------------
# Azure — credential posture
# --------------------------------------------------------------------------

def test_no_client_secret_is_required():
    """
    Requiring a service principal before anything works means an evaluator
    creates one, or commits one somewhere it should not be.
    """
    d = describe()
    assert d["client_secret_required"] is False
    assert d["credential"] == "DefaultAzureCredential"


# --------------------------------------------------------------------------
# Azure — storage
# --------------------------------------------------------------------------

def test_public_blob_access_enabled_is_critical():
    findings = check_storage_account(
        obj(name="sa1", allow_blob_public_access=True,
            enable_https_traffic_only=True, minimum_tls_version="TLS1_2")
    )
    assert findings[0].check == "storage_public_blob_access"
    assert findings[0].severity == "critical"


def test_unset_public_access_is_still_reported():
    """
    Azure treats unset as permitted on older accounts. Reading absent as
    disabled would under-report exactly the case that matters.
    """
    findings = check_storage_account(
        obj(name="sa1", allow_blob_public_access=None,
            enable_https_traffic_only=True, minimum_tls_version="TLS1_2")
    )
    assert any(f.check == "storage_public_blob_access" for f in findings)


def test_explicitly_disabled_public_access_is_clean():
    findings = check_storage_account(
        obj(name="sa1", allow_blob_public_access=False,
            enable_https_traffic_only=True, minimum_tls_version="TLS1_2")
    )
    assert findings == []


def test_plain_http_and_weak_tls_are_reported():
    findings = check_storage_account(
        obj(name="sa1", allow_blob_public_access=False,
            enable_https_traffic_only=False, minimum_tls_version="TLS1_0")
    )
    kinds = {f.check for f in findings}
    assert {"storage_http_allowed", "storage_weak_tls"} <= kinds


def test_remediation_is_a_runnable_command():
    findings = check_storage_account(
        obj(name="mystore", allow_blob_public_access=True,
            enable_https_traffic_only=True, minimum_tls_version="TLS1_2")
    )
    assert "az storage account update" in findings[0].remediation
    assert "mystore" in findings[0].remediation


# --------------------------------------------------------------------------
# Azure — network security groups
# --------------------------------------------------------------------------

def _rule(**kw):
    base = dict(name="r", direction="Inbound", access="Allow",
                source_address_prefix="*", destination_port_range="22",
                priority=100, source_address_prefixes=None,
                destination_port_ranges=None)
    base.update(kw)
    return obj(**base)


def test_ssh_open_to_the_internet_is_critical():
    findings = check_network_security_group(obj(name="nsg1", security_rules=[_rule()]))
    assert len(findings) == 1
    assert findings[0].severity == "critical"
    assert "SSH" in findings[0].title


def test_a_restricted_source_is_not_flagged():
    findings = check_network_security_group(
        obj(name="nsg1", security_rules=[_rule(source_address_prefix="10.0.0.0/8")])
    )
    assert findings == []


def test_deny_rules_are_not_flagged():
    findings = check_network_security_group(
        obj(name="nsg1", security_rules=[_rule(access="Deny")])
    )
    assert findings == []


def test_outbound_rules_are_not_flagged():
    findings = check_network_security_group(
        obj(name="nsg1", security_rules=[_rule(direction="Outbound")])
    )
    assert findings == []


def test_a_port_range_covering_a_sensitive_port_is_flagged():
    findings = check_network_security_group(
        obj(name="nsg1", security_rules=[
            _rule(destination_port_range="20-30")])
    )
    assert "SSH" in findings[0].title


def test_a_wildcard_port_exposes_everything():
    """
    A rule opening all ports is worse than any single port and must not be
    skipped just because '*' does not parse as an integer.
    """
    findings = check_network_security_group(
        obj(name="nsg1", security_rules=[_rule(destination_port_range="*")])
    )
    assert findings
    assert "RDP" in findings[0].title or "SSH" in findings[0].title


def test_https_open_to_the_internet_is_not_a_finding():
    """A public web server is the point of a public web server."""
    findings = check_network_security_group(
        obj(name="nsg1", security_rules=[_rule(destination_port_range="443")])
    )
    assert findings == []


# --------------------------------------------------------------------------
# Azure — IAM
# --------------------------------------------------------------------------

def test_subscription_scoped_owner_is_critical():
    assignments = [
        obj(scope="/subscriptions/abc", role_definition_id="/x/y/owner-guid",
            principal_id="p1", principal_type="User"),
    ]
    findings = check_role_assignments(assignments, {"owner-guid": "Owner"})
    assert findings[0].severity == "critical"
    assert "whole subscription" in findings[0].title


def test_resource_group_scoped_roles_are_not_flagged():
    """Owner on one resource group is normal delegation, not a blast radius."""
    assignments = [
        obj(scope="/subscriptions/abc/resourceGroups/rg1",
            role_definition_id="/x/y/owner-guid",
            principal_id="p1", principal_type="User"),
    ]
    assert check_role_assignments(assignments, {"owner-guid": "Owner"}) == []


def test_holders_of_the_same_role_are_grouped():
    assignments = [
        obj(scope="/subscriptions/abc", role_definition_id="/x/y/owner-guid",
            principal_id=f"p{i}", principal_type="User")
        for i in range(3)
    ]
    findings = check_role_assignments(assignments, {"owner-guid": "Owner"})
    assert len(findings) == 1
    assert findings[0].evidence["count"] == 3


def test_principal_ids_are_truncated_in_evidence():
    """Full object IDs are not needed to act on the finding."""
    assignments = [
        obj(scope="/subscriptions/abc", role_definition_id="/x/y/owner-guid",
            principal_id="0123456789abcdef", principal_type="ServicePrincipal"),
    ]
    findings = check_role_assignments(assignments, {"owner-guid": "Owner"})
    assert "0123456789abcdef" not in str(findings[0].evidence["principal_ids"])


def test_unprivileged_roles_are_not_flagged():
    assignments = [
        obj(scope="/subscriptions/abc", role_definition_id="/x/y/reader-guid",
            principal_id="p1", principal_type="User"),
    ]
    assert check_role_assignments(assignments, {"reader-guid": "Reader"}) == []


# --------------------------------------------------------------------------
# Egress
# --------------------------------------------------------------------------

def _summary(workloads, **kw):
    base = {"available": True, "workloads": workloads, "flows_examined": 100,
            "window_seconds": 90, "dropped_by_workload": {}}
    base.update(kw)
    return base


def _dest(destination, **kw):
    base = {"destination": destination, "ips": ["93.184.216.34"], "ports": [443],
            "flow_count": 10, "dropped_count": 0, "dns_resolved": True}
    base.update(kw)
    return base


def test_without_hubble_the_analysis_says_so_rather_than_reporting_clean():
    result = analyze_egress({"available": False, "reason": "no hubble"})
    assert result["available"] is False
    assert result["findings"] == []


def test_dropped_egress_is_reported():
    result = analyze_egress(_summary({
        "ns/Deployment/api": [_dest("evil.test", dropped_count=4)],
    }))
    assert any(f["kind"] == "egress_denied" for f in result["findings"])


def test_a_bare_ip_destination_is_reported():
    result = analyze_egress(_summary({
        "ns/Deployment/api": [_dest("203.0.113.9", dns_resolved=False)],
    }))
    assert any(f["kind"] == "egress_unresolved_ip" for f in result["findings"])


def test_administrative_ports_leaving_the_cluster_are_critical():
    result = analyze_egress(_summary({
        "ns/Deployment/api": [_dest("somewhere.test", ports=[22])],
    }))
    ssh = [f for f in result["findings"] if f["kind"] == "egress_notable_port"]
    assert ssh and ssh[0]["severity"] == "critical"


def test_common_registries_do_not_dominate_the_list():
    """Package registries appear in every cluster and are not signal."""
    result = analyze_egress(_summary({
        "ns/Deployment/api": [_dest("pypi.org"), _dest("ghcr.io")],
    }))
    assert result["findings"] == []


def test_a_destination_only_one_workload_reaches_is_surfaced():
    result = analyze_egress(_summary({
        "ns/Deployment/api": [_dest("unique-partner.test")],
        "ns/Deployment/web": [_dest("shared.test")],
        "ns/Deployment/job": [_dest("shared.test")],
    }))
    unique = [f for f in result["findings"] if f["kind"] == "egress_unique_destination"]
    assert [f["destination"] for f in unique] == ["unique-partner.test"]


def test_findings_are_ranked_by_blast_radius_within_severity():
    summary = _summary({
        "ns/Deployment/critical": [_dest("a.test", ports=[22])],
        "ns/Deployment/scratch": [_dest("b.test", ports=[22])],
    })
    cei = {
        "ns/Deployment/critical": {"cei_score": 0.9},
        "ns/Deployment/scratch": {"cei_score": 0.05},
    }
    findings = analyze_egress(summary, cei)["findings"]
    assert findings[0]["workload_key"] == "ns/Deployment/critical"


def test_the_window_limitation_is_stated():
    """A workload contacting somewhere rarely will not appear, and that is
    a property a reader must know before trusting an empty result."""
    note = analyze_egress(_summary({}))["summary"]["note"]
    assert "bounded window" in note
