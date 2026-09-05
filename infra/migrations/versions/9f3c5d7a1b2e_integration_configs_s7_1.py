"""integration_configs (S7.1 external integrations)

Revision ID: 9f3c5d7a1b2e
Revises: 7e9a4b2c1d3f
Create Date: 2026-09-02 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f3c5d7a1b2e"
down_revision: str | Sequence[str] | None = "7e9a4b2c1d3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # S7.1 (build bible §19): per-project integration config. One row per
    # (project_id, provider). token_ref names where the secret lives — the
    # secret itself is never stored here (build bible §17).
    op.create_table(
        "integration_configs",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=1024), nullable=True),
        sa.Column("token_ref", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "provider", name="uq_integration_configs_project_provider"
        ),
    )
    op.create_index(
        op.f("ix_integration_configs_project_id"),
        "integration_configs",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_integration_configs_project_id"), table_name="integration_configs")
    op.drop_table("integration_configs")
