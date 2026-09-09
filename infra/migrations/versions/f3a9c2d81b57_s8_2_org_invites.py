"""organization_invites + single-owner constraint (S8.2 teams)

Revision ID: f3a9c2d81b57
Revises: c7e2a4f81b63
Create Date: 2026-09-09 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c2d81b57"
down_revision: str | Sequence[str] | None = "c7e2a4f81b63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # S8.2 (build bible §19): one-time, code-based organization invites.
    # Local-first (§29 — no SMTP): the owner creates the invite and receives
    # a single-use ``code``; only its SHA-256 hash is stored (unique — the
    # lookup key for /invites/{code}/accept; §17). The code expires 7 days
    # out (``expires_at``) and is consumed on accept (``accepted_at`` /
    # ``accepted_by``).
    op.create_table(
        "organization_invites",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "member", name="orgrole", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Uuid(as_uuid=False), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["accepted_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(
        op.f("ix_organization_invites_organization_id"),
        "organization_invites",
        ["organization_id"],
        unique=False,
    )

    # S8.2 (build bible §19): one ``owner`` per organization, enforced at the
    # database level (a second owner insert fails even under a race).
    op.create_index(
        "uq_organization_members_single_owner",
        "organization_members",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_organization_members_single_owner",
        table_name="organization_members",
        postgresql_where=sa.text("role = 'owner'"),
    )
    op.drop_index(
        op.f("ix_organization_invites_organization_id"), table_name="organization_invites"
    )
    op.drop_table("organization_invites")
