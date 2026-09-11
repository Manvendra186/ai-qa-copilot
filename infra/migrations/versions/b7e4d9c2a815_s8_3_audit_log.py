"""audit_log — append-only security audit trail (S8.3 RBAC hardening)

Revision ID: b7e4d9c2a815
Revises: f3a9c2d81b57
Create Date: 2026-09-11 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e4d9c2a815"
down_revision: str | Sequence[str] | None = "f3a9c2d81b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # S8.3 (build bible §19/§17): one row per audited security event —
    # who (``actor_id``, NULL when the actor is unknown, e.g. a login
    # failure), what (``action`` — the closed AuditAction vocabulary),
    # against what (``target``: org/project/user id or email — never a
    # credential), the ``outcome``, the client ``ip`` and the server
    # ``at`` timestamp.
    #
    # Append-only by contract: the API exposes no update/delete path for
    # this table, and ``actor_id`` is ON DELETE SET NULL so self-deletion
    # and org deletion never erase history ("audit rows remain", §17).
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("actor_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_actor_id"), "audit_log", ["actor_id"], unique=False)
    op.create_index(op.f("ix_audit_log_target"), "audit_log", ["target"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_audit_log_target"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_actor_id"), table_name="audit_log")
    op.drop_table("audit_log")
