"""
Tests for the agent's two load-bearing guarantees.

1. Environment variable VALUES never leave the cluster. This is the promise
   that makes an open-source read-only agent installable in a production
   cluster; if it breaks, the product is a credential exfiltration tool.
2. Dependency edges are inferred correctly. CEI is a graph algorithm, so a
   wrong graph makes every downstream number wrong.
"""

from __future__ import annotations

import json

import pytest

from cloudoptimizer_agent.collector import (
    attach_service_references,
    parse_cpu,
    parse_memory,
    workload_key,
)
from cloudoptimizer_agent.inference import infer_edges, map_services_to_workloads
from cloudoptimizer_agent.redact import (
    extract_service_references,
    redact_name,
    safe_env_summary,
)
from cloudoptimizer_agent.snapshot import assert_no_secrets, build_snapshot, serialize


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name",
    [
        "DB_PASSWORD", "STRIPE_API_KEY", "AWS_SECRET_ACCESS_KEY",
        "JWT_SIGNING_KEY", "SESSION_TOKEN", "TLS_CERT", "PASSWD",
        "oauth_client_secret", "Encryption_Salt",
    ],
)
def test_credential_bearing_names_are_redacted(name):
    assert redact_name(name) == "<redacted>"


@pytest.mark.parametrize(
    "name",
    ["PORT", "LOG_LEVEL", "PRODUCT_CATALOG_SERVICE_ADDR", "REDIS_ADDR"],
)
def test_ordinary_names_are_preserved(name):
    assert redact_name(name) == name


def test_secret_values_never_resolve_to_a_reference():
    """
    A secret that happens to look like a hostname still cannot escape, because
    only values matching an observed Service are ever emitted.
    """
    known = {"cartservice", "redis-cart"}
    values = [
        "postgres://admin:hunter2@db.internal:5432/prod",
        "sk_live_51H8xQ2eZvKYlo2C",
        "AKIAIOSFODNN7EXAMPLE",
        "-----BEGIN RSA PRIVATE KEY-----",
        "supersecretpassword",
    ]
    assert extract_service_references(values, known) == set()


def test_genuine_service_addresses_are_extracted():
    known = {"cartservice", "redis-cart", "productcatalogservice"}
    values = [
        "cartservice:7070",
        "http://productcatalogservice:3550",
        "redis-cart.default.svc.cluster.local:6379",
    ]
    found = {name for name, _ in extract_service_references(values, known)}
    assert found == {"cartservice", "productcatalogservice", "redis-cart"}


def test_bare_ports_and_numbers_are_not_hosts():
    """The spike's looser regex matched '8080' and '1' as candidate hosts."""
    assert extract_service_references(["8080", "1", "3550"], {"8080"}) == set()


def test_oversized_values_are_skipped_entirely():
    """A large blob is config or an encoded payload, never a service address."""
    known = {"cartservice"}
    assert extract_service_references(["cartservice " + "x" * 600], known) == set()


def test_env_summary_reports_counts_without_disclosing_values():
    summary = safe_env_summary(["PORT", "DB_PASSWORD", "LOG_LEVEL"])
    assert summary["count"] == 3
    assert summary["sensitive_count"] == 1
    assert "<redacted>" in summary["names"]
    assert "DB_PASSWORD" not in summary["names"]


def test_attach_service_references_removes_the_values():
    workloads = [{
        "key": "default/Deployment/frontend",
        "namespace": "default",
        "_env_values": ["cartservice:7070", "PASSWORD=hunter2"],
    }]
    services = [{"name": "cartservice", "namespace": "default", "selector": {}}]
    attach_service_references(workloads, services)

    assert "_env_values" not in workloads[0]
    assert workloads[0]["service_references"] == [
        {"service": "cartservice", "namespace": None}
    ]


def test_snapshot_refuses_to_serialize_raw_env_values():
    """Structural backstop against a future field carrying values through."""
    payload = json.dumps({"workloads": [{"_env_values": ["secret"]}]}).encode()
    with pytest.raises(RuntimeError, match="raw environment variable values"):
        assert_no_secrets(payload)


