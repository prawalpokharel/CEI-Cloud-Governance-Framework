"""
Compat routes: the legacy-backend contract, served locally.

The property that matters most: /api/demo/* are ALIASES of the scenario
routes -- same handlers, so the NIW demonstration numbers cannot diverge
between the two paths.
"""

import pytest
from fastapi.testclient import TestClient

import src.main
from src.services import compat_cloud


@pytest.fixture(scope="module")
def client():
    with TestClient(src.main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_connections():
    compat_cloud._connected.clear()
    yield
    compat_cloud._connected.clear()


# --- demo aliases -----------------------------------------------------------


def test_demo_scenarios_is_an_alias_of_scenarios_list(client):
    assert client.get("/api/demo/scenarios").json() == client.get("/scenarios/list").json()


def test_demo_scenario_detail_aliases(client):
    scenario_id = client.get("/scenarios/list").json()["scenarios"][0]["scenario_id"]

    assert (
        client.get(f"/api/demo/scenarios/{scenario_id}").json()
        == client.get(f"/scenarios/{scenario_id}").json()
    )


def test_demo_analyze_matches_the_scenario_route(client):
    """The NIW numbers must be identical through either path."""
    scenario_id = client.get("/scenarios/list").json()["scenarios"][0]["scenario_id"]
    compat = client.post(f"/api/demo/scenarios/{scenario_id}/analyze").json()
    direct = client.post(f"/scenarios/{scenario_id}/analyze").json()

    assert compat["analysis"]["weights"] == direct["analysis"]["weights"]
    assert len(compat["analysis"]["nodes"]) == len(direct["analysis"]["nodes"])


def test_demo_unknown_scenario_is_404(client):
    assert client.get("/api/demo/scenarios/nope").status_code == 404


# --- cloud demo flow --------------------------------------------------------


def test_providers_carry_the_mock_badge(client):
    providers = client.get("/api/cloud/providers").json()["providers"]

    assert {p["key"] for p in providers} == {"aws", "gcp", "azure"}
    assert all(p["mock"] is True for p in providers)


def test_connect_flow_round_trip(client):
    assert client.get("/api/cloud/status").json()["connected"] == []

    response = client.get("/api/cloud/auth/aws", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("/connect")

    connected = client.get("/api/cloud/status").json()["connected"]
    assert connected[0]["provider"] == "aws"
    assert connected[0]["mock"] is True

    client.post("/api/cloud/disconnect/aws")
    assert client.get("/api/cloud/status").json()["connected"] == []


def test_unknown_provider_is_404_not_a_connection(client):
    assert client.get("/api/cloud/auth/ibm", follow_redirects=False).status_code == 404
    assert client.get("/api/cloud/status").json()["connected"] == []


def test_topology_is_labelled_and_render_ready(client):
    topology = client.get("/api/cloud/topology/aws").json()

    assert topology["mock"] is True
    assert "local demo" in topology["source"]
    node = topology["topology"]["nodes"][0]
    # The connect page renders n.id and n.tier per node pill.
    assert "id" in node and "tier" in node


def test_topology_is_deterministic(client):
    a = client.get("/api/cloud/topology/gcp").json()["topology"]
    b = client.get("/api/cloud/topology/gcp").json()["topology"]

    assert [n["id"] for n in a["nodes"]] == [n["id"] for n in b["nodes"]]
    assert a["nodes"][0]["metrics"] == b["nodes"][0]["metrics"]


def test_cloud_analyze_is_real_pipeline_over_sample_data(client):
    result = client.post("/api/cloud/analyze/azure").json()

    analysis = result["analysis"]
    assert set(analysis["weights"]) >= {"alpha", "beta", "gamma"}
    assert "suppression_active" in analysis["oscillation_status"]
    assert len(analysis["nodes"]) == len(result["topology"]["topology"]["nodes"])
    # The pipeline actually ranked something: scores are not all equal.
    scores = {round(n["cei_score"], 4) for n in analysis["nodes"]}
    assert len(scores) > 1
