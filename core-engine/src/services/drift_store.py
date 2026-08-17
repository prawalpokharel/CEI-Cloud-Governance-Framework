"""
The drift rail: persistence and notification for structural events.

`drift.py` is a pure comparison and stays that way. This module is the part
that runs on every ingest: compare the new snapshot to its predecessor, keep
what changed, and decide who hears about it.

## Runs after the snapshot commit, never inside it

Drift is derived data. If the comparison, the insert, or the Slack call
fails, the snapshot must still be stored -- an analysis bug that starts
rejecting agent ingests would take down data collection for the whole fleet
in exchange for a nicety. Every failure here is logged and swallowed.

## Debounce is per fact, not per notification channel

A workload that is load-bearing drifts in and out of the detection threshold
as replicas move; without a debounce, each crossing pages someone, and by the
third page the channel is muted. An identical (kind, workload) event within
DEBOUNCE_HOURS is recorded -- the row is the history -- but not re-notified,
and the row says why. "Why did nobody get paged" and "why did I get paged
twice" must both be answerable from the table alone.

## Critical only

Notification is reserved for the events whose whole point is same-day
attention: a workload becoming load-bearing, concentration jumping, a
load-bearing workload vanishing. Warnings accumulate for the dashboard.
Alerting on everything is indistinguishable from alerting on nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import DriftEvent
from . import drift, notify
from .graph_simulation import concentration, structural_centrality
from .blast_radius import build_dependency_graph

log = logging.getLogger(__name__)

DEBOUNCE_HOURS = 24

# Event kinds worth a same-day notification. Everything else accumulates.
NOTIFY_KINDS = {
    "became_load_bearing",
    "concentration_rose",
    "load_bearing_workload_disappeared",
}


def snapshot_concentration(payload: dict) -> float | None:
    """
    The value persisted on every snapshot row for trend queries.

    Internal workload graph only: external nodes need egress data most
    snapshots do not carry, and a trend metric that silently changes meaning
    depending on which agent features are enabled is worse than a narrower
    one that always means the same thing.
    """
    try:
        graph = build_dependency_graph(payload)
        scores = structural_centrality(graph)
        return round(concentration(scores), 6) if scores else None
    except Exception:
        log.exception("Failed to compute snapshot concentration")
        return None


async def _recently_notified(
    session: AsyncSession, cluster_id, kind: str, workload_key: str | None
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=DEBOUNCE_HOURS)
    row = (
        await session.execute(
            select(DriftEvent.id)
            .where(
                DriftEvent.cluster_id == cluster_id,
                DriftEvent.kind == kind,
                DriftEvent.workload_key == workload_key,
                DriftEvent.notified.is_(True),
                DriftEvent.detected_at >= cutoff,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


def _slack_payload(cluster_name: str, event: dict) -> dict:
    icon = {"critical": ":red_circle:", "warning": ":large_orange_circle:"}.get(
        event["severity"], ":white_circle:"
    )
    return {
        "text": f"{icon} [{cluster_name}] {event['title']}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{icon} *{event['title']}*\n"
                        f"Cluster: `{cluster_name}` · severity: {event['severity']}\n\n"
                        f"{event['detail'][:2500]}"
                    ),
                },
            },
        ],
    }


async def record_drift(
    session: AsyncSession,
    *,
    tenant_id,
    cluster_id,
    cluster_name: str,
    previous_payload: dict,
    current_payload: dict,
    before_captured_at=None,
    after_captured_at=None,
) -> dict[str, Any]:
    """
    Compare, persist, notify. Called by ingest after the snapshot committed.

    Returns a small summary for the ingest audit trail. Raises nothing:
    drift must never fail an ingest.
    """
    try:
        result = drift.compare_snapshots(
            previous_payload,
            current_payload,
            before_at=str(before_captured_at) if before_captured_at else None,
            after_at=str(after_captured_at) if after_captured_at else None,
        )
    except Exception:
        log.exception("Drift comparison failed for cluster %s", cluster_id)
        return {"events": 0, "notified": 0, "error": "comparison_failed"}

    stored = 0
    notified = 0
    try:
        for event in result.get("events") or []:
            wants_notify = (
                event["severity"] == "critical" and event["kind"] in NOTIFY_KINDS
            )
            skip_reason = None
            if wants_notify:
                if await _recently_notified(
                    session, cluster_id, event["kind"], event.get("workload_key")
                ):
                    wants_notify, skip_reason = False, "debounced"
            else:
                skip_reason = "below_threshold"

            sent = False
            if wants_notify:
                # Best-effort: a Slack outage must not mark the event lost.
                try:
                    sent = notify.send_slack(_slack_payload(cluster_name, event))
                    if not sent:
                        # send_slack returns False when no webhook is
                        # configured -- different from an outage, and the row
                        # must say which it was.
                        skip_reason = "no_webhook_configured"
                except Exception:
                    log.exception("Drift notification failed")
                    skip_reason = "delivery_failed"

            session.add(DriftEvent(
                tenant_id=tenant_id,
                cluster_id=cluster_id,
                kind=event["kind"],
                severity=event["severity"],
                workload_key=event.get("workload_key"),
                title=event["title"][:300],
                detail=event["detail"],
                evidence=event.get("evidence") or {},
                before_captured_at=before_captured_at,
                after_captured_at=after_captured_at,
                notified=sent,
                notify_skip_reason=None if sent else skip_reason,
            ))
            stored += 1
            if sent:
                notified += 1

        await session.commit()
    except Exception:
        log.exception("Failed to persist drift events for cluster %s", cluster_id)
        await session.rollback()
        return {"events": stored, "notified": notified, "error": "persist_failed"}

    return {
        "events": stored,
        "notified": notified,
        "concentration_delta": (result.get("concentration") or {}).get("delta"),
    }


async def latest_events(
    session: AsyncSession, cluster_id, *, limit: int = 50
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(DriftEvent)
            .where(DriftEvent.cluster_id == cluster_id)
            .order_by(desc(DriftEvent.detected_at))
            .limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "kind": row.kind,
            "severity": row.severity,
            "workload_key": row.workload_key,
            "title": row.title,
            "detail": row.detail,
            "evidence": row.evidence,
            "detected_at": row.detected_at.isoformat() if row.detected_at else None,
            "notified": row.notified,
            "notify_skip_reason": row.notify_skip_reason,
        }
        for row in rows
    ]
