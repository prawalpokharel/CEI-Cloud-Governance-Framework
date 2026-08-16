"""
Snapshot transport.

Outbound HTTPS only. The agent opens no listening port, so installing it does
not expand the cluster's attack surface -- a property worth preserving even
when it would be convenient to expose a health endpoint.
"""

from __future__ import annotations

import gzip
import logging
import random
import time
from typing import Any

import urllib3

from .version import AGENT_VERSION

log = logging.getLogger(__name__)

# Retrying these is pointless -- the request is wrong, not unlucky. Retrying
# a 401 on a revoked key just generates noise in the audit log.
#
# 409 is included: it means this cluster is already registered under another
# record, or the key is bound elsewhere. No amount of retrying resolves
# either, and the operator needs to see the message rather than watch it
# scroll past five times.
_FATAL_STATUSES = {400, 401, 403, 404, 409, 413, 422}


class IngestError(Exception):
    def __init__(self, message: str, status: int | None = None, fatal: bool = False):
        super().__init__(message)
        self.status = status
        self.fatal = fatal


class Transport:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        *,
        verify_tls: bool = True,
        timeout: int = 30,
        max_retries: int = 5,
    ):
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.pool = urllib3.PoolManager(
            cert_reqs="CERT_REQUIRED" if verify_tls else "CERT_NONE",
            retries=False,  # retry policy is handled here, with backoff
            timeout=urllib3.Timeout(connect=10.0, read=timeout),
        )
        if not verify_tls:
            urllib3.disable_warnings()
            log.warning(
                "TLS verification is disabled. Snapshots may be intercepted; "
                "use this only against a local development endpoint."
            )

    def send_snapshot(self, payload: bytes) -> dict[str, Any]:
        """
        POST a snapshot, retrying transient failures with exponential backoff
        and jitter.

        Jitter matters more than usual here: every agent wakes on the same
        interval, so a brief server outage would otherwise synchronise the
        entire fleet into retrying in lockstep.
        """
        body = gzip.compress(payload, compresslevel=6)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "User-Agent": f"cloudoptimizer-agent/{AGENT_VERSION}",
        }
        url = f"{self.endpoint}/v1/ingest"

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.pool.request(
                    "POST", url, body=body, headers=headers
                )
            except Exception as exc:
                last_error = IngestError(f"connection failed: {exc}")
                log.warning(
                    "Ingest attempt %d/%d failed: %s",
                    attempt, self.max_retries, exc,
                )
            else:
                if 200 <= response.status < 300:
                    return self._decode(response)

                message = self._error_text(response)
                if response.status in _FATAL_STATUSES:
                    raise IngestError(message, response.status, fatal=True)

                last_error = IngestError(message, response.status)
                log.warning(
                    "Ingest attempt %d/%d failed: HTTP %d %s",
                    attempt, self.max_retries, response.status, message,
                )

            if attempt < self.max_retries:
                delay = min(60.0, 2 ** attempt) * (0.5 + random.random())
                time.sleep(delay)

        raise last_error or IngestError("ingest failed")

    @staticmethod
    def _decode(response) -> dict[str, Any]:
        import json
        try:
            return json.loads(response.data.decode("utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _error_text(response) -> str:
        try:
            return response.data.decode("utf-8", errors="replace")[:300]
        except Exception:
            return "<unreadable response>"
