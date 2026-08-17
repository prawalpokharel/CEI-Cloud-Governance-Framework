"""
Dashboard API: signup, login, clusters, API keys, topology.

Backs the /app frontend. Separate from the ingest router because the callers
and threat models differ -- browsers with sessions here, agents with API keys
there.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_session
from ..db.models import (
    ActorType,
    ApiKey,
    Cluster,
    ImageScan,
    ImageVulnerability,
    Snapshot,
    Tenant,
    User,
    WorkloadSample,
)
from ..services import audit
from ..services import drift_store
from ..services import availability as availability_service
from ..services import control_plane as control_plane_service
from ..services import external_deps as external_deps_service
from ..services import fleet as fleet_service
from ..services import recovery as recovery_service
from ..services import remediation as remediation_service
from ..services import prescribe as prescribe_service
from ..services import carbon as carbon_service

from ..services.git_provider import GitHubApp, GitProviderError
from ..services.cost import analyze_cluster_cost
from ..services.health import diagnose
from ..services import blast_radius as blast_radius_service
from ..services import ownership as ownership_service
from ..services import pr_review as pr_review_service
from ..services import resilience as resilience_service
from ..services import safe_to_delete as safe_to_delete_service
from ..services.live_cei import CentralityMode, _history_for, compute_live_cei
from ..services.network import analyze_segmentation, generate_all_policies
from ..services.policy import plan_remediation
from ..services.vulnerability import base_image_recommendations, prioritize
from ..services.security import (
    SecretNotConfigured,
    generate_api_key,
    hash_password,
    issue_session,
    password_problems,
    read_session,
    verify_password,
)

router = APIRouter(prefix="/v1", tags=["dashboard"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

# Highest agent snapshot schema this server knows how to use in full.
SNAPSHOT_SCHEMA_SUPPORTED = 2


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str | None = None
    organization: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class CreateClusterRequest(BaseModel):
    name: str


class LinkRepositoryRequest(BaseModel):
    # "owner/name". None clears the link.
    repository: str | None = None


class RemediationRequest(BaseModel):
    workload_key: str
    # Defaults to the cluster's linked repository.
    repository: str | None = None


class ManifestFile(BaseModel):
    path: str
    # Both sides of the change. Either may be absent: a new file has no
    # `before`, a deleted one no `after`. Whole documents rather than a patch
    # -- a `replicas:` line in a hunk says nothing about which object in a
    # multi-document file it belongs to.
    before: str | None = None
    after: str | None = None


class PullRequestReviewRequest(BaseModel):
    files: list[ManifestFile]
    # Manifests routinely omit namespace and get it from kustomize or the
    # apply command. Guessing "default" silently would resolve nothing on a
    # cluster that uses real namespaces, so the caller can say.
    default_namespace: str = "default"
    render_markdown: bool = False


# --------------------------------------------------------------------------
# Session dependency
# --------------------------------------------------------------------------

async def current_user(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(None, 1)[1].strip()
    try:
        claims = read_session(token)
    except SecretNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    if not claims:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = (
        await session.execute(select(User).where(User.id == claims["sub"]))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    return user


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

@router.post("/auth/signup", status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    problems = password_problems(body.password)
    if problems:
        raise HTTPException(status_code=400, detail="; ".join(problems))

    # One tenant per signup. Inviting teammates into an existing tenant is a
    # later concern; giving every account a tenant now means no migration
    # when it arrives.
    tenant = Tenant(
        name=body.organization or email.split("@")[0],
        slug=f"{email.split('@')[0][:40]}-{datetime.now(timezone.utc).timestamp():.0f}",
    )
    session.add(tenant)
    await session.flush()

    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(body.password),
        name=body.name,
    )
    session.add(user)

    await audit.record(
        session,
        action="user.signup",
        actor_type=ActorType.user,
        actor_id=email,
        tenant_id=tenant.id,
        target_type="user",
        source_ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="An account with that email already exists")

    try:
        token = issue_session(user.id, tenant.id)
    except SecretNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "token": token,
        "user": {"email": user.email, "name": user.name},
        "tenant": {"id": str(tenant.id), "name": tenant.name},
    }


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    email = body.email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    # Same response whether the account is unknown or the password is wrong,
    # so this endpoint cannot be used to enumerate registered emails.
    if user is None or not verify_password(body.password, user.password_hash):
        await audit.record(
            session,
            action="user.login_failed",
            actor_type=ActorType.user,
            actor_id=email,
            tenant_id=user.tenant_id if user else None,
            source_ip=audit.client_ip(request),
        )
        await session.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user.last_login_at = datetime.now(timezone.utc)
    await audit.record(
        session,
        action="user.login",
        actor_type=ActorType.user,
        actor_id=email,
        tenant_id=user.tenant_id,
        source_ip=audit.client_ip(request),
    )
    await session.commit()

    try:
        token = issue_session(user.id, user.tenant_id)
    except SecretNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {"token": token, "user": {"email": user.email, "name": user.name}}


@router.get("/auth/me")
async def me(user: User = Depends(current_user)):
    return {"email": user.email, "name": user.name, "tenant_id": str(user.tenant_id)}


# --------------------------------------------------------------------------
# Clusters
# --------------------------------------------------------------------------

# An agent reports on a fixed interval, so silence for several intervals means
# it is gone -- crashed, evicted, network-partitioned, or uninstalled. Three
# intervals tolerates one missed cycle plus retry backoff without flapping.
STALE_AFTER_SECONDS = 180


def _connection_state(last_seen_at) -> tuple[str, int | None]:
    """
    Return (state, seconds_since_last_report).

    "connected" previously meant "has reported at least once, ever", so a
    cluster whose agent died days ago still displayed as healthy. That is the
    opposite of useful: the entire value of a monitoring agent is knowing when
    it stops reporting.
    """
    if last_seen_at is None:
        return "never_connected", None
    age = (datetime.now(timezone.utc) - last_seen_at).total_seconds()
    return ("connected" if age <= STALE_AFTER_SECONDS else "stale"), int(age)


@router.get("/clusters")
async def list_clusters(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(Cluster)
            .where(Cluster.tenant_id == user.tenant_id)
            .order_by(Cluster.created_at)
        )
    ).scalars().all()

    out = []
    for cluster in rows:
        latest = (
            await session.execute(
                select(Snapshot)
                .where(Snapshot.cluster_id == cluster.id)
                .order_by(desc(Snapshot.received_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        state, age = _connection_state(cluster.last_seen_at)
        out.append({
            "id": str(cluster.id),
            "name": cluster.name,
            "provider": cluster.provider.value,
            "k8s_version": cluster.k8s_version,
            "agent_version": cluster.agent_version,
            "metrics_available": cluster.metrics_available,
            "last_seen_at": cluster.last_seen_at.isoformat() if cluster.last_seen_at else None,
            "state": state,
            "seconds_since_report": age,
            "connected": state == "connected",
            "workload_count": latest.workload_count if latest else 0,
            "node_count": latest.node_count if latest else 0,
            "pod_count": latest.pod_count if latest else 0,
        })
    return {"clusters": out}


@router.post("/clusters", status_code=201)
async def create_cluster(
    body: CreateClusterRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Register a cluster and mint its agent key.

    The key is returned exactly once, in this response. It is stored only as
    a hash, so it genuinely cannot be shown again -- the UI must make that
    clear at the moment of creation.
    """
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Cluster name is required")

    cluster = Cluster(tenant_id=user.tenant_id, name=name)
    session.add(cluster)
    await session.flush()

    full_key, handle, key_hash = generate_api_key()
    session.add(
        ApiKey(
            tenant_id=user.tenant_id,
            cluster_id=cluster.id,
            prefix=handle,
            key_hash=key_hash,
            label=f"agent key for {name}",
            created_by_user_id=user.id,
        )
    )

    await audit.record(
        session,
        action="api_key.created",
        actor_type=ActorType.user,
        actor_id=user.email,
        tenant_id=user.tenant_id,
        target_type="cluster",
        target_id=str(cluster.id),
        source_ip=audit.client_ip(request),
        details={"cluster_name": name, "key_prefix": handle},
    )
    await session.commit()

    return {
        "cluster": {"id": str(cluster.id), "name": cluster.name},
        "api_key": full_key,
        "api_key_prefix": handle,
        "warning": "This key is shown once and cannot be retrieved later.",
    }


