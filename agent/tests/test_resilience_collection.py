"""
Tests for the resilience signals added in schema version 2.

Uses lightweight stand-ins rather than the Kubernetes client's model classes:
the collector reads these objects only through attribute access, and building
real V1Deployment trees to test a probe count obscures what is being asserted.
"""

from types import SimpleNamespace as NS

import pytest
from kubernetes.client.rest import ApiException

from cloudoptimizer_agent.collector import (
    ClusterCollector,
    config_references,
    ownership_metadata,
    probe_coverage,
    spread_policy,
)
from cloudoptimizer_agent.snapshot import SNAPSHOT_SCHEMA_VERSION, build_snapshot


# --- ownership -------------------------------------------------------------


def test_ownership_pulls_known_keys_from_labels_and_annotations():
    found = ownership_metadata(
        {"app.kubernetes.io/part-of": "checkout", "tier": "backend"},
        {"team": "payments", "slack": "#payments-oncall"},
    )

    assert found == {
        "app.kubernetes.io/part-of": "checkout",
        "team": "payments",
        "slack": "#payments-oncall",
    }
    assert "tier" not in found


def test_last_applied_configuration_is_never_collected():
    """
    The annotation kubectl writes holds the entire submitted manifest,
    environment variables included. Collecting annotations wholesale would
    route around redact.py completely.
    """
    found = ownership_metadata(
        {},
        {
            "kubectl.kubernetes.io/last-applied-configuration":
                '{"spec":{"containers":[{"env":[{"name":"DB_PASSWORD","value":"hunter2"}]}]}}',
            "team": "payments",
        },
    )

    assert found == {"team": "payments"}
    assert not any("hunter2" in v for v in found.values())


def test_ownership_values_are_truncated():
    found = ownership_metadata({}, {"owner": "x" * 500})

    assert len(found["owner"]) == 120


def test_no_ownership_returns_empty():
    assert ownership_metadata({"app": "web"}, None) == {}


# --- probes ----------------------------------------------------------------


def test_probe_coverage_counts_per_container():
    containers = [
        NS(readiness_probe=object(), liveness_probe=object(), startup_probe=None),
        NS(readiness_probe=None, liveness_probe=object(), startup_probe=None),
        NS(readiness_probe=None, liveness_probe=None, startup_probe=None),
    ]
    assert probe_coverage(containers) == {
        "containers": 3, "with_readiness": 1, "with_liveness": 2, "with_startup": 0,
    }


def test_probe_coverage_handles_no_containers():
    assert probe_coverage(None)["containers"] == 0


# --- spread ----------------------------------------------------------------


def test_spread_policy_records_each_mechanism():
    pod_spec = NS(
        affinity=NS(pod_anti_affinity=NS(
            required_during_scheduling_ignored_during_execution=[object()],
            preferred_during_scheduling_ignored_during_execution=[object(), object()],
        )),
        topology_spread_constraints=[NS(topology_key="kubernetes.io/hostname")],
        priority_class_name="high",
    )
    assert spread_policy(pod_spec) == {
        "topology_spread_constraints": 1,
        "topology_keys": ["kubernetes.io/hostname"],
        "anti_affinity_required": 1,
        "anti_affinity_preferred": 2,
        "priority_class": "high",
    }


def test_spread_policy_with_nothing_configured():
    result = spread_policy(NS(
        affinity=None, topology_spread_constraints=None, priority_class_name=None
    ))

    assert result["topology_spread_constraints"] == 0
    assert result["anti_affinity_required"] == 0
    assert result["priority_class"] is None


# --- config references -----------------------------------------------------


def test_config_references_finds_every_reference_style():
    pod_spec = NS(
        containers=[NS(
            env_from=[NS(config_map_ref=NS(name="app-config"), secret_ref=None)],
            env=[NS(value_from=NS(
                config_map_key_ref=NS(name="feature-flags"),
                secret_key_ref=NS(name="db-creds"),
            ))],
        )],
        init_containers=[NS(
            env_from=[NS(config_map_ref=None, secret_ref=NS(name="registry-auth"))],
            env=[],
        )],
        volumes=[
            NS(config_map=NS(name="nginx-conf"), secret=None, projected=None),
            NS(config_map=None, secret=NS(secret_name="tls-cert"), projected=None),
        ],
    )
    assert config_references(pod_spec) == {
        "config_maps": ["app-config", "feature-flags", "nginx-conf"],
        "secrets": ["db-creds", "registry-auth", "tls-cert"],
    }


def test_config_references_reports_names_only():
    """Names identify fan-in. Contents are never read; RBAC forbids Secrets."""
    pod_spec = NS(
        containers=[NS(env_from=[NS(config_map_ref=NS(name="c"), secret_ref=None)], env=[])],
        init_containers=[], volumes=[],
    )
    result = config_references(pod_spec)

    assert result["config_maps"] == ["c"]
    assert set(result) == {"config_maps", "secrets"}


def test_config_references_handles_projected_volumes():
    pod_spec = NS(
        containers=[], init_containers=[],
        volumes=[NS(config_map=None, secret=None, projected=NS(sources=[
            NS(config_map=NS(name="ca-bundle"), secret=None),
            NS(config_map=None, secret=NS(name="sa-token")),
        ]))],
    )
    result = config_references(pod_spec)

    assert result["config_maps"] == ["ca-bundle"]
    assert result["secrets"] == ["sa-token"]


