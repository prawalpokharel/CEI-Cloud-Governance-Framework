"""
Egress redaction.

The single most important module in the agent from a trust standpoint.

To infer dependency edges, the agent has to read container environment
variables -- that is where in-cluster service addresses live. Environment
variables are also where people put database passwords, API tokens, and
signing keys. An open-source, read-only agent that ships those values to a
vendor's servers is exactly the objection that open-sourcing is supposed to
answer.

So the agent does not transmit environment variable values. It parses them
locally and emits only the derived service references. What leaves the
cluster is "workload A refers to service B", never "DATABASE_URL=postgres://
user:hunter2@...".

Names are retained (not values) because they are useful for diagnostics and
are not themselves secret. Even so, a name that looks like a credential is
reported as a redacted marker rather than echoed.
"""

from __future__ import annotations

import re
from typing import Iterable

# Environment variable NAMES whose presence is worth knowing but whose
# spelling should not be echoed verbatim in case the name itself leaks
# structure (e.g. "STRIPE_LIVE_SECRET_KEY_ACCT_1234").
_SENSITIVE_NAME = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|APIKEY|API_KEY|PRIVATE|CREDENTIAL"
    r"|SESSION|AUTH|CERT|SALT|SIGNING|ENCRYPT)",
    re.IGNORECASE,
)

# An in-cluster address as it actually appears in a manifest:
#   productcatalogservice:3550
#   http://cartservice:7070
#   redis-cart.default.svc.cluster.local:6379
#
# Anchored at both ends so a bare port ("8080") or an arbitrary token does not
# match. The earlier spike version matched loosely and produced candidate
# hosts like "8080" and "1"; harmless because no service matched, but noisy.
_ADDRESS = re.compile(
    r"^(?:(?P<scheme>[a-z][a-z0-9+.-]*)://)?"
    r"(?P<host>[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)"
    r"(?:\.(?P<namespace>[a-z0-9](?:[-a-z0-9]*[a-z0-9])?))?"
    r"(?:\.svc(?:\.cluster\.local)?)?"
    r"(?::(?P<port>\d{1,5}))?"
    r"(?:/[^\s]*)?$",
    re.IGNORECASE,
)

# Hosts that are never an in-cluster dependency.
_IGNORED_HOSTS = {
    "localhost", "127", "0", "kubernetes", "kubernetes.default",
    "metadata", "169", "true", "false", "null", "none",
}


def redact_name(name: str) -> str:
    """Return an env var name safe to transmit."""
    if _SENSITIVE_NAME.search(name):
        return "<redacted>"
    return name


def extract_service_references(
    values: Iterable[str], known_services: set[str]
) -> set[tuple[str, str | None]]:
    """
    Parse env var values into (service_name, namespace) references.

    Only values that resolve to a service the agent has actually observed are
    returned, which keeps arbitrary strings -- including anything secret --
    from ever being emitted. A password that happens to look like a hostname
    still cannot escape unless a Service by that exact name exists.
    """
    found: set[tuple[str, str | None]] = set()
    for value in values:
        if not value or len(value) > 512:
            # Long values are configuration blobs or encoded payloads, not
            # service addresses. Skipping them avoids scanning secrets.
            continue
        for token in re.split(r"[,\s;|]+", value.strip()):
            if not token or len(token) > 253:
                continue
            match = _ADDRESS.match(token)
            if not match:
                continue
            host = (match.group("host") or "").lower()
            if not host or host in _IGNORED_HOSTS or host.isdigit():
                continue
            if host not in known_services:
                continue
            found.add((host, match.group("namespace")))
    return found


def safe_env_summary(env_names: Iterable[str]) -> dict:
    """
    Describe a container's environment without disclosing it.

    Reports how many variables exist and how many look credential-bearing,
    which is enough for the UI to explain what the agent saw and for an
    operator to sanity-check the redaction, without transmitting content.
    """
    names = list(env_names)
    sensitive = [n for n in names if _SENSITIVE_NAME.search(n)]
    return {
        "count": len(names),
        "sensitive_count": len(sensitive),
        "names": sorted(redact_name(n) for n in names)[:50],
    }
