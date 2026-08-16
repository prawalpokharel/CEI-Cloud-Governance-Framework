"""
Credentials: passwords, API keys, and dashboard sessions.

Two different secrets with two different threat models, handled differently
on purpose:

* **Passwords** are low-entropy and human-chosen, so they get Argon2id — a
  deliberately slow, memory-hard KDF. Slowness is the point.
* **API keys** are 256 bits of CSPRNG output. There is nothing to brute-force,
  so they get a single SHA-256. A slow KDF here would add latency to every
  ingest request while buying no security at all.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

# Identifies the key's environment and makes keys greppable in logs and
# recognisable to secret scanners (GitHub's scanning partner programme keys
# off exactly this kind of fixed prefix).
API_KEY_PREFIX = "co_live_"

# Bytes of the token retained in cleartext, as an indexed lookup handle. Long
# enough to be effectively unique, far too short to help an attacker.
PREFIX_HANDLE_CHARS = 12


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def password_problems(password: str) -> list[str]:
    """
    Length-first policy.

    Composition rules ("must contain a symbol") push people toward
    Password1! and are not applied. NIST 800-63B says the same.
    """
    problems = []
    if len(password) < 12:
        problems.append("Password must be at least 12 characters")
    if len(password) > 200:
        problems.append("Password must be at most 200 characters")
    return problems


# --------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------

def generate_api_key() -> tuple[str, str, str]:
    """
    Return (full_key, prefix_handle, key_hash).

    The full key is shown to the user exactly once and never stored. Only the
    handle and the hash are persisted, so a database disclosure does not yield
    working credentials.
    """
    body = secrets.token_urlsafe(32)
    full_key = f"{API_KEY_PREFIX}{body}"
    handle = f"{API_KEY_PREFIX}{body[:PREFIX_HANDLE_CHARS]}"
    return full_key, handle, hash_api_key(full_key)


def hash_api_key(full_key: str) -> str:
    return hashlib.sha256(full_key.encode("utf-8")).hexdigest()


def api_key_handle(full_key: str) -> str | None:
    """Derive the lookup handle from a presented key, for the indexed fetch."""
    if not full_key.startswith(API_KEY_PREFIX):
        return None
    body = full_key[len(API_KEY_PREFIX):]
    if len(body) < PREFIX_HANDLE_CHARS:
        return None
    return f"{API_KEY_PREFIX}{body[:PREFIX_HANDLE_CHARS]}"


def api_key_matches(presented: str, stored_hash: str) -> bool:
    """Constant-time comparison, so timing cannot be used to guess the hash."""
    return hmac.compare_digest(hash_api_key(presented), stored_hash)


# --------------------------------------------------------------------------
# Dashboard sessions
# --------------------------------------------------------------------------

SESSION_TTL = timedelta(days=7)


class SecretNotConfigured(RuntimeError):
    pass


def _session_secret() -> str:
    """
    Read APP_SECRET_KEY lazily, and never fall back to a literal.

    Lazy rather than checked at import: this service also serves the
    unauthenticated scenario endpoints that back the USPTO/NIW evidence
    pages. Refusing to start over a secret those endpoints never use would
    take them offline to fix a problem they do not have.
    """
    secret = os.environ.get("APP_SECRET_KEY", "").strip()
    if not secret:
        raise SecretNotConfigured(
            "APP_SECRET_KEY is not set; dashboard authentication is disabled"
        )
    if len(secret) < 32:
        raise SecretNotConfigured(
            "APP_SECRET_KEY must be at least 32 characters"
        )
    return secret


def issue_session(user_id: str, tenant_id: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "tenant": str(tenant_id),
            "iat": now,
            "exp": now + SESSION_TTL,
        },
        _session_secret(),
        algorithm="HS256",
    )


def read_session(token: str) -> dict | None:
    try:
        return jwt.decode(token, _session_secret(), algorithms=["HS256"])
    except SecretNotConfigured:
        raise
    except jwt.PyJWTError:
        return None
