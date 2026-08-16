"""
Operational commands.

    python -m src.cli prune          delete expired snapshots and samples
    python -m src.cli prune --dry-run
    python -m src.cli report         weekly governance report per cluster
    python -m src.cli alert          Slack alerts for high-CEI critical issues

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
from .services import notify, retention

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

    report_cmd = sub.add_parser("report", help="Send the weekly governance report")
    report_cmd.add_argument(
        "--dry-run", action="store_true", help="Print instead of sending"
    )

    alert_cmd = sub.add_parser("alert", help="Send Slack alerts for critical issues")
    alert_cmd.add_argument(
        "--dry-run", action="store_true", help="Print instead of sending"
    )

    args = parser.parse_args(argv)

    async def run() -> int:
        try:
            if args.command == "prune":
                return await _prune(args.dry_run)
            if args.command == "report":
                return await _report(args.dry_run)
            if args.command == "alert":
                return await _alert(args.dry_run)
            return 1
        finally:
            await dispose_engine()

    return asyncio.run(run())



async def _for_each_cluster(session, fn):
    """Run fn(cluster, cost, health, vulns, cei) for every reporting cluster."""
    from sqlalchemy import desc, select

    from .db.models import Cluster, ImageScan, Snapshot, User
    from .routers.app_api import _load_scans
    from .services.cost import analyze_cluster_cost
    from .services.health import diagnose
    from .services.live_cei import compute_live_cei
    from .services.vulnerability import prioritize

    clusters = (await session.execute(select(Cluster))).scalars().all()
    results = []

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
            log.info("Skipping %s: no snapshot yet", cluster.name)
            continue

        snapshot = latest.payload or {}
        cei = compute_live_cei(snapshot, {})
        cei_by_workload = {n["node_id"]: n for n in cei.nodes}

        cost = analyze_cluster_cost(snapshot)
        health = diagnose(snapshot, cei_by_workload)
        scans = await _load_scans(session, cluster.id)
        vulns = (
            prioritize(scans, cei_by_workload, snapshot)
            if scans
            else {"summary": {"total_vulnerabilities": 0, "by_severity": {},
                              "headline": "No scan results yet."},
                  "top_risks": []}
        )

        recipients = (
            await session.execute(
                select(User).where(User.tenant_id == cluster.tenant_id)
            )
        ).scalars().all()

        results.append(
            await fn(cluster, cost, health, vulns, cei, recipients)
        )
    return results


async def _report(dry_run: bool) -> int:
    from .services.report import build_report

    factory = get_session_factory()
    sent = 0
    skipped = 0

    async def handle(cluster, cost, health, vulns, cei, recipients):
        nonlocal sent, skipped
        report = build_report(
            cluster_name=cluster.name,
            cost=cost,
            health=health,
            vulnerabilities=vulns,
            cei=cei.to_dict(),
        )
        if dry_run:
            print(f"--- {cluster.name} ---")
            print(f"subject: {report['subject']}")
            print(f"recipients: {[u.email for u in recipients]}")
            print(report["text"])
            print()
            return report

        for user in recipients:
            try:
                delivered = notify.send_email(
                    to=user.email,
                    subject=report["subject"],
                    html_body=report["html"],
                    text_body=report["text"],
                )
                sent += 1 if delivered else 0
                skipped += 0 if delivered else 1
            except notify.DeliveryError as exc:
                log.error("Report to %s failed: %s", user.email, exc)
        return report

    async with factory() as session:
        await _for_each_cluster(session, handle)

    if not dry_run:
        print(json.dumps({"emails_sent": sent, "skipped": skipped}, indent=2))
    return 0


async def _alert(dry_run: bool) -> int:
    factory = get_session_factory()
    total_alerts = 0

    async def handle(cluster, cost, health, vulns, cei, recipients):
        nonlocal total_alerts
        alertable = notify.select_alertable(health.get("findings", []))
        if not alertable:
            log.info(
                "%s: %d finding(s), none clear the alert threshold",
                cluster.name, health.get("summary", {}).get("total", 0),
            )
            return None

        total_alerts += len(alertable)
        message = notify.build_slack_message(cluster.name, alertable)
        if dry_run:
            print(f"--- {cluster.name}: {len(alertable)} alertable ---")
            print(json.dumps(message, indent=2)[:1500])
            return message

        try:
            notify.send_slack(message)
        except notify.DeliveryError as exc:
            log.error("Slack delivery failed for %s: %s", cluster.name, exc)
        return message

    async with factory() as session:
        await _for_each_cluster(session, handle)

    if not dry_run:
        print(json.dumps({"alerts": total_alerts}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
