"""
End-to-end tests for the dashboard endpoints, through HTTP.

The service-level suites cover the analyses in depth. These cover the part
those cannot: that the routes are wired, that auth is enforced, that a
snapshot ingested by an agent is the one the analyses read, and that the
response actually serialises. Every unit test in this repository passed while
the routers were mounted behind a `DATABASE_URL` check that had never been
exercised.

Skipped without TEST_DATABASE_URL, like the other database-backed suites.

    export TEST_DATABASE_URL=postgres://cloudopt:devpass@localhost:55432/cloudoptimizer
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


@pytest.fixture(scope="session")
def client(api_client):
    """Shared with every other HTTP suite; see conftest.api_client."""
    return api_client


@pytest.fixture(scope="module")
def auth(client):
    email = f"api-test-{uuid.uuid4().hex[:12]}@example.com"
    response = client.post("/v1/auth/signup", json={
        "email": email, "password": "Correct-Horse-Battery-9", "organization": "test",
    })
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="module")
def cluster(client, auth):
    """A cluster with one ingested snapshot, shaped like a real one."""
    response = client.post("/v1/clusters", json={"name": "api-test"}, headers=auth)
    assert response.status_code == 201, response.text
    created = response.json()
    cluster_id = created["cluster"]["id"]
    api_key = created["api_key"]

    def workload(name, *, config_maps=(), images=("repo/app:1.0",), ownership=None):
        return {
            "key": f"prod/Deployment/{name}", "name": name, "namespace": "prod",
            "kind": "Deployment", "replicas_desired": 2,
            "pod_labels": {"app": name}, "labels": {"app": name},
            "ownership": ownership if ownership is not None else {"team": "platform"},
            "images": list(images),
            "cpu_cores_used": 0.02, "cpu_cores_requested": 1.0,
            "memory_bytes_used": 100 * 1024 ** 2,
            "memory_bytes_requested": 1024 ** 3,
            "probes": {"containers": 1, "with_readiness": 0,
                       "with_liveness": 0, "with_startup": 0},
            "spread": {"topology_spread_constraints": 0, "topology_keys": [],
                       "anti_affinity_required": 0, "anti_affinity_preferred": 0,
                       "priority_class": None},
            "config_refs": {"config_maps": list(config_maps), "secrets": []},
        }

    snapshot = {
        "schema_version": 2,
        "agent_version": "0.2.0",
        "seq": 1,
        "captured_at": "2026-08-16T00:00:00+00:00",
        "cluster": {"uid": f"uid-{uuid.uuid4().hex[:12]}", "provider": "kind",
                    "kubernetes_version": "v1.35.0", "node_count": 3,
                    "metrics_available": True, "metrics_reason": None},
        "nodes": [
            {"name": f"node-{i}", "uid": f"n{i}", "labels": {},
             "allocatable_cpu_cores": 4.0,
             "allocatable_memory_bytes": 8 * 1024 ** 3,
             "capacity_cpu_cores": 4.0, "capacity_memory_bytes": 8 * 1024 ** 3,
             "ready": True, "unschedulable": False,
             "instance_type": "m5.xlarge", "region": "us-east-1", "zone": "us-east-1a"}
            for i in range(3)
        ],
        "workloads": [
            # legacy: unowned, mutable tag, three dependents. Should light up
            # every one of the new analyses.
            workload("legacy", images=["repo/legacy:latest"], ownership={},
                     config_maps=["shared"]),
            workload("api", config_maps=["shared"]),
            workload("worker", config_maps=["shared"]),
            workload("checkout"),
        ],
        "services": [{"name": "legacy", "namespace": "prod",
                      "selector": {"app": "legacy"}, "type": "ClusterIP",
                      "ports": [], "cluster_ip": "10.0.0.1", "uid": "s1"}],
        "pods": [
            {"name": f"legacy-{i}", "namespace": "prod", "node_name": "node-0",
             "labels": {"app": "legacy"}, "phase": "Running", "restart_count": 0,
             "waiting_reasons": [], "last_terminated_reasons": [], "ready": True}
            for i in range(2)
        ],
        "ingresses": [{"name": "public", "namespace": "prod",
                       "backends": [{"service": "legacy", "port": 80}]}],
        "network_policies": [],
        "disruption_budgets": [],
        "autoscalers": [],
        "edges": [
            {"source": f"prod/Deployment/{n}", "target": "prod/Deployment/legacy",
             "confidence": 0.9, "source_kind": "env_reference"}
            for n in ("api", "worker", "checkout")
        ] + [
            {"source": "prod/Ingress/public", "target": "prod/Deployment/legacy",
             "confidence": 1.0, "source_kind": "ingress"}
        ],
        "summary": {"nodes": 3, "workloads": 4, "services": 1, "pods": 2,
                    "edges": {"total": 4, "by_source": {}}},
    }

    # The agent authenticates with a Bearer API key, not a session token.
    ingest = client.post(
        "/v1/ingest", json=snapshot, headers={"Authorization": f"Bearer {api_key}"}
    )
    assert ingest.status_code in (200, 201, 202), ingest.text
    assert ingest.json().get("status") == "accepted", ingest.text
    return cluster_id


# --- auth -------------------------------------------------------------------


@pytest.mark.parametrize("path,method", [
    ("/v1/clusters/{id}/blast-radius", "get"),
    ("/v1/clusters/{id}/resilience", "get"),
    ("/v1/clusters/{id}/ownership", "get"),
    ("/v1/clusters/{id}/cost/safety", "get"),
    ("/v1/clusters/{id}/pr-review", "post"),
])
def test_endpoints_require_authentication(client, cluster, path, method):
    url = path.format(id=cluster)
    response = (
        client.post(url, json={"files": []}) if method == "post"
        else client.get(url)
    )

    assert response.status_code == 401


def test_another_tenants_cluster_is_not_readable(client, cluster):
    other = f"other-{uuid.uuid4().hex[:12]}@example.com"
    token = client.post("/v1/auth/signup", json={
        "email": other, "password": "Correct-Horse-Battery-9",
    }).json()["token"]

    response = client.get(
        f"/v1/clusters/{cluster}/resilience",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code in (403, 404)


# --- blast radius -----------------------------------------------------------


def test_blast_radius_ranking(client, cluster, auth):
    response = client.get(f"/v1/clusters/{cluster}/blast-radius", headers=auth)

    assert response.status_code == 200, response.text
    ranked = response.json()["ranked"]
    assert ranked[0]["workload_key"] == "prod/Deployment/legacy"
    assert ranked[0]["total_affected"] == 3
    assert ranked[0]["user_facing"] is True


def test_blast_radius_for_one_workload(client, cluster, auth):
    response = client.get(
        f"/v1/clusters/{cluster}/blast-radius",
        params={"workload": "prod/Deployment/legacy"}, headers=auth,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["exists"] is True
    assert len(body["direct_dependents"]) == 3
    assert "depends on" in body["headline"] or "depend on" in body["headline"]


def test_blast_radius_unknown_workload_is_404(client, cluster, auth):
    response = client.get(
        f"/v1/clusters/{cluster}/blast-radius",
        params={"workload": "prod/Deployment/nope"}, headers=auth,
    )

    assert response.status_code == 404
    assert "namespace/Kind/name" in response.json()["detail"]


# --- resilience and ownership ----------------------------------------------


def test_resilience_reports_findings_and_agent_capability(client, cluster, auth):
    response = client.get(f"/v1/clusters/{cluster}/resilience", headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    kinds = {f["kind"] for f in body["findings"]}
    assert "single_node_placement" in kinds
    assert "missing_readiness_probe" in kinds
    assert body["agent"]["schema_version"] == 2
    assert body["agent"]["up_to_date"] is True


def test_ownership_finds_the_orphan(client, cluster, auth):
    response = client.get(f"/v1/clusters/{cluster}/ownership", headers=auth)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["orphans"], "expected the unowned, unmanaged, latest-tagged workload"
    assert body["orphans"][0]["workload_key"] == "prod/Deployment/legacy"


def test_cost_safety_splits_claimed_from_safe(client, cluster, auth):
    response = client.get(f"/v1/clusters/{cluster}/cost/safety", headers=auth)

    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    assert "claimed_monthly_usd" in summary
    assert summary["claimed_monthly_usd"] >= summary["safe_monthly_usd"]


# --- pull request review ----------------------------------------------------


LEGACY_BEFORE = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: legacy
  namespace: prod
spec:
  replicas: 2
  selector:
    matchLabels:
      app: legacy
  template:
    metadata:
      labels:
        app: legacy
    spec:
      containers:
        - name: app
          image: repo/legacy:latest
""".strip()