def _agent_capability(snapshot: dict) -> dict:
    """
    What the agent that produced this snapshot was able to report.

    Analyses added after an agent ships see empty collections from older
    agents. Returning zero findings for those is indistinguishable from a
    clean cluster, so every response that depends on a newer schema says which
    schema it got and what is consequently missing.
    """
    version = snapshot.get("schema_version") or 1
    missing = []
    if "disruption_budgets" not in snapshot:
        missing.append("PodDisruptionBudgets")
    if "autoscalers" not in snapshot:
        missing.append("HorizontalPodAutoscalers")
    if not any(
        "probes" in workload for workload in (snapshot.get("workloads") or [])
    ):
        missing.append("probe coverage, spread policy, ownership, config references")

    return {
        "agent_version": snapshot.get("agent_version"),
        "schema_version": version,
        "schema_supported": SNAPSHOT_SCHEMA_SUPPORTED,
        "up_to_date": version >= SNAPSHOT_SCHEMA_SUPPORTED,
        "missing_signals": missing,
        "note": (
            None
            if not missing
            else (
                f"This snapshot is schema v{version}; the server understands "
                f"v{SNAPSHOT_SCHEMA_SUPPORTED}. Not reported by this agent: "
                + "; ".join(missing)
                + ". Findings that depend on them are absent rather than clean "
                "— upgrade the agent and its ClusterRole."
            )
        ),
    }


