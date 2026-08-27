"""User + project-membership lookups (build bible §31.3, S0.8 auth baseline).

The auth baseline is project-scoped: a user's role comes from the
``project_members`` row for that project, not from ``users.role``. These
helpers are the single DB entry point for the API's auth dependencies
(:mod:`qa_copilot_api.auth`) and for the seed script.
"""

from __future__ import annotations

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from . import models

__all__ = ["get_project_role", "get_user_by_email"]


def get_user_by_email(session: Session, email: str) -> models.User | None:
    """Look up a user by exact email (login, §31.3 dev-mode single user)."""
    return session.scalars(select(models.User).where(models.User.email == email)).first()


def get_project_role(session: Session, project_id: str, user_id: str) -> str | None:
    """The user's role for a project, or ``None`` when not a member.

    Returns the stored wire string (``owner`` / ``member`` / ``viewer``);
    the caller validates it against
    :class:`~qa_copilot_domain.enums.ProjectRole`.
    """
    return session.scalars(
        select(models.ProjectMember.role).where(
            and_(
                models.ProjectMember.project_id == project_id,
                models.ProjectMember.user_id == user_id,
            )
        )
    ).first()