def test_pr_review_end_to_end(client, cluster, auth):
    response = client.post(
        f"/v1/clusters/{cluster}/pr-review",
        json={
            "files": [{
                "path": "k8s/legacy.yaml",
                "before": LEGACY_BEFORE,
                "after": LEGACY_BEFORE.replace("replicas: 2", "replicas: 1"),
            }],
            "render_markdown": True,
        },
        headers=auth,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["verdict"] == "critical"
    impact = body["impacts"][0]
    assert impact["resolved_in_cluster"] is True
    assert impact["dependents"] == 4
    assert impact["user_facing"] is True
    assert "Blast radius" in body["markdown"]


def test_pr_review_with_no_files(client, cluster, auth):
    response = client.post(
        f"/v1/clusters/{cluster}/pr-review", json={"files": []}, headers=auth
    )

    assert response.status_code == 200, response.text
    assert response.json()["verdict"] == "none"


def test_pr_review_rejects_a_malformed_body(client, cluster, auth):
    response = client.post(
        f"/v1/clusters/{cluster}/pr-review", json={"files": "not-a-list"}, headers=auth
    )

    assert response.status_code == 422


def test_pr_review_namespace_resolution_over_http(client, cluster, auth):
    """A manifest with no namespace still resolves against the live cluster."""
    bare = LEGACY_BEFORE.replace("  namespace: prod\n", "")
    response = client.post(
        f"/v1/clusters/{cluster}/pr-review",
        json={"files": [{"path": "k8s/legacy.yaml", "before": bare,
                         "after": bare.replace("replicas: 2", "replicas: 1")}]},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    impact = response.json()["impacts"][0]
    assert impact["namespace_used"] == "prod"
    assert impact["resolved_in_cluster"] is True


def test_cluster_without_a_snapshot_returns_409(client, auth):
    created = client.post(
        "/v1/clusters", json={"name": "empty"}, headers=auth
    ).json()
    response = client.get(
        f"/v1/clusters/{created['cluster']['id']}/resilience", headers=auth
    )

    # A cluster with no snapshot must not be reported as a healthy one.
    assert response.status_code == 409
    assert "agent" in response.json()["detail"].lower()
