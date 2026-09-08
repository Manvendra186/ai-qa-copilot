"""failures.jira_issue_key (S7.4 Jira linking)

Revision ID: d5a1b9c7e3f2
Revises: b3d7e2a91c4f
Create Date: 2026-09-07 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5a1b9c7e3f2"
down_revision: str | Sequence[str] | None = "b3d7e2a91c4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # S7.4 (build bible §19): the linked Jira issue key (``PROJECT-123``) on
    # each failure. It is the stable identity the ``jira_link`` job persists for
    # create-or-update idempotency (a re-link updates the issue in place, never
    # duplicates). Nullable — a failure is only linked once the job runs against
    # it — and indexed so a project's linked failures are cheap to query.
    op.add_column(
        "failures",
        sa.Column("jira_issue_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        op.f("ix_failures_jira_issue_key"),
        "failures",
        ["jira_issue_key"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_failures_jira_issue_key"), table_name="failures")
    op.drop_column("failures", "jira_issue_key")
