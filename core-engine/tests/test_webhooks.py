"""
GitHub webhook receiver.

Signature verification is the whole security model for this endpoint -- it is
unauthenticated by necessity, since GitHub will not send a bearer token -- so
most of these tests are about refusing things.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

import pytest

from src.routers.webhooks import ANALYSED_ACTIONS, verify_signature

SECRET = "test-webhook-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    return SECRET


# --- signature verification -------------------------------------------------


def test_valid_signature_passes(secret):
    body = b'{"action":"opened"}'

    assert verify_signature(body, _sign(body)) is True


def test_wrong_secret_fails(secret):
    body = b'{"action":"opened"}'

    assert verify_signature(body, _sign(body, "not-the-secret")) is False


def test_tampered_body_fails(secret):
    """The signature covers the body; changing one byte must invalidate it."""
    signature = _sign(b'{"action":"opened"}')

    assert verify_signature(b'{"action":"closed"}', signature) is False


@pytest.mark.parametrize("signature", [
    None, "", "deadbeef", "sha1=abc", "sha256=", "SHA256=" + "0" * 64,
])
def test_malformed_signatures_fail(secret, signature):
    assert verify_signature(b"{}", signature) is False


def test_no_secret_configured_fails_closed(monkeypatch):
    """
    Absent a secret this endpoint would be an unauthenticated remote trigger.
    It must refuse, not accept everything.
    """
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    body = b"{}"

    assert verify_signature(body, _sign(body)) is False


def test_blank_secret_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "   ")

    assert verify_signature(b"{}", _sign(b"{}")) is False


def test_analysed_actions_cover_pushes_to_the_branch():
    """
    `synchronize` is what keeps the comment matching the current code rather
    than the first commit. Omitting it makes the analysis stale on every push.
    """
    assert "synchronize" in ANALYSED_ACTIONS
    assert "opened" in ANALYSED_ACTIONS
    assert "ready_for_review" in ANALYSED_ACTIONS
    # Not analysed: these change no manifest.
    assert not {"labeled", "assigned", "edited", "closed"} & ANALYSED_ACTIONS


# --- endpoint behaviour -----------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set"
)


@pytest.fixture(scope="session")
def client(api_client):
    """Shared with every other HTTP suite; see conftest.api_client."""
    return api_client


def _post(client, payload: dict, *, event="pull_request", secret=SECRET, sign=True):
    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if sign:
        headers["X-Hub-Signature-256"] = _sign(body, secret)
    return client.post("/v1/webhooks/github", content=body, headers=headers)


def _pull_request(action="opened", *, repo="acme/platform", number=7, draft=False):
    return {
        "action": action,
        "number": number,
        "pull_request": {"number": number, "draft": draft, "title": "Scale down api"},
        "repository": {"full_name": repo},
    }


@pytestmark_db
def test_unsigned_delivery_is_rejected(client, secret):
    response = _post(client, _pull_request(), sign=False)

    assert response.status_code == 401


@pytestmark_db
def test_forged_signature_is_rejected(client, secret):
    response = _post(client, _pull_request(), secret="attacker-guess")

    assert response.status_code == 401


@pytestmark_db
def test_missing_secret_returns_503_not_200(client, monkeypatch):
    """A misconfigured deployment must be loud, not silently permissive."""
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    response = _post(client, _pull_request(), sign=False)

    assert response.status_code == 503
    assert "GITHUB_WEBHOOK_SECRET" in response.json()["detail"]


@pytestmark_db
def test_ping_is_acknowledged(client, secret):
    response = _post(client, {"zen": "Design for failure."}, event="ping")

    assert response.status_code == 200
    assert response.json()["status"] == "pong"


@pytestmark_db
def test_unrelated_event_is_ignored(client, secret):
    response = _post(client, {"action": "created"}, event="issue_comment")

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytestmark_db
@pytest.mark.parametrize("action", ["labeled", "assigned", "closed", "edited"])
def test_irrelevant_actions_are_ignored(client, secret, action):
    response = _post(client, _pull_request(action))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytestmark_db
def test_draft_pull_request_is_ignored(client, secret):
    response = _post(client, _pull_request("opened", draft=True))

    assert response.status_code == 200
    assert response.json()["reason"] == "draft"


@pytestmark_db
def test_draft_marked_ready_is_analysed(client, secret):
    """
    Still flagged draft in the payload, but the action says otherwise. This
    is the moment the author asked for review.
    """
    response = _post(client, _pull_request("ready_for_review", draft=True))

    assert response.status_code == 200
    # No cluster is linked to this repo in the test database, so it stops
    # there -- but for the right reason, not because it was treated as a draft.
    assert response.json().get("reason") != "draft"


@pytestmark_db
def test_unlinked_repository_is_ignored_with_200(client, secret):
    """
    A non-2xx would make GitHub retry forever for a condition that never
    resolves on its own.
    """
    response = _post(client, _pull_request(repo="acme/never-linked"))

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert "no cluster is linked" in response.json()["reason"]


@pytestmark_db
def test_malformed_payload_is_rejected(client, secret):
    response = _post(client, {"action": "opened", "repository": {}})

    assert response.status_code == 400


@pytestmark_db
def test_linked_repository_is_accepted(client, secret):
    """End to end: link a repo to a cluster, then deliver a pull request."""
    import uuid

    email = f"wh-{uuid.uuid4().hex[:12]}@example.com"
    token = client.post("/v1/auth/signup", json={
        "email": email, "password": "Correct-Horse-Battery-9",
    }).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}

    cluster_id = client.post(
        "/v1/clusters", json={"name": "wh-test"}, headers=auth
    ).json()["cluster"]["id"]

    repo = f"acme/repo-{uuid.uuid4().hex[:8]}"
    link = client.put(
        f"/v1/clusters/{cluster_id}/repository",
        json={"repository": repo}, headers=auth,
    )
    assert link.status_code == 200, link.text

    response = _post(client, _pull_request(repo=repo))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["cluster_id"] == cluster_id
    assert body["pull_request"] == 7


@pytestmark_db
def test_repository_must_be_owner_slash_name(client, secret):
    import uuid

    email = f"wh2-{uuid.uuid4().hex[:12]}@example.com"
    token = client.post("/v1/auth/signup", json={
        "email": email, "password": "Correct-Horse-Battery-9",
    }).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    cluster_id = client.post(
        "/v1/clusters", json={"name": "wh2"}, headers=auth
    ).json()["cluster"]["id"]

    response = client.put(
        f"/v1/clusters/{cluster_id}/repository",
        json={"repository": "https://github.com/acme/platform"}, headers=auth,
    )

    assert response.status_code == 400
    assert "owner/name" in response.json()["detail"]


@pytestmark_db
def test_repository_link_requires_auth(client, secret):
    response = client.put(
        "/v1/clusters/00000000-0000-0000-0000-000000000000/repository",
        json={"repository": "acme/x"},
    )

    assert response.status_code == 401