async def _latest_snapshot(session: AsyncSession, cluster_id) -> Snapshot:
    """Most recent snapshot, or a 409 explaining that none has arrived."""
    latest = (
        await session.execute(
            select(Snapshot)
            .where(Snapshot.cluster_id == cluster_id)
            .order_by(desc(Snapshot.received_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(
            status_code=409,
            detail="No snapshot received yet. Is the agent installed and running?",
        )
    return latest


async def _owned_cluster(cluster_id: str, user: User, session: AsyncSession) -> Cluster:
    cluster = (
        await session.execute(
            select(Cluster).where(
                Cluster.id == cluster_id, Cluster.tenant_id == user.tenant_id
            )
        )
    ).scalar_one_or_none()
    # 404 rather than 403 for a cluster owned by someone else: a 403 would
    # confirm that the id exists.
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return cluster


@router.get("/clusters/{cluster_id}/topology")
async def cluster_topology(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Latest snapshot, reshaped for the topology map."""
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = (
        await session.execute(
            select(Snapshot)
            .where(Snapshot.cluster_id == cluster.id)
            .order_by(desc(Snapshot.received_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    if latest is None:
        return {
            "cluster": {"id": str(cluster.id), "name": cluster.name, "connected": False},
            "workloads": [],
            "edges": [],
            "message": "No snapshot received yet. Is the agent installed and running?",
        }

    payload = latest.payload or {}
    return {
        "cluster": {
            "id": str(cluster.id),
            "name": cluster.name,
            "provider": cluster.provider.value,
            "k8s_version": cluster.k8s_version,
            "metrics_available": cluster.metrics_available,
            "metrics_reason": (payload.get("cluster") or {}).get("metrics_reason"),
            "connected": True,
            "last_seen_at": cluster.last_seen_at.isoformat() if cluster.last_seen_at else None,
        },
        "captured_at": latest.captured_at.isoformat(),
        "seq": latest.seq,
        "nodes": payload.get("nodes", []),
        "workloads": payload.get("workloads", []),
        "services": payload.get("services", []),
        "pods": payload.get("pods", []),
        "edges": payload.get("edges", []),
        "summary": payload.get("summary", {}),
    }


@router.get("/clusters/{cluster_id}/cei")
async def cluster_cei(
    cluster_id: str,
    mode: str = "blast_radius",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    CEI over the cluster's latest snapshot.

    Entropy is computed from accumulated workload_samples, not from a
    fabricated series. Until enough history exists the term is withheld and
    its weight redistributed, and the response says so.
    """
    cluster = await _owned_cluster(cluster_id, user, session)

    try:
        centrality_mode = CentralityMode(mode)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown centrality mode {mode!r}. Valid values: "
                f"{', '.join(m.value for m in CentralityMode)}"
            ),
        )

    latest = (
        await session.execute(
            select(Snapshot)
            .where(Snapshot.cluster_id == cluster.id)
            .order_by(desc(Snapshot.received_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        raise HTTPException(
            status_code=409,
            detail="No snapshot received yet. Is the agent installed and running?",
        )

    # Ordered oldest-first: entropy is computed over a time series, and the
    # binning is order-independent but the stability monitor's windowing is
    # not.
    rows = (
        await session.execute(
            select(WorkloadSample)
            .where(WorkloadSample.cluster_id == cluster.id)
            .order_by(WorkloadSample.observed_at)
        )
    ).scalars().all()

    history: dict[str, list[dict]] = {}
    for row in rows:
        history.setdefault(row.workload_key, []).append({
            "cpu_cores_used": row.cpu_cores_used,
            "cpu_cores_requested": row.cpu_cores_requested,
            "mem_bytes_used": row.mem_bytes_used,
            "mem_bytes_requested": row.mem_bytes_requested,
        })

    result = compute_live_cei(
        latest.payload or {},
        {k: _history_for(v) for k, v in history.items()},
        centrality_mode=centrality_mode,
    )
    payload = result.to_dict()
    payload["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    payload["captured_at"] = latest.captured_at.isoformat()
    return payload


@router.put("/clusters/{cluster_id}/repository")
async def link_repository(
    cluster_id: str,
    body: LinkRepositoryRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Link the repository whose manifests describe this cluster.

    What lets an inbound GitHub webhook find the right dependency graph.
    Without it the webhook declines to analyse rather than guessing, because
    a pull request measured against the wrong cluster produces impact numbers
    that are confident and entirely fictional.
    """
    cluster = await _owned_cluster(cluster_id, user, session)

    repository = (body.repository or "").strip() or None
    if repository is not None and not re.match(r"^[\w.-]+/[\w.-]+$", repository):
        raise HTTPException(
            status_code=400,
            detail="Repository must be in 'owner/name' form, e.g. acme/platform",
        )

    cluster.repository = repository
    await audit.record(
        session,
        action="cluster.repository_linked",
        actor_type=ActorType.user,
        actor_id=user.email,
        tenant_id=user.tenant_id,
        target_type="cluster",
        target_id=str(cluster.id),
        details={"repository": repository},
    )
    await session.commit()
    return {"cluster": {"id": str(cluster.id), "name": cluster.name},
            "repository": repository}


@router.get("/clusters/{cluster_id}/drift")
async def cluster_drift(
    cluster_id: str,
    limit: int = 50,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Structural events detected between consecutive snapshots.

    Written by the ingest rail, so this endpoint only reads. The concentration
    trend comes from the per-snapshot column rather than from payloads.
    """
    cluster = await _owned_cluster(cluster_id, user, session)

    events = await drift_store.latest_events(
        session, cluster.id, limit=max(1, min(limit, 200))
    )

    trend_rows = (
        await session.execute(
            select(Snapshot.captured_at, Snapshot.structural_concentration)
            .where(
                Snapshot.cluster_id == cluster.id,
                Snapshot.structural_concentration.is_not(None),
            )
            .order_by(desc(Snapshot.captured_at))
            .limit(200)
        )
    ).all()

    return {
        "cluster": {"id": str(cluster.id), "name": cluster.name},
        "events": events,
        "concentration_trend": [
            {"captured_at": captured.isoformat(), "concentration": value}
            for captured, value in reversed(trend_rows)
        ],
        "notification_policy": {
            "notified_kinds": sorted(drift_store.NOTIFY_KINDS),
            "severity": "critical only",
            "debounce_hours": drift_store.DEBOUNCE_HOURS,
        },
    }


@router.get("/clusters/{cluster_id}/external-dependencies")
async def cluster_external_dependencies(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    The graph below the cluster: managed services and SaaS, with the DCI.

    Uses the egress summary embedded in the snapshot when the agent runs
    Hubble; degrades to internal-only with an explicit reason otherwise.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = external_deps_service.analyze(
        snapshot, snapshot.get("egress"), None, cei_by_workload
    )
    if not result.get("available"):
        # Egress-less clusters still get the internal-only concentration
        # number rather than nothing.
        result["dci"] = external_deps_service.dependency_concentration_index(snapshot)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/recovery")
async def cluster_recovery(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Recovery-amplification risk: where reconnect storms will concentrate."""
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = recovery_service.analyze(snapshot, cei_by_workload)
    result["agent"] = _agent_capability(snapshot)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/availability")
async def cluster_availability(
    cluster_id: str,
    trials: int = 20000,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Effective availability under correlated failure, vs the naive product.

    The gap between the two columns is the cost of the correlation -- what
    the architecture actually buys vs what the component SLAs imply.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}

    external = {}
    egress = snapshot.get("egress")
    if egress and egress.get("available"):
        external = external_deps_service.build_external_nodes(snapshot, egress)

    result = availability_service.simulate(
        snapshot, external=external, trials=max(1000, min(trials, 100_000))
    )
    if result.get("available"):
        result["correlation_cost"] = availability_service.correlation_cost(result)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/control-plane")
async def cluster_control_plane(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Static-stability audit: what recovery needs that steady state does not."""
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = control_plane_service.analyze(snapshot, cei_by_workload)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/fleet/convergence")
async def fleet_convergence(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    External dependencies shared across the tenant's clusters.

    The multi-cloud independence question: dependencies reached from clusters
    on different providers are one failure domain wearing two logos.
    """
    clusters = (
        await session.execute(
            select(Cluster).where(Cluster.tenant_id == user.tenant_id)
        )
    ).scalars().all()

    fleet = []
    for cluster in clusters:
        latest = (
            await session.execute(
                select(Snapshot)
                .where(Snapshot.cluster_id == cluster.id)
                .order_by(desc(Snapshot.received_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest is None:
            continue
        fleet.append({
            "name": cluster.name,
            "provider": cluster.provider.value if cluster.provider else "unknown",
            "snapshot": latest.payload or {},
        })

    result = fleet_service.analyze(fleet)
    # Second-order concentration: the dependency's own substrate. Assumed
    # public knowledge, marked as such, appended after the observed findings.
    if result.get("available"):
        result["findings"].extend(fleet_service.substrate_overlaps(fleet))
    result["clusters_without_snapshots"] = len(clusters) - len(fleet)
    return result


@router.get("/clusters/{cluster_id}/remediations")
async def cluster_remediations(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Remediations this cluster warrants, with the exact manifests that would
    be proposed. Read-only preview; opening the PR is a separate POST.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    proposals = remediation_service.plan(snapshot, cei_by_workload)
    return {
        "cluster": {"id": str(cluster.id), "name": cluster.name},
        "repository": cluster.repository,
        "proposals": proposals,
        "captured_at": latest.captured_at.isoformat(),
        "note": (
            "Each proposal is a deterministic new-file change. POST to "
            "/remediations/open with a workload_key to open it as a pull "
            "request; nothing is applied to the cluster directly -- the agent "
            "remains read-only, and action happens through your repository "
            "and your review."
        ),
    }


@router.post("/clusters/{cluster_id}/remediations/open", status_code=201)
async def open_remediation(
    cluster_id: str,
    body: RemediationRequest,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Open one remediation as a pull request via the GitHub App.

    One PR per proposal: independently mergeable, independently revertible.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    repo = (body.repository or cluster.repository or "").strip()
    if not repo:
        raise HTTPException(
            status_code=400,
            detail=(
                "No repository. Link one to this cluster (PUT "
                "/clusters/{id}/repository) or pass it in the request."
            ),
        )

    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }
    proposals = remediation_service.plan(snapshot, cei_by_workload)
    proposal = next(
        (p for p in proposals if p["workload_key"] == body.workload_key), None
    )
    if proposal is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No remediation is currently proposed for {body.workload_key}. "
                "GET /remediations lists what this cluster warrants."
            ),
        )

    app_client = GitHubApp()
    if not app_client.configured:
        raise HTTPException(
            status_code=503,
            detail="GitHub App is not configured on this server.",
        )
    try:
        result = remediation_service.open_pr(app_client, repo, proposal)
    except GitProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await audit.record(
        session,
        action="remediation.pr_opened",
        actor_type=ActorType.user,
        actor_id=user.email,
        tenant_id=user.tenant_id,
        target_type="cluster",
        target_id=str(cluster.id),
        source_ip=audit.client_ip(request),
        details={
            "workload_key": body.workload_key,
            "repository": repo,
            "kind": proposal["kind"],
            "pr": result.get("pull_request"),
        },
    )
    await session.commit()
    return result


@router.get("/clusters/{cluster_id}/prescriptions")
async def cluster_prescriptions(
    cluster_id: str,
    downtime_cost_per_hour: float = prescribe_service.DEFAULT_DOWNTIME_COST_PER_HOUR,
    trials: int = prescribe_service.DEFAULT_TRIALS,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Prescriptive resilience optimization: interventions ranked by risk
    reduced per dollar, with risk and cost in the same currency.

    Pass your real cost of downtime as downtime_cost_per_hour; the ranking
    is robust to the default, the absolute dollar figures are not.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = prescribe_service.prescribe(
        snapshot,
        snapshot.get("egress"),
        cei_by_workload,
        downtime_cost_per_hour=max(1.0, downtime_cost_per_hour),
        trials=max(1000, min(trials, 30_000)),
    )
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/prescriptions/set")
async def cluster_prescription_set(
    cluster_id: str,
    downtime_cost_per_hour: float = prescribe_service.DEFAULT_DOWNTIME_COST_PER_HOUR,
    budget_usd: float | None = None,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    The best SET of interventions under an optional budget: greedy marginal
    benefit, each pick evaluated against the architecture as already
    modified by prior picks. An empty selection is a valid answer.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }
    result = prescribe_service.prescribe_set(
        snapshot, snapshot.get("egress"), cei_by_workload,
        downtime_cost_per_hour=max(1.0, downtime_cost_per_hour),
        budget_usd=budget_usd,
    )
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/carbon")
async def cluster_carbon(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Annual energy and carbon estimate. Fit for relative use; says so."""
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    result = carbon_service.estimate(latest.payload or {})
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/playbook")
async def cluster_playbook(
    cluster_id: str,
    upstream: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Pre-scale playbook for an upstream degradation: who to scale, in what
    order, before their own metrics notice. Advisory by design.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }
    result = recovery_service.pre_scale_playbook(snapshot, upstream, cei_by_workload)
    if not result["found"]:
        raise HTTPException(
            status_code=404,
            detail=f"{upstream!r} is not in the latest snapshot's graph.",
        )
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    return result


@router.get("/clusters/{cluster_id}/metastability")
async def cluster_metastability(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    The signature health checks cannot see: workloads whose load never
    returned to baseline after their last dip. Every pod reports Ready in
    that state; the elevated load is retry work.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    await _latest_snapshot(session, cluster.id)  # 409 if no data at all

    rows = (
        await session.execute(
            select(WorkloadSample)
            .where(WorkloadSample.cluster_id == cluster.id)
            .order_by(WorkloadSample.observed_at)
        )
    ).scalars().all()

    history: dict[str, list[dict]] = {}
    for row in rows:
        history.setdefault(row.workload_key, []).append(
            {"cpu_cores_used": row.cpu_cores_used}
        )

    results = []
    for key, series in sorted(history.items()):
        verdict = recovery_service.detect_metastability(series)
        if verdict.get("detectable"):
            results.append({"workload_key": key, **verdict})

    suspected = [r for r in results if r.get("metastable_suspected")]
    return {
        "cluster": {"id": str(cluster.id), "name": cluster.name},
        "workloads_analyzed": len(results),
        "metastable_suspected": suspected,
        "clean": len(results) - len(suspected),
        "note": (
            "Detection needs accumulated usage samples; workloads without "
            "enough history are omitted rather than guessed at."
        ),
    }


@router.get("/clusters/{cluster_id}/blast-radius")
async def cluster_blast_radius(
    cluster_id: str,
    workload: str | None = None,
    limit: int = 10,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    What breaks if a workload changes.

    Without `workload`, ranks the cluster by whose failure costs the most.
    That ranking is deliberately not the CEI ranking: CEI judges the workload
    itself, this judges what it takes down with it, and a stable well-governed
    service half the cluster calls belongs at the top of one and not the other.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    if workload:
        radius = blast_radius_service.compute_blast_radius(
            snapshot, workload, cei_by_workload
        )
        if not radius.exists:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Workload {workload!r} is not in the latest snapshot. "
                    "Keys look like 'namespace/Kind/name'."
                ),
            )
        result = radius.to_dict()
    else:
        result = {
            "ranked": blast_radius_service.rank_by_blast_radius(
                snapshot, cei_by_workload, limit=limit
            )
        }

    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/resilience")
async def cluster_resilience(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Single points of failure nobody labelled as one.

    Distinct from /health, which reports what is failing now. This reports
    what is fine now and structurally unable to survive an ordinary node
    drain.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = resilience_service.analyze(snapshot, cei_by_workload)
    result["agent"] = _agent_capability(snapshot)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/ownership")
async def cluster_ownership(
    cluster_id: str,
    include_system: bool = False,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Critical dependencies with no owner, no source of truth, or no identifiable image."""
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = ownership_service.analyze(
        snapshot, cei_by_workload, include_system_namespaces=include_system
    )
    result["agent"] = _agent_capability(snapshot)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/cost/safety")
async def cluster_cost_safety(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Cost recommendations with a safety verdict attached.

    Reports the claimed saving and the safe saving separately. The gap is the
    money a utilization-only tool would have told someone to take, and the
    outage they would have bought with it.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = safe_to_delete_service.review_cost_recommendations(
        snapshot, analyze_cluster_cost(snapshot), cei_by_workload
    )
    result["agent"] = _agent_capability(snapshot)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.post("/clusters/{cluster_id}/pr-review")
async def cluster_pr_review(
    cluster_id: str,
    payload: PullRequestReviewRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Blast radius of a proposed change, against this cluster's live graph.

    Accepts manifests directly rather than requiring a GitHub App, so the
    analysis can be driven from any CI system -- and so it can be tried
    without installing anything.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei_by_workload = {
        n["node_id"]: n for n in compute_live_cei(snapshot, {}).nodes
    }

    result = pr_review_service.review(
        snapshot,
        [f.model_dump() for f in payload.files],
        cei_by_workload,
        default_namespace=payload.default_namespace,
    )
    if payload.render_markdown:
        result["markdown"] = pr_review_service.render_markdown(result)
    result["agent"] = _agent_capability(snapshot)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/cost")
async def cluster_cost(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Cost allocation and reserved-but-unused spend for the latest snapshot."""
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    result = analyze_cluster_cost(latest.payload or {})
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/health")
async def cluster_health(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Health findings, ranked by the CEI of the affected workload.

    Every Kubernetes dashboard can list CrashLoopBackOff pods. Ranking them by
    how much depends on the workload is the part worth paying for, so CEI is
    computed here rather than left to the caller to join.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}

    # Ranking only; entropy history is not needed to order findings, so the
    # cheaper no-history path is used deliberately.
    cei = compute_live_cei(snapshot, {})
    cei_by_workload = {n["node_id"]: n for n in cei.nodes}

    result = diagnose(snapshot, cei_by_workload)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


async def _load_scans(session: AsyncSession, cluster_id) -> list[dict]:
    """Reassemble stored scans into the shape the prioritizer expects."""
    scans = (
        await session.execute(
            select(ImageScan).where(ImageScan.cluster_id == cluster_id)
        )
    ).scalars().all()
    if not scans:
        return []

    vulns = (
        await session.execute(
            select(ImageVulnerability).where(
                ImageVulnerability.cluster_id == cluster_id
            )
        )
    ).scalars().all()
    by_scan: dict = {}
    for v in vulns:
        by_scan.setdefault(v.scan_id, []).append({
            "id": v.vulnerability_id,
            "severity": v.severity,
            "cvss_score": float(v.cvss_score) if v.cvss_score is not None else None,
            "pkg_name": v.pkg_name,
            "installed_version": v.installed_version,
            "fixed_version": v.fixed_version,
            "pkg_class": v.pkg_class,
            "title": v.title,
            "primary_url": v.primary_url,
        })

    return [
        {
            "image_reference": s.image_reference,
            "digest": s.image_digest,
            "os_family": s.os_family,
            "os_name": s.os_name,
            "workload_keys": s.workload_keys or [],
            "scanned_at": s.scanned_at.isoformat(),
            "scan_error": s.scan_error,
            "counts": {
                "CRITICAL": s.critical_count,
                "HIGH": s.high_count,
                "MEDIUM": s.medium_count,
                "LOW": s.low_count,
                "UNKNOWN": s.unknown_count,
            },
            "fixable_count": s.fixable_count,
            "vulnerabilities": by_scan.get(s.id, []),
        }
        for s in scans
    ]


@router.get("/clusters/{cluster_id}/vulnerabilities")
async def cluster_vulnerabilities(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Vulnerabilities ranked by what they put at risk, not by severity alone.

    A CVSS 9.8 in a workload nothing depends on is a smaller problem than a
    7.5 in the database every service reaches. Sorting by severity puts them
    in the wrong order, which is why 400-item vulnerability lists go unread.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    scans = await _load_scans(session, cluster.id)
    if not scans:
        return {
            "cluster": {"id": str(cluster.id), "name": cluster.name},
            "summary": {
                "images_scanned": 0,
                "total_vulnerabilities": 0,
                "headline": "No scan results yet. Enable the scanner CronJob "
                            "in the Helm chart to begin scanning images.",
            },
            "top_risks": [],
            "findings": [],
            "base_image_recommendations": [],
        }

    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei = compute_live_cei(snapshot, {})
    cei_by_workload = {n["node_id"]: n for n in cei.nodes}

    result = prioritize(scans, cei_by_workload, snapshot)
    result["base_image_recommendations"] = base_image_recommendations(scans)
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["scanned_at"] = max(s["scanned_at"] for s in scans)
    return result


@router.get("/clusters/{cluster_id}/network")
async def cluster_network(
    cluster_id: str,
    generate: bool = False,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Segmentation coverage, and optionally generated NetworkPolicies.

    Policies are derived from observed dependencies. A dependency resolved
    only at runtime produces no edge and would be blocked, which is why every
    generated policy carries that warning and is never applied from here.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}

    cei = compute_live_cei(snapshot, {})
    cei_by_workload = {n["node_id"]: n for n in cei.nodes}

    result = analyze_segmentation(snapshot, cei_by_workload)
    if generate:
        result["generated_policies"] = generate_all_policies(
            snapshot, cei_by_workload
        )
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    result["captured_at"] = latest.captured_at.isoformat()
    return result


@router.get("/clusters/{cluster_id}/remediation")
async def cluster_remediation(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    What may be done automatically about each finding, and what may not.

    Decision only. Nothing here executes: applying a change requires the Git
    or cluster-write integration, which is separately gated.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    scans = await _load_scans(session, cluster.id)
    if not scans:
        return {
            "cluster": {"id": str(cluster.id), "name": cluster.name},
            "summary": {"total": 0, "by_action": {}, "auto_apply_enabled": False},
            "plan": [],
        }

    latest = await _latest_snapshot(session, cluster.id)
    snapshot = latest.payload or {}
    cei = compute_live_cei(snapshot, {})
    ranked = prioritize(scans, {n["node_id"]: n for n in cei.nodes}, snapshot)

    namespaces = {
        w["key"]: w.get("namespace", "default")
        for w in (snapshot.get("workloads") or [])
    }
    result = plan_remediation(
        ranked["findings"], workload_namespaces=namespaces
    )
    result["cluster"] = {"id": str(cluster.id), "name": cluster.name}
    return result


@router.get("/clusters/{cluster_id}/history")
async def cluster_history(
    cluster_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    How much observation history exists.

    Surfaced because the CEI entropy term is only meaningful once enough
    samples have accumulated. The UI uses this to say "warming up, N samples"
    instead of presenting a number computed from almost nothing.
    """
    cluster = await _owned_cluster(cluster_id, user, session)
    count = (
        await session.execute(
            select(func.count(Snapshot.id)).where(Snapshot.cluster_id == cluster.id)
        )
    ).scalar_one()
    first = (
        await session.execute(
            select(func.min(Snapshot.captured_at)).where(
                Snapshot.cluster_id == cluster.id
            )
        )
    ).scalar_one()
    return {
        "snapshot_count": count,
        "first_seen_at": first.isoformat() if first else None,
        "entropy_ready": count >= 30,
        "entropy_samples_required": 30,
    }
