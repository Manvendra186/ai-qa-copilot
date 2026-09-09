"""User + membership lookups (build bible §31.3, S0.8 + §19 S8.2).

The auth baseline is project-scoped: a user's role comes from the
``project_members`` row for that project, not from ``users.role``. These
helpers are the single DB entry point for the API's auth dependencies
(:mod:`qa_copilot_api.auth`) and for the seed script.

S8.2 (build bible §19 S8.2) generalizes project access: organization
membership is the **baseline** role for every project in the organization
(``get_project_role`` falls back to the caller's org role when no explicit
``project_members`` row exists), and an explicit ``project_members`` row
**always takes priority** — it can narrow *or* widen the baseline.
"""

from __future__ import annotations

from qa_copilot_domain.enums import OrgRole, ProjectRole
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "ORG_ROLE_TO_PROJECT_ROLE",
    "get_org_owner_id",
    "get_org_role",
    "get_project_role",
    "get_user_by_email",
    "user_orgs",
]

#: S8.2 access model (bible §19): org role → baseline project role for the
#: org's projects (used only when no explicit ``project_members`` row wins).
ORG_ROLE_TO_PROJECT_ROLE: dict[OrgRole, ProjectRole] = {
    OrgRole.OWNER: ProjectRole.OWNER,
    OrgRole.MEMBER: ProjectRole.MEMBER,
}


def get_user_by_email(session: Session, email: str) -> models.User | None:
    """Look up a user by exact email (login, §31.3 dev-mode single user)."""
    return session.scalars(select(models.User).where(models.User.email == email)).first()


def user_orgs(session: Session, user_id: str) -> list[models.OrganizationMember]:
    """The user's organization memberships, ordered by org name (S8.1 /me)."""
    return list(
        session.scalars(
            select(models.OrganizationMember)
            .join(
                models.Organization,
                models.Organization.id == models.OrganizationMember.organization_id,
            )
            .where(models.OrganizationMember.user_id == user_id)
            .order_by(models.Organization.name, models.OrganizationMember.organization_id)
        ).all()
    )


def get_org_role(session: Session, organization_id: str, user_id: str) -> OrgRole | None:
    """The user's org role in the org (S8.2), or ``None`` when not a member."""
    return session.scalar(
        select(models.OrganizationMember.role).where(
            and_(
                models.OrganizationMember.organization_id == organization_id,
                models.OrganizationMember.user_id == user_id,
            )
        )
    )


def get_org_owner_id(session: Session, organization_id: str) -> str | None:
    """The org's single ``owner`` user id (S8.2: one owner per org), else ``None``."""
    return session.scalar(
        select(models.OrganizationMember.user_id).where(
            and_(
                models.OrganizationMember.organization_id == organization_id,
                models.OrganizationMember.role == OrgRole.OWNER,
            )
        )
    )


def get_project_role(session: Session, project_id: str, user_id: str) -> str | None:
    """The user's effective role for a project, or ``None`` when not a member.

    S8.2 access model (bible §19): an explicit ``project_members`` row always
    takes priority; without one, the user's **organization** role for the
    project's organization is the baseline (org ``owner`` → project
    ``owner``, org ``member`` → project ``member``).

    Returns the stored wire string (``owner`` / ``member`` / ``viewer``);
    the caller validates it against
    :class:`~qa_copilot_domain.enums.ProjectRole`.
    """
    explicit = session.scalars(
        select(models.ProjectMember.role).where(
            and_(
                models.ProjectMember.project_id == project_id,
                models.ProjectMember.user_id == user_id,
            )
        )
    ).first()
    if explicit is not None:
        return explicit
    project = session.get(models.Project, project_id)
    if project is None or project.organization_id is None:
        return None
    org_role = get_org_role(session, project.organization_id, user_id)
    if org_role is None:
        return None
    return ORG_ROLE_TO_PROJECT_ROLE.get(org_role, OrgRole.MEMBER).value
