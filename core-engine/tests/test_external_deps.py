"""
External dependency graph.

The headline case is the one Kubernetes cannot show: workloads with no
dependency path between them that nonetheless fail together because they
share an identity provider.
"""

import json

import pytest

from src.services.external_deps import (
    CONVERGENCE_THRESHOLD,
    analyze,
    build_combined_graph,
    build_external_nodes,
    classify,
    parse_terraform_state,
)


def _workload(name, ns="prod"):
    return {
        "key": f"{ns}/Deployment/{name}", "name": name, "namespace": ns,
        "kind": "Deployment", "replicas_desired": 2,
        "pod_labels": {"app": name}, "labels": {"app": name},
        "config_refs": {"config_maps": [], "secrets": []},
    }


def _egress(mapping, available=True):
    """mapping: {workload_key: [(destination, ports, dns_resolved), ...]}"""
    return {
        "available": available,
        "workloads": {
            key: [
                {"destination": d, "ports": list(p), "dns_resolved": r,
                 "flow_count": 10, "dropped_count": 0, "ips": []}
                for d, p, r in entries
            ]
            for key, entries in mapping.items()
        },
    }


def _kinds(result):
    return {f["kind"] for f in result["findings"]}


def _find(result, kind, endpoint=None):
    return next(
        f for f in result["findings"]
        if f["kind"] == kind and (endpoint is None or f["endpoint"] == endpoint)
    )


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize("host,category,provider", [
    ("prod-db.abc123.us-east-1.rds.amazonaws.com", "database", "AWS RDS"),
    ("mycache.abc.0001.use1.cache.amazonaws.com", "cache", "AWS ElastiCache"),
    ("assets.s3.us-east-1.amazonaws.com", "storage", "AWS S3"),
    ("tenant.auth0.com", "identity", "Auth0"),
    ("acme.okta.com", "identity", "Okta"),
    ("login.microsoftonline.com", "identity", "Microsoft Entra ID"),
    ("api.stripe.com", "payments", "Stripe"),
    ("cluster0.ab12c.mongodb.net", "database", "MongoDB Atlas"),
    ("myserver.database.windows.net", "database", "Azure SQL"),
    ("storage.googleapis.com", "storage", "Google Cloud Storage"),
    ("app.datadoghq.com", "observability", "Datadog"),
    ("ghcr.io", "registry", "container registry"),
    ("1.1.1.1", "dns", "public DNS resolver"),
])
def test_classification(host, category, provider):
    assert classify(host) == (category, provider)


def test_unknown_host_is_not_guessed():
    assert classify("internal-tool.acme-corp.example") == ("unknown", None)


def test_classification_strips_scheme_and_port():
    assert classify("https://api.stripe.com:443/v1")[1] == "Stripe"


def test_identity_and_dns_carry_the_heaviest_weight():
    from src.services.external_deps import CATEGORY_WEIGHT

    assert CATEGORY_WEIGHT["identity"] == max(CATEGORY_WEIGHT.values())
    assert CATEGORY_WEIGHT["dns"] == max(CATEGORY_WEIGHT.values())
    assert CATEGORY_WEIGHT["observability"] < CATEGORY_WEIGHT["database"]


# --- terraform state --------------------------------------------------------


V4_STATE = {
    "version": 4,
    "resources": [
        {
            "mode": "managed", "type": "aws_db_instance", "name": "primary",
            "instances": [{"attributes": {
                "endpoint": "prod-db.abc123.us-east-1.rds.amazonaws.com:5432",
                "identifier": "prod-db",
            }}],
        },
        {
            "mode": "managed", "type": "aws_s3_bucket", "name": "assets",
            "instances": [{"attributes": {
                "bucket_domain_name": "assets.s3.amazonaws.com",
                "bucket": "assets",
            }}],
        },
        {
            # Data sources read infrastructure they do not own.
            "mode": "data", "type": "aws_db_instance", "name": "readonly",
            "instances": [{"attributes": {"endpoint": "other.rds.amazonaws.com"}}],
        },
        {
            "mode": "managed", "type": "aws_iam_role", "name": "irrelevant",
            "instances": [{"attributes": {"arn": "arn:aws:iam::1:role/x"}}],
        },
    ],
}


def test_terraform_v4_state_parsing():
    parsed = {r["endpoint"]: r for r in parse_terraform_state(V4_STATE)}

    assert "prod-db.abc123.us-east-1.rds.amazonaws.com" in parsed
    assert parsed["prod-db.abc123.us-east-1.rds.amazonaws.com"]["category"] == "database"
    assert parsed["prod-db.abc123.us-east-1.rds.amazonaws.com"]["terraform_address"] == (
        "aws_db_instance.primary"
    )


def test_terraform_port_is_stripped_from_the_endpoint():
    """The hostname is the join key; observed traffic reports it without a port."""
    parsed = {r["endpoint"] for r in parse_terraform_state(V4_STATE)}

    assert "prod-db.abc123.us-east-1.rds.amazonaws.com" in parsed
    assert not any(":" in e for e in parsed)


