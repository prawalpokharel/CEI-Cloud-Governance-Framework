"""
GitHub webhook receiver.

Turns the pull-request analysis from something a CI job has to call into
something that happens on its own. That difference decides whether the check
gets used: a step someone has to add to every repository's workflow is a step
most repositories never get.

## Signature verification is the whole security model

This endpoint is unauthenticated by necessity -- GitHub will not send a bearer
token -- so the HMAC signature is the only thing separating a real delivery
from anyone who knows the URL. Every request is verified before its body is
parsed, in constant time, and an unverified payload is never allowed to select
a cluster or reach the analysis.

Without a configured secret the endpoint refuses outright rather than
accepting everything. A webhook that processes unsigned payloads is an
unauthenticated remote trigger against a customer's infrastructure data.

## Responding before working

GitHub expects a response within 10 seconds and retries what it considers a
failure. The analysis fetches every changed manifest at two commits, which is
comfortably slower than that on a large pull request, so the work is handed to
a background task and the delivery is acknowledged immediately. Doing it
inline produces duplicate comments from retried deliveries.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os

from fastapi import (
    APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request,
)
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_session, get_session_factory
from ..db.models import Cluster, Snapshot
from ..services import pr_review as pr_review_service
from ..services.git_provider import GitHubApp, GitProviderError
from ..services.live_cei import compute_live_cei

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

# Only these actions warrant analysis. "opened" and "reopened" are obvious;
# "synchronize" fires on every push to the branch, which is what keeps the
# comment matching the code rather than the first commit. Everything else --
# labels, assignees, review requests, edits to the description -- changes no
# manifest.
ANALYSED_ACTIONS = {"opened", "reopened", "synchronize", "ready_for_review"}


def webhook_secret() -> str | None:
    return (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").strip() or None


def verify_signature(body: bytes, signature: str | None) -> bool:
    """
    Constant-time HMAC-SHA256 check of GitHub's X-Hub-Signature-256.

    Returns False for a missing secret rather than raising, so the caller
    decides the status code. `compare_digest` rather than `==`: the timing of
    a byte-wise comparison leaks how much of a forged signature was correct.
    """
    secret = webhook_secret()
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature[len("sha256="):])


async def _cluster_for_repository(
    session: AsyncSession, repo: str
) -> Cluster | None:
    """
    Which cluster a repository's manifests describe.

    Matched on the cluster's configured repository. Deliberately not "the
    tenant's only cluster": analysing a pull request against the wrong
    cluster's dependency graph produces confident, entirely fictional impact,
    which is worse than declining to analyse it.
    """
    clusters = (
        await session.execute(select(Cluster).where(Cluster.repository == repo))
    ).scalars().all()
    if len(clusters) == 1:
        return clusters[0]
    if len(clusters) > 1:
        log.warning(
            "Repository %s is linked to %d clusters; skipping analysis because "
            "the correct dependency graph is ambiguous.", repo, len(clusters),
        )
    return None


async def _analyse(cluster_id, repo: str, number: int, session_factory) -> None:
    """Run the review and publish it. Never raises into the request cycle."""
    try:
        async with session_factory() as session:
            snapshot_row = (
                await session.execute(
                    select(Snapshot)
                    .where(Snapshot.cluster_id == cluster_id)
                    .order_by(desc(Snapshot.received_at))
                    .limit(1)
                )
            ).scalar_one_or_none()

        if snapshot_row is None:
            log.warning(
                "No snapshot for cluster %s; skipping %s#%s. Impact cannot be "
                "measured without a dependency graph.", cluster_id, repo, number,
            )
            return

        snapshot = snapshot_row.payload or {}
        cei = {n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes}

        result = pr_review_service.review_pull_request(
            GitHubApp(), repo, number, snapshot, cei,
        )
        log.info(
            "Reviewed %s#%s: verdict=%s, %d object(s), %d workload(s) reached",
            repo, number, result["verdict"],
            result["summary"]["objects_changed"],
            result["summary"]["workloads_touched"],
        )
    except GitProviderError as exc:
        # An App without permission on this repository, or an installation
        # that was removed. Logged, not retried: GitHub already delivered
        # successfully and the fault is on our side.
        log.warning("Could not review %s#%s: %s", repo, number, exc)
    except Exception:
        log.exception("Unexpected failure reviewing %s#%s", repo, number)


@router.post("/github")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
):
    """
    Receive a GitHub delivery and analyse pull requests against the cluster.

    Always 200 for a verified delivery, even when nothing is analysed. GitHub
    treats a non-2xx as a failure and retries it, and a repository with no
    linked cluster would otherwise generate a retry storm for a condition that
    will never resolve on its own.
    """
    body = await request.body()

    if not webhook_secret():
        # Refusing beats accepting: without a secret this is an
        # unauthenticated remote trigger against customer infrastructure data.
        raise HTTPException(
            status_code=503,
            detail=(
                "GITHUB_WEBHOOK_SECRET is not configured. This endpoint will "
                "not process unsigned payloads."
            ),
        )

    if not verify_signature(body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event {x_github_event!r}"}

    # Parsed only after the signature holds.
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Body is not valid JSON")

    action = payload.get("action")
    if action not in ANALYSED_ACTIONS:
        return {"status": "ignored", "reason": f"action {action!r}"}

    pull = payload.get("pull_request") or {}
    repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
    number = pull.get("number")
    if not repo or not isinstance(number, int):
        raise HTTPException(status_code=400, detail="Malformed pull_request payload")

    # A draft is explicitly not ready for review. Analysing it produces a
    # comment nobody asked for on work still in progress; "ready_for_review"
    # is in ANALYSED_ACTIONS so it is picked up the moment that changes.
    if pull.get("draft") and action != "ready_for_review":
        return {"status": "ignored", "reason": "draft"}

    cluster = await _cluster_for_repository(session, repo)
    if cluster is None:
        return {
            "status": "ignored",
            "reason": (
                f"no cluster is linked to {repo}. Set the repository on a "
                "cluster to enable blast-radius analysis."
            ),
        }

    background.add_task(
        _analyse, cluster.id, repo, number, get_session_factory()
    )
    return {
        "status": "accepted",
        "repository": repo,
        "pull_request": number,
        "cluster_id": str(cluster.id),
    }
