"""HTTP routes (build bible §7).

S0.3: ``GET /health`` (inline in ``main.py``).
S0.8: auth baseline (§31.3) — ``POST /api/v1/auth/login``,
``GET /api/v1/auth/me`` and project endpoints gated by project-scoped roles:

- ``GET /api/v1/projects``          — auth (any member)
- ``GET /api/v1/projects/{id}``     — ``viewer`` or above
- ``DELETE /api/v1/projects/{id}``  — ``owner`` (§31.3: project deletion)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from qa_copilot_domain.enums import ProjectRole
from qa_copilot_repository import models
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import auth, schemas
from .db import get_db

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
projects_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _user_out(user: models.User) -> schemas.UserOut:
    return schemas.UserOut(id=user.id, email=user.email, role=user.role)


def _member_projects(db: Session, user: models.User) -> list[schemas.ProjectRef]:
    """The caller's project memberships (role from ``project_members``)."""
    rows = db.execute(
        select(models.Project, models.ProjectMember.role)
        .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == user.id)
        .order_by(models.Project.name)
    ).all()
    return [
        schemas.ProjectRef(id=project.id, name=project.name, role=role) for project, role in rows
    ]


# --- auth ---------------------------------------------------------------------


@auth_router.post("/login", response_model=schemas.TokenResponse)
def login(
    body: schemas.LoginRequest,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.TokenResponse:
    """Dev-mode login (§31.3): email + password → HS256 Bearer token."""
    settings = request.app.state.settings
    try:
        secret = auth._require_secret(settings)
    except RuntimeError as exc:
        # fail loud with a readable body instead of a bare 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    user = db.scalar(select(models.User).where(models.User.email == body.email))
    if user is None or not auth.check_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = auth.create_access_token(user.id, user.email, secret)
    return schemas.TokenResponse(
        token=token,
        expires_in=int(auth.TOKEN_TTL.total_seconds()),
        user=_user_out(user),
        projects=_member_projects(db, user),
    )


@auth_router.get("/me", response_model=schemas.MeResponse)
def me(
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.MeResponse:
    """The authenticated user + their project roles (401 without a valid token)."""
    return schemas.MeResponse(user=_user_out(user), projects=_member_projects(db, user))


# --- projects (role-gated, §31.3) ---------------------------------------------


@projects_router.get("", response_model=list[schemas.ProjectRef])
def list_projects(
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.ProjectRef]:
    """Projects the caller holds any role in (auth required)."""
    return _member_projects(db, user)


@projects_router.get("/{project_id}", response_model=schemas.ProjectOut)
def get_project(
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.VIEWER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.ProjectOut:
    """Project detail — ``viewer`` or above (401 unauthenticated, 403 non-member)."""
    _, project_id = ctx
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return schemas.ProjectOut(id=project.id, name=project.name, settings=project.settings)


@projects_router.delete("/{project_id}", status_code=204)
def delete_project(
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a project — ``owner`` only (§31.3). Memberships cascade per schema FKs."""
    _, project_id = ctx
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    # ORM: remove membership rows first — the ORM would otherwise try to
    # null out the composite PK instead of relying on ON DELETE CASCADE.
    db.execute(delete(models.ProjectMember).where(models.ProjectMember.project_id == project_id))
    db.delete(project)
    db.commit()