def test_data_sources_are_skipped():
    """A data source reads infrastructure it does not own."""
    parsed = {r["endpoint"] for r in parse_terraform_state(V4_STATE)}

    assert "other.rds.amazonaws.com" not in parsed


def test_unmapped_resource_types_are_ignored():
    parsed = {r["type"] for r in parse_terraform_state(V4_STATE)}

    assert "aws_iam_role" not in parsed


def test_terraform_v3_state_parsing():
    """v3 still turns up in older repositories."""
    state = {
        "version": 3,
        "modules": [{
            "path": ["root"],
            "resources": {
                "aws_db_instance.legacy": {
                    "type": "aws_db_instance",
                    "primary": {"attributes": {"address": "legacy.rds.amazonaws.com"}},
                },
            },
        }],
    }
    parsed = parse_terraform_state(state)

    assert parsed[0]["endpoint"] == "legacy.rds.amazonaws.com"


def test_nested_attribute_structures():
    """cache_nodes is a list of objects, not a string."""
    state = {"version": 4, "resources": [{
        "mode": "managed", "type": "aws_elasticache_cluster", "name": "sessions",
        "instances": [{"attributes": {
            "cache_nodes": [{"address": "sessions.abc.cache.amazonaws.com", "port": 6379}],
        }}],
    }]}
    assert parse_terraform_state(state)[0]["endpoint"] == "sessions.abc.cache.amazonaws.com"


def test_state_accepts_a_json_string():
    assert parse_terraform_state(json.dumps(V4_STATE))


@pytest.mark.parametrize("bad", ["", "not json", "[]", "null", {}, {"version": 4}])
def test_malformed_state_yields_nothing(bad):
    assert parse_terraform_state(bad) == []


# --- node assembly ----------------------------------------------------------


def test_traffic_decides_which_nodes_exist():
    """A provisioned resource nothing calls is not a dependency."""
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [
        ("prod-db.abc123.us-east-1.rds.amazonaws.com", [5432], True),
    ]})
    nodes = build_external_nodes(snapshot, egress, V4_STATE)

    assert set(nodes) == {"prod-db.abc123.us-east-1.rds.amazonaws.com"}
    assert "assets.s3.amazonaws.com" not in nodes


def test_terraform_enriches_what_traffic_found():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [
        ("prod-db.abc123.us-east-1.rds.amazonaws.com", [5432], True),
    ]})
    node = build_external_nodes(snapshot, egress, V4_STATE)[
        "prod-db.abc123.us-east-1.rds.amazonaws.com"
    ]

    assert node.provenance == "both"
    assert node.terraform_address == "aws_db_instance.primary"
    assert node.category == "database"


def test_endpoint_with_no_terraform_match_is_egress_only():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [("tenant.auth0.com", [443], True)]})
    node = build_external_nodes(snapshot, egress, V4_STATE)["tenant.auth0.com"]

    assert node.provenance == "egress"
    assert node.terraform_address is None


def test_combined_graph_attaches_external_nodes():
    snapshot = {"workloads": [_workload("api"), _workload("worker")], "edges": []}
    egress = _egress({
        "prod/Deployment/api": [("tenant.auth0.com", [443], True)],
        "prod/Deployment/worker": [("tenant.auth0.com", [443], True)],
    })
    external = build_external_nodes(snapshot, egress)
    graph = build_combined_graph(snapshot, external)

    key = "external/identity/tenant.auth0.com"
    assert key in graph
    assert graph.nodes[key]["is_external"] is True
    # Edges run workload -> external, matching the existing convention.
    assert graph.has_edge("prod/Deployment/api", key)
    assert set(graph.predecessors(key)) == {
        "prod/Deployment/api", "prod/Deployment/worker",
    }


# --- convergence and hidden coupling ---------------------------------------


@pytest.fixture
def fourteen_services():
    """
    The motivating case: many services, no dependency between any of them
    inside the cluster, all validating tokens against one identity provider.
    """
    names = [f"svc{i}" for i in range(14)]
    snapshot = {"workloads": [_workload(n) for n in names], "edges": []}
    egress = _egress({
        f"prod/Deployment/{n}": [("tenant.auth0.com", [443], True)] for n in names
    })
    return snapshot, egress


def test_convergence_on_an_identity_provider_is_critical(fourteen_services):
    snapshot, egress = fourteen_services
    finding = _find(analyze(snapshot, egress), "external_convergence")

    assert finding["severity"] == "critical"
    assert "14 workloads" in finding["title"]
    assert finding["category"] == "identity"


def test_hidden_coupling_is_reported(fourteen_services):
    """
    No path between any pair inside the cluster. Every Kubernetes-scoped view
    calls them independent. They are not.
    """
    snapshot, egress = fourteen_services
    finding = _find(analyze(snapshot, egress), "hidden_coupling")

    assert finding["severity"] == "critical"
    assert len(finding["evidence"]["workloads"]) == 14
    assert "look independent" in finding["title"]