def test_config_references_empty_pod_spec():
    assert config_references(NS(containers=[], init_containers=None, volumes=None)) == {
        "config_maps": [], "secrets": [],
    }


# --- graceful degradation --------------------------------------------------


class _Config:
    def wants_namespace(self, namespace):
        return namespace != "excluded"


def _collector():
    collector = ClusterCollector.__new__(ClusterCollector)
    collector.config = _Config()
    return collector


@pytest.mark.parametrize("status", [403, 404])
def test_missing_rbac_yields_empty_not_failure(status, caplog):
    """
    An agent upgraded ahead of its ClusterRole must lose one signal, not the
    entire snapshot. Failing here would take CEI, cost, and health down with
    it for the whole cluster.
    """
    def denied():
        raise ApiException(status=status)

    result = _collector()._optional_list("poddisruptionbudgets", denied, lambda i: {})

    assert result == []
    assert "ClusterRole" in caplog.text or "not collected" in caplog.text


def test_absent_api_group_yields_empty():
    def missing():
        raise AttributeError("AutoscalingV2Api not available")

    assert _collector()._optional_list("horizontalpodautoscalers", missing, lambda i: {}) == []


def test_optional_list_applies_namespace_filter():
    items = [
        NS(metadata=NS(name="a", namespace="default")),
        NS(metadata=NS(name="b", namespace="excluded")),
    ]
    result = _collector()._optional_list(
        "poddisruptionbudgets", lambda: items, lambda i: {"name": i.metadata.name}
    )

    assert result == [{"name": "a"}]


# --- record shapes ---------------------------------------------------------


def test_pdb_record_carries_disruptions_allowed():
    """
    minAvailable: 1 on a single replica reports 0 allowed disruptions: it
    protects nothing and blocks every node drain. That number is the reason
    this resource is collected at all.
    """
    item = NS(
        metadata=NS(name="api-pdb", namespace="prod"),
        spec=NS(selector=NS(match_labels={"app": "api"}), min_available=1, max_unavailable=None),
        status=NS(disruptions_allowed=0, current_healthy=1, desired_healthy=1, expected_pods=1),
    )
    record = ClusterCollector._pdb_record(item)

    assert record["disruptions_allowed"] == 0
    assert record["min_available"] == "1"
    assert record["max_unavailable"] is None
    assert record["selector"] == {"app": "api"}


def test_pdb_record_handles_percentage_strings():
    item = NS(
        metadata=NS(name="p", namespace="prod"),
        spec=NS(selector=None, min_available=None, max_unavailable="25%"),
        status=NS(disruptions_allowed=2, current_healthy=8, desired_healthy=6, expected_pods=8),
    )
    record = ClusterCollector._pdb_record(item)

    assert record["max_unavailable"] == "25%"
    assert record["selector"] == {}


def test_hpa_record_captures_target_and_ceiling():
    item = NS(
        metadata=NS(name="api-hpa", namespace="prod"),
        spec=NS(scale_target_ref=NS(kind="Deployment", name="api"),
                min_replicas=2, max_replicas=10,
                metrics=[NS(type="Resource", resource=NS(name="cpu"))]),
        status=NS(current_replicas=10, desired_replicas=10),
    )
    record = ClusterCollector._hpa_record(item)

    assert record["target_kind"] == "Deployment"
    assert record["target_name"] == "api"
    # Pinned at the ceiling: not autoscaling, absorbing load it cannot shed.
    assert record["current_replicas"] == record["max_replicas"]
    assert record["metrics"] == [{"type": "Resource", "resource": "cpu"}]


def test_hpa_record_survives_a_spec_without_metrics():
    """Older API objects may lack the field entirely; empty means default CPU."""
    item = NS(
        metadata=NS(name="h", namespace="prod"),
        spec=NS(scale_target_ref=NS(kind="Deployment", name="api"),
                min_replicas=1, max_replicas=2),
        status=NS(current_replicas=1, desired_replicas=1),
    )
    assert ClusterCollector._hpa_record(item)["metrics"] == []


# --- snapshot integration --------------------------------------------------


def _snapshot(**overrides):
    base = dict(
        seq=1, cluster_uid="uid", provider="kind", kubernetes_version="v1.35.0",
        nodes=[], workloads=[], services=[], pods=[], ingresses=[],
        network_policies=[], metrics_available=False, metrics_reason=None,
    )
    base.update(overrides)
    return build_snapshot(**base)


def test_snapshot_version_is_two():
    assert SNAPSHOT_SCHEMA_VERSION == 2
    assert _snapshot()["schema_version"] == 2


def test_new_collections_default_to_empty():
    """A v2 agent denied the new RBAC still produces a valid snapshot."""
    snapshot = _snapshot()

    assert snapshot["disruption_budgets"] == []
    assert snapshot["autoscalers"] == []
    assert snapshot["summary"]["disruption_budgets"] == 0


def test_new_collections_appear_in_summary():
    snapshot = _snapshot(
        disruption_budgets=[{"name": "a"}, {"name": "b"}],
        autoscalers=[{"name": "h"}],
    )

    assert snapshot["summary"]["disruption_budgets"] == 2
    assert snapshot["summary"]["autoscalers"] == 1
