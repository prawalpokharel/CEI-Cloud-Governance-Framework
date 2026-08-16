"""
Agent configuration.

Everything is supplied by environment variable so the Helm chart is the only
place configuration lives. No config file, no flags -- a pod that needs a
rebuilt image to change its poll interval is a pod nobody will tune.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    raw = os.environ.get(name, "").strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class AgentConfig:
    # --- required ---------------------------------------------------------
    api_key: str = ""
    endpoint: str = "https://api.cloudoptimizer.app"

    # --- cadence ----------------------------------------------------------
    # Full snapshots rather than event streams: idempotent, restart-safe, and
    # free of event-ordering bugs. 60s is frequent enough that the topology
    # map feels live without making the payload the dominant cost.
    interval_seconds: int = 60

    # --- scoping ----------------------------------------------------------
    # Empty means all namespaces. Operators who want to trial the agent on
    # one namespace first should not have to install a second time.
    namespaces: list[str] = field(default_factory=list)
    exclude_namespaces: list[str] = field(default_factory=list)

    # --- behaviour --------------------------------------------------------
    collect_metrics: bool = True
    # Off by default. Enabling it makes the agent transmit raw environment
    # variable VALUES, which routinely contain credentials. See redact.py.
    send_raw_env: bool = False

    verify_tls: bool = True
    request_timeout_seconds: int = 30
    max_retries: int = 5
    log_level: str = "INFO"

    # Run one collect-and-send cycle then exit. Used by CI and by operators
    # verifying a fresh install without waiting out an interval.
    once: bool = False

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            api_key=os.environ.get("CLOUDOPTIMIZER_API_KEY", "").strip(),
            endpoint=os.environ.get(
                "CLOUDOPTIMIZER_ENDPOINT", "https://api.cloudoptimizer.app"
            ).strip().rstrip("/"),
            interval_seconds=max(10, _int("CLOUDOPTIMIZER_INTERVAL_SECONDS", 60)),
            namespaces=_list("CLOUDOPTIMIZER_NAMESPACES"),
            exclude_namespaces=_list("CLOUDOPTIMIZER_EXCLUDE_NAMESPACES"),
            collect_metrics=_bool("CLOUDOPTIMIZER_COLLECT_METRICS", True),
            send_raw_env=_bool("CLOUDOPTIMIZER_SEND_RAW_ENV", False),
            verify_tls=_bool("CLOUDOPTIMIZER_VERIFY_TLS", True),
            request_timeout_seconds=_int("CLOUDOPTIMIZER_TIMEOUT_SECONDS", 30),
            max_retries=_int("CLOUDOPTIMIZER_MAX_RETRIES", 5),
            log_level=os.environ.get("CLOUDOPTIMIZER_LOG_LEVEL", "INFO").upper(),
            once=_bool("CLOUDOPTIMIZER_ONCE", False),
        )

    def validate(self) -> list[str]:
        problems = []
        if not self.api_key:
            problems.append(
                "CLOUDOPTIMIZER_API_KEY is not set. Create a cluster in the "
                "dashboard to get one."
            )
        if not self.endpoint.startswith(("http://", "https://")):
            problems.append(
                f"CLOUDOPTIMIZER_ENDPOINT must be an http(s) URL, got "
                f"{self.endpoint!r}"
            )
        if self.send_raw_env:
            problems.append(
                "CLOUDOPTIMIZER_SEND_RAW_ENV is enabled. Environment variable "
                "values frequently contain credentials and will be "
                "transmitted. Unset this unless you have audited every "
                "workload in scope."
            )
        return problems

    def wants_namespace(self, namespace: str) -> bool:
        if namespace in self.exclude_namespaces:
            return False
        if self.namespaces:
            return namespace in self.namespaces
        return True
