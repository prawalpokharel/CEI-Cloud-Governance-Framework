"""
Sandbox mode and retention.

The sandbox is the public demo surface, so the properties that matter are
that it is deterministic (two visitors must see the same headline number) and
that it exercises the real analysis rather than fixtures.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.db.base import async_url
from src.db.models import Cluster, Snapshot, Tenant, WorkloadSample
from src.services.cost import analyze_cluster_cost
from src.services.health import diagnose
from src.services.live_cei import compute_live_cei
from src.services.retention import (
    SNAPSHOT_RETENTION_HOURS,
    _latest_snapshot_ids,
    prune,
)
from src.services.sandbox import build_sandbox_history, build_sandbox_snapshot


# --------------------------------------------------------------------------
# Sandbox
# --------------------------------------------------------------------------

def test_sandbox_snapshot_is_deterministic():
    """A demo whose numbers change between two page loads is not a demo."""
    a = build_sandbox_snapshot()
    b = build_sandbox_snapshot()
    assert a == b


def test_sandbox_analysis_is_deterministic():
    snapshot = build_sandbox_snapshot()
    history = build_sandbox_history(snapshot)
    first = analyze_cluster_cost(snapshot)["summary"]["wasted_monthly_usd"]
    second = analyze_cluster_cost(snapshot)["summary"]["wasted_monthly_usd"]
    assert first == second

    cei_a = compute_live_cei(snapshot, history)
    cei_b = compute_live_cei(snapshot, history)
    assert [n["cei_score"] for n in cei_a.nodes] == [
        n["cei_score"] for n in cei_b.nodes
    ]


def test_sandbox_cluster_is_not_over_committed():
    """
    A demo cluster requesting more than it can allocate would show the
    over-commitment warning, which is not the story the sandbox is telling.
    """
    summary = analyze_cluster_cost(build_sandbox_snapshot())["summary"]
    assert summary["over_committed"] is False
    assert 0.4 < summary["cpu_commitment_ratio"] < 1.0


def test_sandbox_shows_a_meaningful_waste_figure():
    summary = analyze_cluster_cost(build_sandbox_snapshot())["summary"]
    assert summary["wasted_monthly_usd"] > 500
    assert summary["waste_as_pct_of_cluster"] <= 100
    assert summary["workloads_over_provisioned"] >= 5


def test_sandbox_has_enough_history_for_entropy():
    """
    The sandbox should demonstrate the full score, not the warming-up state.
    """
    snapshot = build_sandbox_snapshot()
    result = compute_live_cei(snapshot, build_sandbox_history(snapshot))
    assert result.entropy_ready is True
    assert result.weights["beta"] > 0


def test_sandbox_entropy_discriminates_between_workload_types():
    """
    Batch jobs swing; databases are steady. If every workload had the same
    entropy the term would look real but carry no information.
    """
    snapshot = build_sandbox_snapshot()
    result = compute_live_cei(snapshot, build_sandbox_history(snapshot))
    entropies = [n["entropy"] for n in result.nodes]
    assert max(entropies) - min(entropies) > 0.1


def test_sandbox_surfaces_health_findings_to_rank():
    snapshot = build_sandbox_snapshot()
    cei = compute_live_cei(snapshot, build_sandbox_history(snapshot))
    result = diagnose(snapshot, {n["node_id"]: n for n in cei.nodes})
    assert result["summary"]["critical"] >= 3
    kinds = {f["kind"] for f in result["findings"]}
    assert {"crash_loop", "oom_killed", "image_pull_failure"} <= kinds


def test_sandbox_snapshot_carries_no_environment_values():
    """The sandbox must model the agent's redaction contract, not bypass it."""
    import json

    payload = json.dumps(build_sandbox_snapshot())
    assert "_env_values" not in payload
    for workload in build_sandbox_snapshot()["workloads"]:
        assert "_env_values" not in workload


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------

TEST_DB = os.environ.get("TEST_DATABASE_URL", "").strip()
APP_DB = os.environ.get("DATABASE_URL", "").strip()

