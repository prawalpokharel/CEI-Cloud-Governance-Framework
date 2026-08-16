"""
Agent ingest.

    POST /v1/ingest

Authenticated by API key, gzip-encoded body, idempotent on (cluster, seq).
This is the hot path: every agent in every customer cluster hits it on a
fixed interval, so it does the minimum work required to durably accept a
snapshot and returns. Analysis happens on read, not here.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import get_session
from ..db.models import ActorType, ApiKey, Cluster, ClusterProvider, Snapshot, WorkloadSample
from ..services import audit
from ..services.security import api_key_handle, api_key_matches

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["ingest"])

# Uncompressed ceiling. A 1000-pod cluster serializes to a few MiB, so this
# leaves generous headroom while bounding what a single request can force the
# server to hold in memory.
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024

# Guards against a decompression bomb: a small gzip body that expands to
# something enormous. Checked during streaming decompression, not after.
MAX_COMPRESSED_BYTES = 8 * 1024 * 1024


async def authenticate_agent(
    request: Request,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
) -> tuple[ApiKey, Cluster]:
    """
    Resolve a Bearer API key to its cluster.

    Looks up by the non-secret handle (indexed) and then compares hashes in
    constant time, so authentication is one indexed row fetch rather than a
    scan.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer API key")

    presented = authorization.split(None, 1)[1].strip()
    handle = api_key_handle(presented)
    if not handle:
        raise HTTPException(status_code=401, detail="Malformed API key")

    row = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == handle))
    ).scalar_one_or_none()

    # Same message for unknown, mismatched, and revoked keys: distinguishing
    # them tells an attacker which guesses were structurally correct.
    if row is None or not api_key_matches(presented, row.key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")
    if row.revoked_at is not None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    cluster = (
        await session.execute(select(Cluster).where(Cluster.id == row.cluster_id))
    ).scalar_one_or_none()
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster no longer exists")

    return row, cluster


async def _read_body(request: Request) -> dict:
    """
    Read and decode the request body.

    Starlette's GZipMiddleware compresses responses only; a gzip-encoded
    REQUEST is not handled anywhere in the stack, so it is decompressed here.
    """
    raw = await request.body()
    if len(raw) > MAX_COMPRESSED_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Body exceeds {MAX_COMPRESSED_BYTES // (1024 * 1024)} MiB",
        )

    encoding = (request.headers.get("content-encoding") or "").lower()
    if "gzip" in encoding:
        try:
            decompressor = gzip.GzipFile(fileobj=io.BytesIO(raw))
            chunks = []
            total = 0
            while True:
                chunk = decompressor.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SNAPSHOT_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Decompressed snapshot exceeds size limit",
                    )
                chunks.append(chunk)
            raw = b"".join(chunks)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Malformed gzip body: {exc}"
            )
    elif len(raw) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(status_code=413, detail="Snapshot exceeds size limit")

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Body is not valid JSON: {exc}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Snapshot must be a JSON object")
    return payload


def _cadence_directive() -> dict:
    """
    Optionally instruct the agent to change its poll interval.

    Returned only when INGEST_FORCED_INTERVAL_SECONDS is set. The mechanism
    exists so a noisy fleet can be backed off without anyone editing a Helm
    value and redeploying -- but an unconditional value silently overrode the
    operator's configured interval, so a cluster installed with
    intervalSeconds=20 quietly ran at 60. Absent an actual reason to
    intervene, the agent keeps what it was configured with.
    """
    raw = os.environ.get("INGEST_FORCED_INTERVAL_SECONDS", "").strip()
    if not raw:
        return {}
    try:
        seconds = int(raw)
    except ValueError:
        return {}
    return {"next_interval_seconds": max(10, seconds)}


def _parse_timestamp(value) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


