"""
Audit logging.

Written from day one because it is cheap now and cannot be backfilled: once
the product is live, the events you most want to review are the ones that
already happened.

Audit writes never raise into the caller. An audit failure must not fail the
operation being audited -- losing one log line is bad, refusing a customer's
ingest because a log line could not be written is worse.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import ActorType, AuditLog

log = logging.getLogger(__name__)


async def record(
    session: AsyncSession,
    *,
    action: str,
    actor_type: ActorType,
    actor_id: str | None = None,
    tenant_id: Any = None,
    target_type: str | None = None,
    target_id: str | None = None,
    source_ip: str | None = None,
    user_agent: str | None = None,
    details: dict | None = None,
) -> None:
    """
    Append an audit entry to the caller's transaction.

    Deliberately joins the caller's session rather than opening its own, so
    the audit row commits atomically with the thing it describes. An
    "api_key.created" entry for a key that was rolled back would be worse
    than no entry.
    """
    try:
        session.add(
            AuditLog(
                tenant_id=tenant_id,
                actor_type=actor_type,
                actor_id=str(actor_id) if actor_id else None,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                source_ip=source_ip,
                user_agent=(user_agent or "")[:1000] or None,
                details=details,
            )
        )
    except Exception:
        log.exception("Failed to record audit event %s", action)


def client_ip(request) -> str | None:
    """
    Best-effort client address.

    Railway terminates TLS upstream, so the socket peer is the proxy. The
    left-most X-Forwarded-For entry is the original client. It is
    caller-supplied and therefore spoofable -- fine for an audit trail, not
    to be used for authorization.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None
