"""
Operational commands.

    python -m src.cli prune          delete expired snapshots and samples
    python -m src.cli prune --dry-run

Run `prune` on a schedule (Railway cron, or any scheduler that can invoke a
one-off command against the service). It is idempotent and safe to run
concurrently with the API.

Deliberately a separate entrypoint rather than a background thread inside the
web process: with more than one replica every replica would run it, and a
long-running delete inside a request-serving process competes with request
handling for the connection pool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .db.base import dispose_engine, get_session_factory
from .services import retention

log = logging.getLogger("cloudoptimizer.cli")


async def _prune(dry_run: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        if dry_run:
            from datetime import datetime, timedelta, timezone

            from sqlalchemy import func, select

            from .db.models import Snapshot, WorkloadSample

            now = datetime.now(timezone.utc)
            snapshot_cutoff = now - timedelta(
                hours=retention.SNAPSHOT_RETENTION_HOURS
            )
            sample_cutoff = now - timedelta(days=retention.SAMPLE_RETENTION_DAYS)

            snapshots = (
                await session.execute(
                    select(func.count(Snapshot.id)).where(
                        Snapshot.received_at < snapshot_cutoff
                    )
                )
            ).scalar_one()
            samples = (
                await session.execute(
                    select(func.count(WorkloadSample.id)).where(
                        WorkloadSample.observed_at < sample_cutoff
                    )
                )
            ).scalar_one()
            keep = len(await retention._latest_snapshot_ids(session))

            print(json.dumps({
                "dry_run": True,
                "snapshots_expired": snapshots,
                "snapshots_exempt_as_latest_per_cluster": keep,
                "samples_expired": samples,
                "snapshot_cutoff": snapshot_cutoff.isoformat(),
                "sample_cutoff": sample_cutoff.isoformat(),
            }, indent=2))
            return 0

        result = await retention.prune(session)
        print(json.dumps(result, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(prog="cloudoptimizer")
    sub = parser.add_subparsers(dest="command", required=True)

    prune_cmd = sub.add_parser("prune", help="Delete expired snapshots/samples")
    prune_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be deleted without deleting it",
    )

    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.command == "prune":
                return await _prune(args.dry_run)
            return 1
        finally:
            await dispose_engine()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