def test_built_snapshot_is_free_of_env_values():
    workloads = [{
        "key": "default/Deployment/api",
        "namespace": "default",
        "pod_labels": {"app": "api"},
        "_env_values": ["DB_PASSWORD=hunter2"],
    }]
    snapshot = build_snapshot(
        seq=1, cluster_uid="uid", provider="kind", kubernetes_version="1.29",
        nodes=[], workloads=workloads, services=[], pods=[], ingresses=[],
        network_policies=[], metrics_available=False, metrics_reason=None,
    )
    payload = serialize(snapshot)
    assert_no_secrets(payload)
    assert b"hunter2" not in payload


# --------------------------------------------------------------------------
# Edge inference
# --------------------------------------------------------------------------

def _workload(name, labels, refs=()):
    return {
        "key": workload_key("default", "Deployment", name),
        "name": name,
        "namespace": "default",
        "kind": "Deployment",
        "pod_labels": labels,
        "service_references": [
            {"service": r, "namespace": None} for r in refs
        ],
    }


def _service(name, selector):
    return {"name": name, "namespace": "default", "selector": selector}


def test_services_map_to_the_workload_they_select():
    workloads = [_workload("cart", {"app": "cart"})]
    services = [_service("cartservice", {"app": "cart"})]
    mapping = map_services_to_workloads(services, workloads)
    assert mapping["default/cartservice"] == "default/Deployment/cart"
    assert mapping["cartservice"] == "default/Deployment/cart"


def test_service_without_selector_is_ignored():
    """Headless and ExternalName services back no workload."""
    workloads = [_workload("cart", {"app": "cart"})]
    assert map_services_to_workloads([_service("external", {})], workloads) == {}


def test_edges_are_built_from_service_references():
    workloads = [
        _workload("frontend", {"app": "frontend"}, refs=["cartservice"]),
        _workload("cart", {"app": "cart"}),
    ]
    services = [_service("cartservice", {"app": "cart"})]
    edges = infer_edges(workloads, services, [])

    assert len(edges) == 1
    assert edges[0]["source"] == "default/Deployment/frontend"
    assert edges[0]["target"] == "default/Deployment/cart"
    assert edges[0]["source_kind"] == "env_reference"
    assert edges[0]["confidence"] == 0.9


def test_self_references_do_not_create_a_loop():
    """A workload naming its own Service must not become its own dependency."""
    workloads = [_workload("api", {"app": "api"}, refs=["apiservice"])]
    services = [_service("apiservice", {"app": "api"})]
    assert infer_edges(workloads, services, []) == []


def test_ingress_backends_become_edges():
    workloads = [_workload("frontend", {"app": "frontend"})]
    services = [_service("frontend", {"app": "frontend"})]
    ingresses = [{
        "name": "public",
        "namespace": "default",
        "backends": [{"service": "frontend", "host": "shop.example.com"}],
    }]
    edges = infer_edges(workloads, services, ingresses)
    assert len(edges) == 1
    assert edges[0]["source"] == "default/Ingress/public"
    assert edges[0]["source_kind"] == "ingress"
    assert edges[0]["confidence"] == 1.0


def test_ambiguous_bare_service_names_are_not_guessed():
    """
    The same Service name in two namespaces cannot be resolved from a bare
    reference, so it is dropped rather than attributed to one at random.
    """
    workloads = [
        {**_workload("a", {"app": "a"}), "namespace": "ns1",
         "key": "ns1/Deployment/a"},
        {**_workload("b", {"app": "b"}), "namespace": "ns2",
         "key": "ns2/Deployment/b"},
    ]
    services = [
        {"name": "api", "namespace": "ns1", "selector": {"app": "a"}},
        {"name": "api", "namespace": "ns2", "selector": {"app": "b"}},
    ]
    mapping = map_services_to_workloads(services, workloads)
    assert "api" not in mapping
    assert mapping["ns1/api"] == "ns1/Deployment/a"
    assert mapping["ns2/api"] == "ns2/Deployment/b"


# --------------------------------------------------------------------------
# Quantity parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [("100m", 0.1), ("1", 1.0), ("2500m", 2.5), ("500000u", 0.5),
     ("1000000000n", 1.0), (None, None), ("garbage", None)],
)
def test_cpu_quantities(value, expected):
    assert parse_cpu(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [("128Mi", 134217728), ("1Gi", 1073741824), ("1000", 1000),
     ("1M", 1000000), (None, None), ("garbage", None)],
)
def test_memory_quantities(value, expected):
    assert parse_memory(value) == expected
