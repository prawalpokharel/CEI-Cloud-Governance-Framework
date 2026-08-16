"""
CloudOptimizer Phase 1 schema.

Scope: what the in-cluster agent needs to authenticate, report, and be
rendered — tenants, users, clusters, API keys, snapshots, derived workload
samples, and an append-only audit log.

Two conventions applied throughout:

* **tenant_id on every table from the start.** There is one customer today,
  but retrofitting tenancy onto a populated schema is a migration nobody
  enjoys. It costs a column now and saves a rewrite later.
* **Timezone-aware timestamps everywhere.** Naive timestamps silently mix
  server-local and UTC once anything runs in more than one place.

Identifier strategy: UUIDs for anything that appears in a URL or an API
response, so ids are not enumerable and can be generated client-side.
BIGSERIAL for the two append-heavy tables, where a monotonic integer is
cheaper to index and never leaves the backend.
"""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


TZDateTime = DateTime(timezone=True)


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

class ClusterProvider(str, enum.Enum):
    """Detected from node labels at ingest; unknown until the first report."""

    eks = "eks"
    aks = "aks"
    gke = "gke"
    kind = "kind"
    k3s = "k3s"
    openshift = "openshift"
    other = "other"
    unknown = "unknown"


class ActorType(str, enum.Enum):
    user = "user"
    agent = "agent"
    system = "system"


# --------------------------------------------------------------------------
# Tenancy and identity
# --------------------------------------------------------------------------

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[object] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="tenant")
    clusters: Mapped[list["Cluster"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Email is normalized to lowercase by the application before it
        # reaches the database, so a plain unique constraint gives
        # case-insensitive uniqueness without requiring the citext extension
        # (not enabled by default on managed Postgres).
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Stored already lowercased by the application layer.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))

    email_verified_at: Mapped[object | None] = mapped_column(TZDateTime)
    last_login_at: Mapped[object | None] = mapped_column(TZDateTime)
    created_at: Mapped[object] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="users")


# --------------------------------------------------------------------------
# Clusters and agent credentials
# --------------------------------------------------------------------------

class Cluster(Base):
    __tablename__ = "clusters"
    __table_args__ = (
        # The agent reports the kube-system namespace UID, the conventional
        # stable fingerprint for a cluster. Unique per tenant so the same
        # cluster cannot be registered twice, while two tenants monitoring
        # genuinely separate clusters never collide.
        UniqueConstraint("tenant_id", "cluster_uid", name="uq_clusters_tenant_uid"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # Null until the agent's first successful ingest.
    cluster_uid: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[ClusterProvider] = mapped_column(
        SAEnum(ClusterProvider, name="cluster_provider"),
        nullable=False,
        default=ClusterProvider.unknown,
        server_default=ClusterProvider.unknown.value,
    )
    k8s_version: Mapped[str | None] = mapped_column(String(40))
    agent_version: Mapped[str | None] = mapped_column(String(40))

    # Whether metrics-server was reachable on the most recent report. Drives
    # the "metrics unavailable" state in the UI, since CPU/memory actuals are
    # absent without it and several figures degrade to requests/limits.
    metrics_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    last_seen_at: Mapped[object | None] = mapped_column(TZDateTime, index=True)
    created_at: Mapped[object] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )

    tenant: Mapped[Tenant] = relationship(back_populates="clusters")
    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="cluster")