@router.post("/ingest")
async def ingest_snapshot(
    request: Request,
    session: AsyncSession = Depends(get_session),
    auth: tuple[ApiKey, Cluster] = Depends(authenticate_agent),
):
    api_key, cluster = auth
    payload = await _read_body(request)

    seq = payload.get("seq")
    if not isinstance(seq, int) or seq < 0:
        raise HTTPException(status_code=400, detail="Snapshot 'seq' must be a non-negative integer")

    cluster_info = payload.get("cluster") or {}
    reported_uid = (cluster_info.get("uid") or "").strip()

    # Bind the cluster identity on first ingest. Afterwards a mismatch means
    # the key was copied into a different cluster: recorded and rejected,
    # because silently accepting it would merge two clusters' topologies into
    # one incoherent graph.
    if reported_uid:
        if not cluster.cluster_uid:
            # Is this physical cluster already registered under a different
            # row in this tenant? (tenant_id, cluster_uid) is unique, so
            # assigning it here would fail at commit -- and because that
            # failure is an IntegrityError, it used to be swallowed by the
            # duplicate-snapshot handler below: HTTP 200, agent logs success,
            # nothing ever persists. Silent and permanent. Detect it up front
            # and say so plainly instead.
            existing = (
                await session.execute(
                    select(Cluster).where(
                        Cluster.tenant_id == cluster.tenant_id,
                        Cluster.cluster_uid == reported_uid,
                        Cluster.id != cluster.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing_name = existing.name
                await audit.record(
                    session,
                    action="cluster.duplicate_registration",
                    actor_type=ActorType.agent,
                    actor_id=str(api_key.id),
                    tenant_id=cluster.tenant_id,
                    target_type="cluster",
                    target_id=str(cluster.id),
                    source_ip=audit.client_ip(request),
                    details={
                        "cluster_uid": reported_uid,
                        "already_registered_as": existing_name,
                    },
                )
                await session.commit()
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"This cluster is already registered as "
                        f"{existing_name!r}. Use that cluster's API key, or "
                        f"delete it first. Registering one cluster twice "
                        f"would split its history across two records."
                    ),
                )
            cluster.cluster_uid = reported_uid
        elif cluster.cluster_uid != reported_uid:
            await audit.record(
                session,
                action="cluster.uid_mismatch",
                actor_type=ActorType.agent,
                actor_id=str(api_key.id),
                tenant_id=cluster.tenant_id,
                target_type="cluster",
                target_id=str(cluster.id),
                source_ip=audit.client_ip(request),
                details={"expected": cluster.cluster_uid, "received": reported_uid},
            )
            await session.commit()
            raise HTTPException(
                status_code=409,
                detail=(
                    "This API key is bound to a different cluster. Create a "
                    "new cluster in the dashboard for this one."
                ),
            )

    provider_raw = (cluster_info.get("provider") or "unknown").lower()
    try:
        provider = ClusterProvider(provider_raw)
    except ValueError:
        provider = ClusterProvider.other

    workloads = payload.get("workloads") or []
    nodes = payload.get("nodes") or []
    pods = payload.get("pods") or []
    captured_at = _parse_timestamp(payload.get("captured_at"))

    cluster.provider = provider
    cluster.k8s_version = cluster_info.get("kubernetes_version")
    cluster.agent_version = payload.get("agent_version")
    cluster.metrics_available = bool(cluster_info.get("metrics_available"))
    cluster.last_seen_at = datetime.now(timezone.utc)
    api_key.last_used_at = datetime.now(timezone.utc)

    # Captured before commit. A failed commit is followed by a rollback,
    # which expires every ORM object in the session, so reading cluster.id
    # afterwards would trigger a lazy refresh -- async IO from a sync
    # attribute access, which raises MissingGreenlet instead of the intended
    # duplicate response.
    cluster_id_str = str(cluster.id)

    body_size = int(request.headers.get("content-length") or 0)
    snapshot = Snapshot(
        tenant_id=cluster.tenant_id,
        cluster_id=cluster.id,
        seq=seq,
        captured_at=captured_at,
        agent_version=payload.get("agent_version"),
        payload=payload,
        payload_bytes=body_size,
        node_count=len(nodes),
        pod_count=len(pods),
        workload_count=len(workloads),
    )
    session.add(snapshot)

    # Derive the durable time series. The raw snapshot is retained only
    # briefly; these rows are what the entropy term and Phase 2's
    # requested-vs-used analysis actually read.
    for workload in workloads:
        key = workload.get("key")
        if not key:
            continue
        session.add(
            WorkloadSample(
                tenant_id=cluster.tenant_id,
                cluster_id=cluster.id,
                workload_key=key,
                namespace=workload.get("namespace") or "default",
                observed_at=captured_at,
                cpu_cores_used=workload.get("cpu_cores_used"),
                cpu_cores_requested=workload.get("cpu_cores_requested"),
                mem_bytes_used=workload.get("memory_bytes_used"),
                mem_bytes_requested=workload.get("memory_bytes_requested"),
                replicas_ready=workload.get("replicas_ready"),
                replicas_desired=workload.get("replicas_desired"),
            )
        )

    await audit.record(
        session,
        action="cluster.ingested",
        actor_type=ActorType.agent,
        actor_id=str(api_key.id),
        tenant_id=cluster.tenant_id,
        target_type="cluster",
        target_id=str(cluster.id),
        source_ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
        details={
            "seq": seq,
            "workloads": len(workloads),
            "nodes": len(nodes),
            "pods": len(pods),
            "edges": len(payload.get("edges") or []),
            "metrics_available": cluster.metrics_available,
        },
    )

    try:
        await session.commit()
    except IntegrityError:
        # The (cluster_id, captured_at) constraint fired: this exact
        # observation was already accepted and the agent is retrying.
        # Idempotent success, because the desired state is already true.
        await session.rollback()
        log.info(
            "Duplicate snapshot captured_at=%s for cluster %s",
            captured_at, cluster_id_str,
        )
        return {
            "status": "duplicate",
            "cluster_id": cluster_id_str,
            "seq": seq,
            **_cadence_directive(),
        }

    return {
        "status": "accepted",
        "cluster_id": cluster_id_str,
        "seq": seq,
        # Cadence is server-controlled so a noisy fleet can be backed off
        # without anyone editing a Helm value and redeploying.
        "next_interval_seconds": 60,
    }
