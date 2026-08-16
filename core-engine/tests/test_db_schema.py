"""
Schema round-trip tests against a real Postgres.

Skipped when TEST_DATABASE_URL is unset so the golden suite still runs in an
environment without a database. Uses a separate variable from DATABASE_URL on
purpose: a test that truncates tables must never be one typo away from
pointing at production.

    docker run -d --name co-postgres -e POSTGRES_PASSWORD=devpass \
      -e POSTGRES_USER=cloudopt -e POSTGRES_DB=cloudoptimizer \
      -p 55432:5432 postgres:16-alpine

    export TEST_DATABASE_URL=postgres://cloudopt:devpass@localhost:55432/cloudoptimizer
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import async_url
from src.db.models import (
    ActorType,
    ApiKey,
    AuditLog,
    Cluster,
    ClusterProvider,
    Snapshot,
    Tenant,
    User,
    WorkloadSample,
)

TEST_DB = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set; skipping database tests"
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(async_url(TEST_DB))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        # Clean up in FK-safe order. Tenants cascade, but audit_log
        # deliberately has no FK, so it is removed explicitly.
        await s.rollback()
        await s.execute(delete(AuditLog))
        await s.execute(delete(Tenant))
        await s.commit()
    await engine.dispose()


async def _tenant(session, slug: str | None = None) -> Tenant:
    t = Tenant(name="Acme", slug=slug or f"acme-{uuid.uuid4().hex[:8]}")
    session.add(t)
    await session.flush()
    return t


@pytest.mark.asyncio
async def test_full_object_graph_round_trips(session):
    """A tenant with a user, cluster, key, snapshot, and sample persists."""
    t = await _tenant(session)

    user = User(
        tenant_id=t.id,
        email="founder@acme.test",
        password_hash="argon2-placeholder",
        name="Founder",
    )
    cluster = Cluster(
        tenant_id=t.id,
        name="prod-us-east",
        cluster_uid="ns-kube-system-uid-1",
        provider=ClusterProvider.eks,
        k8s_version="1.29",
        agent_version="0.1.0",
        metrics_available=True,
    )
    session.add_all([user, cluster])
    await session.flush()

    session.add(
        ApiKey(
            tenant_id=t.id,
            cluster_id=cluster.id,
            prefix="co_live_a1b2c3d4",
            key_hash="0" * 64,
            label="prod agent",
            created_by_user_id=user.id,
        )
    )
    now = datetime.now(timezone.utc)
    session.add(
        Snapshot(
            tenant_id=t.id,
            cluster_id=cluster.id,
            seq=1,
            captured_at=now,
            agent_version="0.1.0",
            payload={"nodes": [], "workloads": []},
            payload_bytes=24,
            node_count=3,
            pod_count=42,
            workload_count=11,
        )
    )
    session.add(
        WorkloadSample(
            tenant_id=t.id,
            cluster_id=cluster.id,
            workload_key="default/Deployment/frontend",
            namespace="default",
            observed_at=now,
            cpu_cores_used=0.35,
            cpu_cores_requested=1.0,
            mem_bytes_used=512 * 1024 * 1024,
            mem_bytes_requested=1024 * 1024 * 1024,
            replicas_ready=3,
            replicas_desired=3,
        )
    )
    await session.commit()

    got = (
        await session.execute(select(Cluster).where(Cluster.id == cluster.id))
    ).scalar_one()
    assert got.provider is ClusterProvider.eks
    assert got.metrics_available is True

    sample = (
        await session.execute(
            select(WorkloadSample).where(WorkloadSample.cluster_id == cluster.id)
        )
    ).scalar_one()
    # Numeric comes back as Decimal; compare numerically, not by type.
    assert float(sample.cpu_cores_used) == pytest.approx(0.35)
    assert sample.mem_bytes_requested == 1024 * 1024 * 1024


@pytest.mark.asyncio
async def test_snapshot_idempotency_keys_on_capture_time(session):
    """
    A retried ingest resends identical bytes, so captured_at is the same and
    the second insert must be rejected.
    """
    t = await _tenant(session)
    cluster = Cluster(tenant_id=t.id, name="c1")
    session.add(cluster)
    await session.flush()

    now = datetime.now(timezone.utc)
    session.add(
        Snapshot(
            tenant_id=t.id, cluster_id=cluster.id, seq=7,
            captured_at=now, payload={}, payload_bytes=2,
        )
    )
    await session.commit()

    session.add(
        Snapshot(
            tenant_id=t.id, cluster_id=cluster.id, seq=7,
            captured_at=now, payload={}, payload_bytes=2,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_restarted_agent_can_still_ingest(session):
    """
    seq resets to 1 when the agent pod restarts. Keying idempotency on seq
    made every post-restart snapshot look like a duplicate, so the agent went
    permanently silent after its first restart. Different capture times must
    be accepted regardless of repeated seq values.
    """
    t = await _tenant(session)
    cluster = Cluster(tenant_id=t.id, name="c1")
    session.add(cluster)
    await session.flush()

    now = datetime.now(timezone.utc)
    for offset in range(3):
        session.add(
            Snapshot(
                tenant_id=t.id, cluster_id=cluster.id,
                seq=1,  # reset by a restart every time
                captured_at=now + timedelta(seconds=offset),
                payload={}, payload_bytes=2,
            )
        )
    await session.commit()

    stored = (
        await session.execute(
            select(Snapshot).where(Snapshot.cluster_id == cluster.id)
        )
    ).scalars().all()
    assert len(stored) == 3


@pytest.mark.asyncio
async def test_cluster_uid_unique_per_tenant_not_globally(session):
    """
    Two tenants may each register a cluster; the same tenant may not register
    the same physical cluster twice.
    """
    t1 = await _tenant(session)
    t2 = await _tenant(session)
    uid = "shared-kube-system-uid"

    session.add(Cluster(tenant_id=t1.id, name="a", cluster_uid=uid))
    session.add(Cluster(tenant_id=t2.id, name="b", cluster_uid=uid))
    await session.commit()  # different tenants: allowed

    session.add(Cluster(tenant_id=t1.id, name="c", cluster_uid=uid))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_api_key_prefix_globally_unique(session):
    """Prefix is the lookup index for auth, so collisions must be impossible."""
    t = await _tenant(session)
    c = Cluster(tenant_id=t.id, name="c1")
    session.add(c)
    await session.flush()

    session.add(
        ApiKey(tenant_id=t.id, cluster_id=c.id, prefix="co_live_dup", key_hash="a" * 64)
    )
    await session.commit()

    session.add(
        ApiKey(tenant_id=t.id, cluster_id=c.id, prefix="co_live_dup", key_hash="b" * 64)
    )
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_audit_log_survives_deletion_of_its_subject(session):
    """
    Audit rows must outlive what they describe -- deleting a tenant cannot
    erase the record of what was done to it.
    """
    t = await _tenant(session)
    tenant_id = t.id
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_type=ActorType.user,
            actor_id="founder@acme.test",
            action="api_key.created",
            target_type="cluster",
            target_id=str(uuid.uuid4()),
            source_ip="203.0.113.10",
            details={"label": "prod agent"},
        )
    )
    await session.commit()

    await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    await session.commit()

    rows = (
        await session.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == "api_key.created"


@pytest.mark.asyncio
async def test_timestamps_are_timezone_aware(session):
    """Naive timestamps silently mix local and UTC once anything is deployed."""
    t = await _tenant(session)
    await session.commit()

    got = (await session.execute(select(Tenant).where(Tenant.id == t.id))).scalar_one()
    assert got.created_at.tzinfo is not None
