"""organization_members + user_refresh_tokens (S8.1 auth hardening)

Revision ID: c7e2a4f81b63
Revises: d5a1b9c7e3f2
Create Date: 2026-09-08 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7e2a4f81b63"
down_revision: str | Sequence[str] | None = "d5a1b9c7e3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # S8.1 (build bible §19): self-service signup is "account + workspace" —
    # registration creates the user **and** their organization, and the user
    # becomes the org owner. The membership link (org role: owner/member)
    # lives here; S8.2 builds the membership/invite surface on this table.
    op.create_table(
        "organization_members",
        sa.Column("organization_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "role",
            sa.Enum("owner", "member", name="orgrole", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("organization_id", "user_id"),
    )
    op.create_index(
        op.f("ix_organization_members_user_id"), "organization_members", ["user_id"], unique=False
    )

    # S8.1 (build bible §19): opaque rotating refresh tokens. Only the
    # SHA-256 hash is stored (unique — the lookup key for /auth/refresh);
    # the plaintext is returned exactly once per issuance and never
    # persisted, logged or audited (§17). ``family_id`` groups one login
    # chain for reuse detection (a revoked token revokes its family).
    op.create_table(
        "user_refresh_tokens",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("family_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        op.f("ix_user_refresh_tokens_user_id"), "user_refresh_tokens", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_refresh_tokens_family_id"), "user_refresh_tokens", ["family_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_user_refresh_tokens_family_id"), table_name="user_refresh_tokens")
    op.drop_index(op.f("ix_user_refresh_tokens_user_id"), table_name="user_refresh_tokens")
    op.drop_table("user_refresh_tokens")
    op.drop_index(op.f("ix_organization_members_user_id"), table_name="organization_members")
    op.drop_table("organization_members")
