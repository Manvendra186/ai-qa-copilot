"""webhook_events (S7.3 CI/CD webhook)

Revision ID: b3d7e2a91c4f
Revises: 9f3c5d7a1b2e
Create Date: 2026-09-05 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d7e2a91c4f"
down_revision: str | Sequence[str] | None = "9f3c5d7a1b2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # S7.3 (build bible §19): one row per inbound webhook delivery.
    # delivery_id is the sender's delivery identifier (X-GitHub-Delivery)
    # and is unique — the dedupe gate: a re-sent delivery answers 200 and
    # must not spawn a second job.
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("delivery_id", sa.String(length=255), nullable=False),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=True),
        sa.Column("job_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="uq_webhook_events_delivery_id"),
    )
    op.create_index(
        op.f("ix_webhook_events_project_id"),
        "webhook_events",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_webhook_events_project_id"), table_name="webhook_events")
    op.drop_table("webhook_events")