retention_tests = pytest.mark.skipif(
    not TEST_DB, reason="TEST_DATABASE_URL not set; skipping database tests"
)

if TEST_DB and APP_DB and TEST_DB == APP_DB:
    raise RuntimeError(
        "TEST_DATABASE_URL is the same as DATABASE_URL. These tests delete "
        "rows. Point TEST_DATABASE_URL at a throwaway database."
    )


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(async_url(TEST_DB))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()
        await s.execute(delete(Tenant))
        await s.commit()
    await engine.dispose()


async def _cluster(session) -> Cluster:
    tenant = Tenant(name="Acme", slug=f"acme-{datetime.now().timestamp()}")
    session.add(tenant)
    await session.flush()
    cluster = Cluster(tenant_id=tenant.id, name="c1")
    session.add(cluster)
    await session.flush()
    return cluster


def _snap(cluster, *, age_hours: float, seq: int) -> Snapshot:
    when = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return Snapshot(
        tenant_id=cluster.tenant_id,
        cluster_id=cluster.id,
        seq=seq,
        captured_at=when,
        received_at=when,
        payload={},
        payload_bytes=2,
    )


@retention_tests
@pytest.mark.asyncio
async def test_expired_snapshots_are_deleted(session):
    cluster = await _cluster(session)
    session.add_all([
        _snap(cluster, age_hours=SNAPSHOT_RETENTION_HOURS + 10, seq=1),
        _snap(cluster, age_hours=SNAPSHOT_RETENTION_HOURS + 5, seq=2),
        _snap(cluster, age_hours=1, seq=3),
    ])
    await session.commit()

    result = await prune(session)
    assert result["snapshots_deleted"] == 2

    remaining = (
        await session.execute(
            select(func.count(Snapshot.id)).where(Snapshot.cluster_id == cluster.id)
        )
    ).scalar_one()
    assert remaining == 1


@retention_tests
@pytest.mark.asyncio
async def test_the_latest_snapshot_survives_however_old_it_is(session):
    """
    A cluster whose agent has been offline for a week should still render its
    last known topology. "No data" and "stale data" are different states and
    the UI distinguishes them.
    """
    cluster = await _cluster(session)
    session.add_all([
        _snap(cluster, age_hours=400, seq=1),
        _snap(cluster, age_hours=300, seq=2),
    ])
    await session.commit()

    await prune(session)

    remaining = (
        await session.execute(
            select(Snapshot).where(Snapshot.cluster_id == cluster.id)
        )
    ).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].seq == 2  # the most recent, not an arbitrary one


@retention_tests
@pytest.mark.asyncio
async def test_latest_is_chosen_by_time_not_by_uuid_ordering(session):
    """
    Snapshot ids are UUIDs, so max(id) picks whichever sorts highest as a
    128-bit value -- unrelated to recency. Exempting that one would delete
    the current snapshot and keep a stale one.
    """
    cluster = await _cluster(session)
    snaps = [_snap(cluster, age_hours=100 - i, seq=i) for i in range(5)]
    session.add_all(snaps)
    await session.commit()

    latest_ids = await _latest_snapshot_ids(session)
    newest = max(snaps, key=lambda s: s.received_at)
    assert latest_ids == {newest.id}


@retention_tests
@pytest.mark.asyncio
async def test_workload_samples_are_kept_far_longer_than_snapshots(session):
    """Samples feed the entropy term, so they outlive the raw payloads."""
    cluster = await _cluster(session)
    now = datetime.now(timezone.utc)
    session.add_all([
        WorkloadSample(
            tenant_id=cluster.tenant_id, cluster_id=cluster.id,
            workload_key="default/Deployment/a", namespace="default",
            observed_at=now - timedelta(days=age),
        )
        for age in (1, 10, 25, 45)
    ])
    await session.commit()

    result = await prune(session)
    assert result["samples_deleted"] == 1  # only the 45-day-old one

    remaining = (
        await session.execute(
            select(func.count(WorkloadSample.id)).where(
                WorkloadSample.cluster_id == cluster.id
            )
        )
    ).scalar_one()
    assert remaining == 3
