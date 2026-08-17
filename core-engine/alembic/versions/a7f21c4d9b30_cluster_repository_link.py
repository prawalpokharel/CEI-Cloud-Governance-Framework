"""Link a cluster to the repository whose manifests describe it.

Needed by the GitHub webhook: an inbound pull_request delivery names a
repository, and the analysis needs the dependency graph of the cluster that
repository deploys to. Nullable because existing clusters have no link and
the webhook simply declines to analyse until an operator sets one -- guessing
would analyse a pull request against the wrong cluster's graph and report
impact that is confident and entirely fictional.

Indexed but not unique: a monorepo can legitimately describe several
clusters. The webhook treats an ambiguous match as no match.

Revision ID: a7f21c4d9b30
Revises: eef9306a7cd5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7f21c4d9b30"
down_revision: Union[str, Sequence[str], None] = "eef9306a7cd5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clusters", sa.Column("repository", sa.String(255), nullable=True))
    op.create_index("ix_clusters_repository", "clusters", ["repository"])


def downgrade() -> None:
    op.drop_index("ix_clusters_repository", table_name="clusters")
    op.drop_column("clusters", "repository")