class ApiKey(Base):
    """
    Agent credential.

    The plaintext key is shown once at creation and never stored. Lookup is
    by ``prefix`` (indexed, non-secret) followed by a hash comparison, so
    authenticating a request is a single indexed row fetch rather than a scan
    with a hash check on every row.
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("prefix", name="uq_api_keys_prefix"),
        CheckConstraint("length(prefix) >= 8", name="ck_api_keys_prefix_len"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Non-secret leading segment, e.g. "co_live_a1b2c3d4". Safe to display
    # and to log; used to locate the row.
    prefix: Mapped[str] = mapped_column(String(40), nullable=False)
    # SHA-256 of the full key. Not a password hash on purpose: these are
    # 256-bit random tokens, so there is nothing to brute-force and a slow
    # KDF would only add latency to every ingest request.
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    label: Mapped[str | None] = mapped_column(String(120))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[object] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[object | None] = mapped_column(TZDateTime)
    revoked_at: Mapped[object | None] = mapped_column(TZDateTime)

    cluster: Mapped[Cluster] = relationship(back_populates="api_keys")


# --------------------------------------------------------------------------
# Agent reports
# --------------------------------------------------------------------------

class Snapshot(Base):
    """
    One full cluster report from the agent.

    Full snapshots rather than incremental watch events: they are idempotent,
    survive agent restarts, and have no event-ordering failure mode. The cost
    is bandwidth, which is bounded by the 60s cadence and gzip.

    Retention is short (~24h). The durable signal is extracted into
    workload_samples at ingest; the raw payload exists for debugging and
    replay, not as the system of record.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        # Idempotency key is the capture instant, NOT seq.
        #
        # seq is a within-run counter that resets when the agent pod
        # restarts, so keying on it meant a restarted agent produced seq=1
        # again and every subsequent snapshot was rejected as a duplicate --
        # the agent would go permanently silent after its first restart.
        #
        # captured_at is stamped once per collection and resent byte-identical
        # on a transport retry, which is exactly the semantic wanted: dedupe
        # retries of one observation, accept genuinely new observations.
        UniqueConstraint(
            "cluster_id", "captured_at", name="uq_snapshots_cluster_captured"
        ),
        Index("ix_snapshots_cluster_received", "cluster_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Agent-side monotonic counter, reset on agent restart.
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Agent clock; may skew from the server's.
    captured_at: Mapped[object] = mapped_column(TZDateTime, nullable=False)
    # Server clock. Ordering and retention use this one.
    received_at: Mapped[object] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now()
    )

    agent_version: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    node_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pod_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    workload_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class WorkloadSample(Base):
    """
    Per-workload utilization extracted from each snapshot.

    This is the table the CEI entropy term reads. The engine's Shannon
    entropy needs a distribution over time, and a freshly installed agent has
    no history — the previous behavior of fabricating one produced a score
    built on random numbers. Accumulating real samples here is what lets
    entropy be computed honestly, and lets the UI state how much history
    exists rather than implying ninety days of it.

    Also the direct input to Phase 2's requested-vs-used waste detection.
    """

    __tablename__ = "workload_samples"
    __table_args__ = (
        Index(
            "ix_workload_samples_lookup",
            "cluster_id",
            "workload_key",
            "observed_at",
        ),
        UniqueConstraint(
            "cluster_id",
            "workload_key",
            "observed_at",
            name="uq_workload_samples_point",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Stable identity across restarts: "<namespace>/<kind>/<name>". Pod names
    # churn; workload identity does not.
    workload_key: Mapped[str] = mapped_column(String(512), nullable=False)
    namespace: Mapped[str] = mapped_column(String(253), nullable=False)

    observed_at: Mapped[object] = mapped_column(TZDateTime, nullable=False)

    # Nullable throughout: metrics-server may be absent, in which case only
    # the requested/limit values are known. Storing null is honest; storing
    # zero would be indistinguishable from a genuinely idle workload.
    cpu_cores_used: Mapped[float | None] = mapped_column(Numeric(12, 6))
    cpu_cores_requested: Mapped[float | None] = mapped_column(Numeric(12, 6))
    mem_bytes_used: Mapped[int | None] = mapped_column(BigInteger)
    mem_bytes_requested: Mapped[int | None] = mapped_column(BigInteger)

    replicas_ready: Mapped[int | None] = mapped_column(Integer)
    replicas_desired: Mapped[int | None] = mapped_column(Integer)


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

class AuditLog(Base):
    """
    Append-only record of consequential actions.

    Written from day one because it is cheap now and expensive to backfill:
    once the product is live, the events you most want to review are the ones
    that already happened.

    No ORM relationships and no cascade deletes — an audit row must survive
    the deletion of whatever it describes, so actor and target are recorded
    as loose identifiers rather than foreign keys.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_log_action", "action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    actor_type: Mapped[ActorType] = mapped_column(
        SAEnum(ActorType, name="actor_type"), nullable=False
    )
    actor_id: Mapped[str | None] = mapped_column(String(120))

    # Dotted verb, e.g. "api_key.created", "cluster.ingested", "user.login".
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(60))
    target_id: Mapped[str | None] = mapped_column(String(120))

    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[object] = mapped_column(
        TZDateTime, nullable=False, server_default=func.now(), index=True
    )
