"""
Snapshot retention.

Raw snapshots are debugging and replay material, not the system of record.
The durable signal is extracted into `workload_samples` at ingest, so the
payloads can be discarded aggressively.

The arithmetic that makes this non-optional: one cluster at a 60s interval
writes ~1,440 snapshots/day at ~30 KiB each for a small cluster, and a few
hundred KiB for a large one. A hundred clusters at 24h retention is a few
GiB of JSONB; without retention it is a few GiB *per day*, growing forever.

Deletion is chunked rather than issued as one statement. A single
`DELETE FROM snapshots WHERE received_at < ...` over a large backlog takes a
long lock and can time out, leaving nothing deleted and the table still
growing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Snapshot, WorkloadSample

log = logging.getLogger(__name__)

SNAPSHOT_RETENTION_HOURS = 24

# Samples are two orders of magnitude smaller than snapshots (a handful of
# numbers per workload versus the full cluster payload) and are what the
# entropy term reads, so they are kept far longer.
SAMPLE_RETENTION_DAYS = 30

DELETE_CHUNK = 500


async def _latest_snapshot_ids(session: AsyncSession) -> set:
    """
    The id of the most recent snapshot for each cluster.

    Ordered by received_at, NOT by max(id): ids are UUIDs, so taking their
    maximum returns whichever sorts highest as a 128-bit value — unrelated to
    recency, and it would have exempted an arbitrary snapshot per cluster
    while deleting the current one.
    """
    ranked = (
        select(
            Snapshot.id,
            func.row_number()
            .over(
                partition_by=Snapshot.cluster_id,
                order_by=Snapshot.received_at.desc(),
            )
            .label("rank"),
        )
        .subquery()
    )
    rows = await session.execute(select(ranked.c.id).where(ranked.c.rank == 1))
    return set(rows.scalars().all())


async def _delete_older_than(
    session: AsyncSession, model, column, cutoff: datetime
) -> int:
    total = 0
    while True:
        stmt = select(model.id).where(column < cutoff).limit(DELETE_CHUNK)
        ids = (await session.execute(stmt)).scalars().all()
        if not ids:
            break
        await session.execute(delete(model).where(model.id.in_(ids)))
        await session.commit()
        total += len(ids)
        if len(ids) < DELETE_CHUNK:
            break
    return total


async def prune(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """
    Delete expired snapshots and workload samples.

    Always retains each cluster's most recent snapshot regardless of age. A
    cluster whose agent has been offline for a week should still render its
    last known topology rather than appearing to have never reported --
    "no data" and "stale data" are different states and the UI distinguishes
    them.
    """
    now = now or datetime.now(timezone.utc)
    snapshot_cutoff = now - timedelta(hours=SNAPSHOT_RETENTION_HOURS)
    sample_cutoff = now - timedelta(days=SAMPLE_RETENTION_DAYS)

    # The newest snapshot per cluster, exempt from deletion.
    keep_ids = await _latest_snapshot_ids(session)

    deleted_snapshots = 0
    while True:
        ids = (
            await session.execute(
                select(Snapshot.id)
                .where(Snapshot.received_at < snapshot_cutoff)
                .limit(DELETE_CHUNK)
            )
        ).scalars().all()
        ids = [i for i in ids if i not in keep_ids]
        if not ids:
            break
        await session.execute(delete(Snapshot).where(Snapshot.id.in_(ids)))
        await session.commit()
        deleted_snapshots += len(ids)
        if len(ids) < DELETE_CHUNK:
            break

    deleted_samples = await _delete_older_than(
        session, WorkloadSample, WorkloadSample.observed_at, sample_cutoff
    )

    if deleted_snapshots or deleted_samples:
        log.info(
            "Retention: removed %d snapshot(s) older than %dh and %d sample(s) "
            "older than %dd",
            deleted_snapshots, SNAPSHOT_RETENTION_HOURS,
            deleted_samples, SAMPLE_RETENTION_DAYS,
        )

    return {
        "snapshots_deleted": deleted_snapshots,
        "samples_deleted": deleted_samples,
        "snapshot_cutoff": snapshot_cutoff.isoformat(),
        "sample_cutoff": sample_cutoff.isoformat(),
    }