def test_workloads_already_coupled_internally_are_not_hidden_coupling():
    """
    If a already depends on b, their shared external dependency is not a
    surprise -- they were going to fail together anyway.
    """
    snapshot = {
        "workloads": [_workload("a"), _workload("b")],
        "edges": [{"source": "prod/Deployment/a", "target": "prod/Deployment/b",
                   "confidence": 0.9, "source_kind": "env_reference"}],
    }
    egress = _egress({
        "prod/Deployment/a": [("tenant.auth0.com", [443], True)],
        "prod/Deployment/b": [("tenant.auth0.com", [443], True)],
    })
    assert "hidden_coupling" not in _kinds(analyze(snapshot, egress))


def test_convergence_threshold_is_respected():
    snapshot = {"workloads": [_workload("a"), _workload("b")], "edges": []}
    egress = _egress({
        "prod/Deployment/a": [("api.stripe.com", [443], True)],
        "prod/Deployment/b": [("api.stripe.com", [443], True)],
    })
    assert CONVERGENCE_THRESHOLD == 3
    assert "external_convergence" not in _kinds(analyze(snapshot, egress))


def test_observability_convergence_is_only_informational():
    """Dashboards going dark is not the same as requests failing."""
    names = [f"svc{i}" for i in range(6)]
    snapshot = {"workloads": [_workload(n) for n in names], "edges": []}
    egress = _egress({
        f"prod/Deployment/{n}": [("app.datadoghq.com", [443], True)] for n in names
    })
    assert _find(analyze(snapshot, egress), "external_convergence")["severity"] == "info"


def test_ranking_weights_category_not_just_count():
    """
    Six workloads on a metrics endpoint outrank by count; three on an identity
    provider outrank by consequence.
    """
    snapshot = {"workloads": [_workload(f"s{i}") for i in range(6)], "edges": []}
    egress = _egress({
        **{f"prod/Deployment/s{i}": [("app.datadoghq.com", [443], True)] for i in range(6)},
        **{f"prod/Deployment/s{i}": [
            ("app.datadoghq.com", [443], True), ("acme.okta.com", [443], True),
        ] for i in range(3)},
    })
    nodes = analyze(snapshot, egress)["external_nodes"]

    assert nodes[0]["endpoint"] == "acme.okta.com"
    assert nodes[0]["dependent_count"] < nodes[1]["dependent_count"]


# --- undeclared and bare IP -------------------------------------------------


def test_undeclared_dependency_is_reported_when_state_is_supplied():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [
        ("shadow-db.xyz.us-east-1.rds.amazonaws.com", [5432], True),
    ]})
    finding = _find(analyze(snapshot, egress, V4_STATE), "undeclared_dependency")

    assert finding["severity"] == "warning"
    assert "nothing to rebuild from" in finding["detail"]


def test_no_undeclared_findings_without_state():
    """Everything is undeclared when no state was supplied; saying so is noise."""
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [("shadow.rds.amazonaws.com", [5432], True)]})

    assert "undeclared_dependency" not in _kinds(analyze(snapshot, egress))


def test_registries_are_not_reported_as_undeclared():
    """Nobody puts Docker Hub in Terraform."""
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [("ghcr.io", [443], True)]})
    result = analyze(snapshot, egress, V4_STATE)

    assert "undeclared_dependency" not in _kinds(result)


def test_declared_dependency_is_not_flagged():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [
        ("prod-db.abc123.us-east-1.rds.amazonaws.com", [5432], True),
    ]})
    assert "undeclared_dependency" not in _kinds(analyze(snapshot, egress, V4_STATE))


def test_bare_ip_dependency_is_flagged():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [("203.0.113.9", [5432], False)]})
    finding = _find(analyze(snapshot, egress), "external_bare_ip")

    assert "cannot fail over" in finding["detail"]


# --- degradation ------------------------------------------------------------


def test_no_egress_data_reports_unavailable_not_empty():
    """
    Zero external dependencies and "we cannot see external dependencies" are
    different claims, and only one of them is true here.
    """
    result = analyze({"workloads": [_workload("api")], "edges": []}, {"available": False})

    assert result["available"] is False
    assert "requires flow observation" in result["reason"]
    assert result["findings"] == []


def test_missing_egress_argument_is_unavailable():
    assert analyze({"workloads": [], "edges": []})["available"] is False


def test_empty_egress_is_available_but_empty():
    result = analyze({"workloads": [], "edges": []}, {"available": True, "workloads": {}})

    assert result["available"] is True
    assert result["summary"]["external_nodes"] == 0


def test_unknown_destination_is_skipped():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [("unknown", [443], False)]})

    assert analyze(snapshot, egress)["summary"]["external_nodes"] == 0


def test_summary_counts_terraform_joins():
    snapshot = {"workloads": [_workload("api")], "edges": []}
    egress = _egress({"prod/Deployment/api": [
        ("prod-db.abc123.us-east-1.rds.amazonaws.com", [5432], True),
        ("tenant.auth0.com", [443], True),
    ]})
    summary = analyze(snapshot, egress, V4_STATE)["summary"]

    assert summary["external_nodes"] == 2
    assert summary["joined_with_terraform"] == 1
    assert summary["by_category"] == {"database": 1, "identity": 1}
