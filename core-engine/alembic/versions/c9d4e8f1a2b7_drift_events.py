"""Drift events and per-snapshot concentration.

The rail for structural-event detection: every accepted snapshot is compared
to its predecessor at ingest, and what changed is recorded here. Snapshots
additionally carry their own concentration value so the trend is one indexed
query rather than N payload loads.

Revision ID: c9d4e8f1a2b7
Revises: a7f21c4d9b30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "c9d4e8f1a2b7"
down_revision: Union[str, Sequence[str], None] = "a7f21c4d9b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drift_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id", UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "cluster_id", UUID(as_uuid=True),
            sa.ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("workload_key", sa.String(255), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("before_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("after_captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notify_skip_reason", sa.String(40), nullable=True),
        sa.Column(
            "detected_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_drift_events_tenant_id", "drift_events", ["tenant_id"])
    op.create_index(
        "ix_drift_events_cluster_detected", "drift_events",
        ["cluster_id", "detected_at"],
    )
    op.create_index(
        "ix_drift_events_debounce", "drift_events",
        ["cluster_id", "kind", "workload_key", "detected_at"],
    )

    op.add_column(
        "snapshots",
        sa.Column("structural_concentration", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("snapshots", "structural_concentration")
    op.drop_index("ix_drift_events_debounce", table_name="drift_events")
    op.drop_index("ix_drift_events_cluster_detected", table_name="drift_events")
    op.drop_index("ix_drift_events_tenant_id", table_name="drift_events")
    op.drop_table("drift_events")
