"""
Database engine, session factory, and URL normalization.

Railway supplies DATABASE_URL in libpq form (``postgres://user:pass@host/db``),
which needs two corrections before SQLAlchemy 2.x will accept it:

1. The ``postgres://`` scheme was removed in SQLAlchemy 1.4. Only
   ``postgresql://`` is recognized, so the scheme is rewritten.
2. A driver must be named explicitly. The application runs on asyncpg;
   Alembic migrations run on psycopg2, because migrations are synchronous and
   an async migration environment buys nothing here.

asyncpg also rejects libpq-style query parameters that psycopg2 accepts --
``sslmode`` in particular, which Railway and most managed providers append.
Those are stripped for the async URL and translated to asyncpg's own connect
argument.
"""

from __future__ import annotations

import os
from typing import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all CloudOptimizer tables."""


# Query parameters that libpq/psycopg2 understand but asyncpg does not accept
# as DSN arguments.
_LIBPQ_ONLY_PARAMS = {
    "sslmode",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "target_session_attrs",
    "connect_timeout",
    "application_name",
    "options",
}


def _split_scheme(url: str) -> tuple[str, str]:
    """Return (normalized_url_without_driver, original_query_string)."""
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme in ("postgres", "postgresql"):
        scheme = "postgresql"
    elif scheme.startswith("postgresql+"):
        scheme = "postgresql"
    return (
        urlunsplit((scheme, parts.netloc, parts.path, "", "")),
        parts.query,
    )


def async_url(raw: str) -> str:
    """Normalize a DATABASE_URL for the asyncpg driver."""
    base, query = _split_scheme(raw)
    kept = [(k, v) for k, v in parse_qsl(query) if k not in _LIBPQ_ONLY_PARAMS]
    parts = urlsplit(base)
    return urlunsplit(
        ("postgresql+asyncpg", parts.netloc, parts.path, urlencode(kept), "")
    )


def sync_url(raw: str) -> str:
    """Normalize a DATABASE_URL for the psycopg2 driver (Alembic)."""
    base, query = _split_scheme(raw)
    parts = urlsplit(base)
    return urlunsplit(
        ("postgresql+psycopg2", parts.netloc, parts.path, query, "")
    )


def requires_ssl(raw: str) -> bool:
    """True when the source URL asked for TLS via libpq's sslmode."""
    _, query = _split_scheme(raw)
    mode = dict(parse_qsl(query)).get("sslmode", "")
    return mode in ("require", "verify-ca", "verify-full")


def database_url() -> str:
    """
    Read DATABASE_URL, failing loudly rather than silently falling back.

    A default here would let a misconfigured deployment start up and write to
    the wrong database, which is worse than not starting at all.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. On Railway this is provided by the "
            "Postgres service; locally, see core-engine/.env.example."
        )
    return url


# --------------------------------------------------------------------------
# Engine / session
# --------------------------------------------------------------------------

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    """Lazily build the process-wide async engine."""
    global _engine
    if _engine is None:
        raw = database_url()
        connect_args = {}
        if requires_ssl(raw):
            # asyncpg spells this differently from libpq.
            connect_args["ssl"] = True
        _engine = create_async_engine(
            async_url(raw),
            connect_args=connect_args,
            # Managed Postgres closes idle connections; recycling below the
            # usual server-side idle timeout avoids handing out dead ones.
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=10,
            echo=os.environ.get("SQL_ECHO") == "1",
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that rolls back on error."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close pooled connections on shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
