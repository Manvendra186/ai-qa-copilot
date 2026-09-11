"""HTTP routes (build bible §7).

S0.3: ``GET /health`` (inline in ``main.py``).
S0.8: auth baseline (§31.3) — ``POST /api/v1/auth/login``,
``GET /api/v1/auth/me`` and project endpoints gated by project-scoped roles:

- ``GET /api/v1/projects``          — auth (any member)
- ``GET /api/v1/projects/{id}``     — ``viewer`` or above
- ``DELETE /api/v1/projects/{id}``  — ``owner`` (§31.3: project deletion)

S8.1: auth hardening (§19 S8.1) — self-service + token hygiene:

- ``POST /api/v1/auth/register``        — account **and** workspace (user
  owns the new org); duplicate email → 409, weak password/email → 422
- ``POST /api/v1/auth/refresh``         — rotate the opaque refresh token
  (re-use after rotation → 401 + whole token family revoked)
- ``POST /api/v1/auth/change-password`` — re-auth with current password;
  revokes **all** refresh tokens (204)
- ``POST /api/v1/auth/login``           — now also returns the rotating
  refresh token + organizations; brute-force throttled in Redis per email
  and per IP (429 + ``Retry-After``; a successful login resets the counters)
- ``GET /api/v1/auth/me``               — now includes ``organizations``

Passwords and refresh tokens never appear in responses other than the
one-time issuance, logs or audit (§17).

S0.9: async jobs API (§11, §31.2) — the mandatory ``202 + job_id`` pattern:

- ``POST /api/v1/requirements/analyze`` → **202 + {job_id}** (``member`` or above)
- ``POST /api/v1/requirements/test-cases`` → **202 + {job_id}** (S1.2, ``member``+)
- ``GET /api/v1/requirements/{id}``  — persisted requirement + test cases (S1.3, ``viewer``+)
- ``GET /api/v1/projects/{id}/requirements`` — the project's requirements,
  newest first, with test-case counts (``viewer``+)
- ``GET /api/v1/jobs/{job_id}``         — job status/progress/result refs (``viewer``+)
- ``GET /api/v1/events``                — SSE stream of job progress events
                                          (``viewer``+ on the job's/project's project)
S2.4: automation generation + generated-test review (§19 S2.4):

- ``POST /api/v1/automation/generate`` → **202 + {job_id}`` (``member``+)
- ``GET /api/v1/projects/{id}/generated-tests`` — review queue (``viewer``+)
- ``GET /api/v1/generated-tests/{id}``          — review row detail (``viewer``+)
- ``POST /api/v1/generated-tests/{id}/approve`` (``member``+, audit)
- ``POST /api/v1/generated-tests/{id}/reject``  (``member``+, audit)
- ``POST /api/v1/generated-tests/{id}/apply``   (``member``+; writes the file, audit)

S3.2: run history, results, artifacts (§10, §15):

- ``GET /api/v1/projects/{id}/runs`` — the project's runs, newest first (``viewer``+)
- ``GET /api/v1/runs/{id}``          — run + results + artifacts (``viewer``+)
- ``GET /api/v1/runs/{id}/results``  — the run's test outcomes (``viewer``+)
- ``GET /api/v1/runs/{id}/artifacts`` — the run's artifact rows (``viewer``+)
- ``GET /api/v1/runs/{id}/artifacts/{artifact_id}/content`` — file bytes (``viewer``+)

S7.1: external integrations config (§19 S7.1, §17):

- ``GET    /api/v1/projects/{id}/integrations``           — configs (``member``+)
- ``PUT    /api/v1/projects/{id}/integrations/{provider}`` — upsert (``owner``+)
- ``DELETE /api/v1/projects/{id}/integrations/{provider}`` — remove (``owner``+)

S7.2: PR → regression (§19 S7.2):

- ``POST /api/v1/projects/{id}/regression/analyze`` now also accepts
  ``pull_request: {owner, repo, number}`` (exactly one of
  ``files`` / ``base_ref``+``head_ref`` / ``pull_request``; 409 when the
  S7.1 GitHub integration is missing for a PR source)
- ``POST /api/v1/projects/{id}/regression/pr-comment`` — idempotent PR
  comment (``owner``+; 202 + job; ``regression.comment`` SSE event)

S7.3: CI/CD webhook (§19 S7.3):

- ``POST /api/v1/webhooks/github`` — the HMAC ``X-Hub-Signature-256``
  **is the auth** (invalid/missing → 401; no token, no RBAC on this
  endpoint). ``pull_request`` ``opened``/``synchronize`` resolves
  ``repository.full_name`` → project → ``regression_analysis`` job
  (**202 + Location**, ``regression.set`` SSE). Other events are recorded
  + acknowledged **200** (``ignored``). ``X-GitHub-Delivery`` is unique in
  ``webhook_events`` — a re-sent delivery answers **200** (``duplicate``)
  and never spawns a second job. 401 when no webhook secret is configured;
  409 when no project matches the repository or the project has no
  ``repository_path``.

Token values never appear in these payloads (§17): the PUT body takes
``token_ref`` (the secret's name) and reads return ``token_configured``.

S8.2: teams — organizations, membership, invites (§19 S8.2):

- ``GET    /api/v1/organizations``                   — the caller's orgs
- ``GET    /api/v1/organizations/{id}/members``      — roster (any member)
- ``POST   /api/v1/organizations/{id}/members``      — add an existing account
- ``PUT    /api/v1/organizations/{id}/members/{uid}`` — change a member's role
- ``DELETE /api/v1/organizations/{id}/members/{uid}`` — self-leave, or removed
- ``POST   /api/v1/organizations/{id}/invites``      — one-time code (owner)
- ``POST   /api/v1/invites/{code}/accept``           — the invitee (email match)
- ``DELETE /api/v1/organizations/{id}``              — owner; hard-deletes the
  org and its projects (all project-scoped rows cascade) and revokes the
  refresh tokens of every former member (S8.3 deletion workflow, §17)
- ``DELETE /api/v1/auth/account``                    — self-delete: purges the
  user row (PII) + memberships + refresh tokens; ``ai_sessions`` rows survive
  with ``user_id`` nulled (history/audit kept, §17)

S8.3: RBAC hardening + append-only audit trail (§19 S8.3, §17):

- Org role gates are now the ``auth.require_org_role`` dependency (same
  pattern as ``require_role`` for projects): any member may read the
  roster; membership changes, invites, deletion and audit export are
  ``owner``-only. Every denied gate (403) is recorded as
  ``org.gate.denied``; RBAC runs before body validation, so 403/401 always
  precede a 422.
- ``DELETE /api/v1/organizations/{id}`` now takes a body —
  ``{"current_password"}`` — and re-authenticates the owner (wrong
  password → 401 + ``org.delete.reauth_failure``).
- ``GET /api/v1/organizations/{id}/audit`` (owner) exports the org's
  newest audit rows (newest first, capped at 200) — append-only: no API
  path can update or delete them.
- Every auth flow (login/register/refresh/change-password/account delete)
  and org flow (membership add/update/remove/leave, invite
  create/accept/deny, org deletion, project deletion) records a
  success/failure/blocked row in ``audit_log`` with the client IP; rows
  outlive their actors (``actor_id`` ON DELETE SET NULL).
"""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from qa_copilot_domain.enums import (
    AuditAction,
    AuditOutcome,
    GeneratedTestStatus,
    JobType,
    OrgRole,
    ProjectRole,
    TestResultStatus,
    role_at_least,
)
from qa_copilot_execution import ArtifactStore, ArtifactStoreError
from qa_copilot_integrations import webhook as webhook_core
from qa_copilot_knowledge import SearchHit
from qa_copilot_repository import db as repo_db
from qa_copilot_repository import generated_tests as repo_generated_tests
from qa_copilot_repository import integrations as repo_integrations
from qa_copilot_repository import invites as repo_invites
from qa_copilot_repository import membership, models
from qa_copilot_repository import requirements as repo_requirements
from qa_copilot_repository import runs as repo_runs
from qa_copilot_repository import security_audit as repo_security_audit
from qa_copilot_repository import webhooks as repo_webhooks
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from . import auth, jobs, knowledge_store, schemas
from .db import get_db
from .throttle import LoginThrottler

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
projects_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])
requirements_router = APIRouter(prefix="/api/v1/requirements", tags=["requirements"])
jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
events_router = APIRouter(prefix="/api/v1/events", tags=["events"])
automation_router = APIRouter(prefix="/api/v1/automation", tags=["automation"])
generated_tests_router = APIRouter(prefix="/api/v1/generated-tests", tags=["generated-tests"])
runs_router = APIRouter(prefix="/api/v1/runs", tags=["runs"])
integrations_router = APIRouter(prefix="/api/v1/projects", tags=["integrations"])
webhooks_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])
organizations_router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])
invites_router = APIRouter(prefix="/api/v1/invites", tags=["invites"])

#: S7.3: the only webhook deliveries that spawn a job (§19: "``pull_request``
#: opened/synchronize → ``regression_analysis`` job"); every other event is
#: recorded + acknowledged 200 (``ignored``).
_WEBHOOK_TRIGGER_ACTIONS = frozenset({"opened", "synchronize"})


def _user_out(user: models.User) -> schemas.UserOut:
    return schemas.UserOut(id=user.id, email=user.email, role=user.role)


def _explicit_project_rows(db: Session, user: models.User) -> list[tuple[models.Project, str]]:
    """The caller's explicit ``project_members`` rows (role from the row)."""
    rows = db.execute(
        select(models.Project, models.ProjectMember.role)
        .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == user.id)
        .order_by(models.Project.name)
    ).all()
    return [(project, role.value) for project, role in rows]


def _org_baseline_projects(db: Session, user: models.User) -> list[tuple[models.Project, str]]:
    """S8.2 baseline: the caller's org projects with **no** explicit row.

    Role = the caller's org role for the project's org mapped to the project
    role (org ``owner`` → ``owner``, org ``member`` → ``member``); an
    explicit ``project_members`` row always wins (never listed here).
    """
    org_rows = db.execute(
        select(
            models.OrganizationMember.organization_id,
            models.OrganizationMember.role,
        ).where(models.OrganizationMember.user_id == user.id)
    ).all()
    if not org_rows:
        return []
    org_role_by_id = {organization_id: OrgRole(role) for organization_id, role in org_rows}
    explicit_ids = set(
        db.scalars(
            select(models.ProjectMember.project_id).where(models.ProjectMember.user_id == user.id)
        )
    )
    projects = db.scalars(
        select(models.Project)
        .where(models.Project.organization_id.in_(org_role_by_id))
        .order_by(models.Project.name)
    ).all()
    out: list[tuple[models.Project, str]] = []
    for project in projects:
        if project.id in explicit_ids or project.organization_id is None:
            continue
        org_role = org_role_by_id[project.organization_id]
        out.append((project, membership.ORG_ROLE_TO_PROJECT_ROLE[org_role].value))
    return out


def _member_projects(db: Session, user: models.User) -> list[schemas.ProjectRef]:
    """Projects the caller can access, with the caller's *effective* role.

    S8.2 (bible §19): an explicit ``project_members`` row always wins; a
    project without one falls back to the caller's **org** role for that
    project's organization (org ``owner`` → ``owner``, org ``member`` →
    ``member``).
    """
    by_id: dict[str, tuple[str, str]] = {}
    for project, role in _explicit_project_rows(db, user):
        by_id[project.id] = (project.name, role)
    for project, role in _org_baseline_projects(db, user):
        by_id.setdefault(project.id, (project.name, role))
    return [
        schemas.ProjectRef(id=project_id, name=name, role=role)
        for project_id, (name, role) in sorted(by_id.items(), key=lambda kv: kv[1][0])
    ]


def _org_memberships(db: Session, user: models.User) -> list[schemas.OrganizationRef]:
    """S8.1: the caller's organizations (role from ``organization_members``)."""
    rows = db.execute(
        select(models.Organization, models.OrganizationMember.role)
        .join(
            models.OrganizationMember,
            models.OrganizationMember.organization_id == models.Organization.id,
        )
        .where(models.OrganizationMember.user_id == user.id)
        .order_by(models.Organization.name)
    ).all()
    return [schemas.OrganizationRef(id=org.id, name=org.name, role=role) for org, role in rows]


def _token_response(
    db: Session, user: models.User, secret: str, refresh_token: str
) -> schemas.TokenResponse:
    """The shared login/refresh body (access + rotating refresh token, S8.1)."""
    return schemas.TokenResponse(
        token=auth.create_access_token(user.id, user.email, secret),
        expires_in=int(auth.TOKEN_TTL.total_seconds()),
        refresh_token=refresh_token,
        refresh_expires_in=int(auth.REFRESH_TTL.total_seconds()),
        user=_user_out(user),
        organizations=_org_memberships(db, user),
        projects=_member_projects(db, user),
    )


def _client_ip(request: Request) -> str:
    """Best-effort client IP (S8.5 hardens proxy trust; local-first stays simple)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _audit(
    db: Session,
    action: AuditAction,
    *,
    actor_id: str | None,
    target: str | None,
    outcome: AuditOutcome,
    ip: str | None,
    commit: bool = True,
) -> None:
    """S8.3: one append-only ``audit_log`` row (§17) — never a credential in *target*.

    *commit=False* folds the row into the caller's pending business commit
    (atomic with the write it describes); failure paths commit standalone
    before the error response.
    """
    repo_security_audit.record(
        db, actor_id=actor_id, action=action, target=target, outcome=outcome, ip=ip
    )
    if commit:
        db.commit()


# --- auth (S0.8 baseline + S8.1 hardening, §19) -------------------------------


@auth_router.post("/register", status_code=201, response_model=schemas.RegisterResponse)
def register(
    body: schemas.RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.RegisterResponse:
    """Self-service signup (S8.1): account **and** workspace.

    Creates the user and a new organization the user owns — one signup, one
    private workspace (build bible §19 S8.1). Duplicate email → 409; the
    password must satisfy the S8.1 policy (422). The password is only ever
    hashed (§17). S8.3: the outcome is audited (``auth.register.*``) with
    the target email — never the password.
    """
    ip = _client_ip(request)
    email = body.email
    if db.scalar(select(models.User).where(models.User.email == email)) is not None:
        _audit(
            db,
            AuditAction.REGISTER_FAILURE,
            actor_id=None,
            target=email,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="email already registered")
    violations = auth.password_policy_violations(body.password)
    if violations:
        _audit(
            db,
            AuditAction.REGISTER_FAILURE,
            actor_id=None,
            target=email,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=422, detail="password must " + " and ".join(violations))
    user = models.User(email=email, role="owner", password_hash=auth.hash_password(body.password))
    org = models.Organization(name=body.organization_name or f"{email.split('@')[0]}'s workspace")
    db.add_all([org, user])
    db.flush()
    db.add(models.OrganizationMember(organization_id=org.id, user_id=user.id, role=OrgRole.OWNER))
    # S8.3: the audit row commits atomically with the account + workspace —
    # the commit happens below, so a lost unique-email race 409s *and*
    # leaves no orphaned success row behind.
    _audit(
        db,
        AuditAction.REGISTER_SUCCESS,
        actor_id=user.id,
        target=email,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    try:
        db.commit()
    except IntegrityError as exc:  # a concurrent signup won the unique-email race
        db.rollback()
        _audit(
            db,
            AuditAction.REGISTER_FAILURE,
            actor_id=None,
            target=email,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="email already registered") from exc
    return schemas.RegisterResponse(
        user=_user_out(user),
        organization=schemas.OrganizationRef(id=org.id, name=org.name, role=OrgRole.OWNER),
        projects=[],
    )


@auth_router.post("/login", response_model=schemas.TokenResponse)
def login(
    body: schemas.LoginRequest,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.TokenResponse:
    """Email + password → access token + rotating refresh token (S8.1).

    Brute-force throttled in Redis per email *and* per IP: once the failure
    counter hits the limit the endpoint answers ``429`` + ``Retry-After``;
    a successful login resets the counters (S8.1, §19).

    S8.3: every outcome is audited (``auth.login.success`` / ``failure`` /
    ``blocked``) with the target email and client IP — the password never
    reaches the trail (§17).
    """
    settings = request.app.state.settings
    try:
        secret = auth._require_secret(settings)
    except RuntimeError as exc:
        # fail loud with a readable body instead of a bare 500
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    throttler: LoginThrottler = request.app.state.login_throttler
    ip = _client_ip(request)
    decision = throttler.check(body.email, ip)
    if decision.blocked:
        _audit(
            db,
            AuditAction.LOGIN_BLOCKED,
            actor_id=None,
            target=body.email,
            outcome=AuditOutcome.DENIED,
            ip=ip,
        )
        raise HTTPException(
            status_code=429,
            detail="too many failed login attempts; try again later",
            headers={"Retry-After": str(decision.retry_after_s)},
        )

    user = db.scalar(select(models.User).where(models.User.email == body.email))
    if user is None or not auth.check_password(body.password, user.password_hash):
        throttler.record_failure(body.email, ip)
        _audit(
            db,
            AuditAction.LOGIN_FAILURE,
            actor_id=None,
            target=body.email,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=401, detail="invalid credentials")

    throttler.reset(body.email, ip)
    # S8.3: the success row commits with the refresh-token issuance below.
    _audit(
        db,
        AuditAction.LOGIN_SUCCESS,
        actor_id=user.id,
        target=body.email,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    refresh_token, _ = auth.issue_refresh_token(db, user.id)
    return _token_response(db, user, secret, refresh_token)


@auth_router.post("/refresh", response_model=schemas.TokenResponse)
def refresh(
    body: schemas.RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.TokenResponse:
    """Rotate a refresh token (S8.1): the presented token dies, a successor lives.

    Reuse of an already-rotated token is rejected (401) and revokes the
    whole token family, so a stolen token kills every live successor
    (stolen-token rule, §17). S8.3: both outcomes are audited
    (``auth.refresh.success`` / ``failure``) — the token itself is never
    written to the trail (§17).
    """
    settings = request.app.state.settings
    try:
        secret = auth._require_secret(settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    ip = _client_ip(request)
    try:
        user, refresh_token = auth.rotate_refresh_token(db, body.refresh_token)
    except auth.AuthError as exc:
        _audit(
            db,
            AuditAction.REFRESH_FAILURE,
            actor_id=None,
            target=None,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    # rotation already committed; the success row commits on its own.
    _audit(
        db,
        AuditAction.REFRESH_SUCCESS,
        actor_id=user.id,
        target=None,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
    )
    return _token_response(db, user, secret, refresh_token)


@auth_router.post("/change-password", status_code=204)
def change_password(
    body: schemas.ChangePasswordRequest,
    request: Request,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """Change the caller's password (S8.1).

    Re-authenticates the current password, then hashes the new one (policy:
    10+ chars, letter + digit) and revokes **all** refresh tokens — any
    other session loses its token. Neither password is ever logged, audited
    or stored in cleartext (§17). S8.3: the outcome is audited as
    ``auth.change_password.success`` / ``failure`` (actor + user id only).
    """
    ip = _client_ip(request)
    if not auth.check_password(body.current_password, user.password_hash):
        _audit(
            db,
            AuditAction.CHANGE_PASSWORD_FAILURE,
            actor_id=user.id,
            target=user.id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=401, detail="current password is incorrect")
    violations = auth.password_policy_violations(body.new_password)
    if violations:
        _audit(
            db,
            AuditAction.CHANGE_PASSWORD_FAILURE,
            actor_id=user.id,
            target=user.id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=422, detail="new password must " + " and ".join(violations))
    user.password_hash = auth.hash_password(body.new_password)
    # S8.3: the success row commits with the hash update + token revocation
    # (``revoke_user_refresh_tokens`` issues the commit below).
    _audit(
        db,
        AuditAction.CHANGE_PASSWORD_SUCCESS,
        actor_id=user.id,
        target=user.id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    auth.revoke_user_refresh_tokens(db, user.id)
    db.commit()


@auth_router.delete("/account", status_code=204)
def delete_account(
    request: Request,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """Self-delete the caller's account (S8.2/S8.3, §17 deletion workflows).

    Purges the PII — the ``users`` row (email + password hash) — and, with
    it, the cascading ``project_members`` / ``organization_members`` /
    ``user_refresh_tokens`` rows (no token can resolve to a missing user, so
    every live session dies). ``ai_sessions.user_id`` has **no** ON DELETE
    rule, so the session rows are kept project-scoped with ``user_id``
    nulled — the AI history/audit outlives the person (§17). S8.3: the
    deletion is audited (``auth.account.delete``); the audit row itself
    survives the cascade with its ``actor_id`` nulled.
    """
    ip = _client_ip(request)
    # S8.3: written before the user row is deleted — the commit below keeps
    # the audit record (``actor_id`` ON DELETE SET NULL, §17).
    _audit(
        db,
        AuditAction.ACCOUNT_DELETE,
        actor_id=user.id,
        target=user.id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    db.execute(
        update(models.AISession).where(models.AISession.user_id == user.id).values(user_id=None)
    )
    db.execute(delete(models.User).where(models.User.id == user.id))
    db.commit()


@auth_router.get("/me", response_model=schemas.MeResponse)
def me(
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.MeResponse:
    """The authenticated user + their organizations and project roles (S8.1)."""
    return schemas.MeResponse(
        user=_user_out(user),
        organizations=_org_memberships(db, user),
        projects=_member_projects(db, user),
    )


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
    request: Request,
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """Delete a project — ``owner`` only (§31.3). Memberships cascade per schema FKs.

    S8.3: the deletion is audited (``project.delete``) with the project id
    as target — the row survives the project's own deletion (§17).
    """
    user, project_id = ctx
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    # S8.3: audit row commits with the deletion (the project id outlives it).
    _audit(
        db,
        AuditAction.PROJECT_DELETE,
        actor_id=user.id,
        target=project_id,
        outcome=AuditOutcome.SUCCESS,
        ip=_client_ip(request),
        commit=False,
    )
    # ORM: remove membership rows first — the ORM would otherwise try to
    # null out the composite PK instead of relying on ON DELETE CASCADE.
    db.execute(delete(models.ProjectMember).where(models.ProjectMember.project_id == project_id))
    db.delete(project)
    db.commit()


# --- jobs (S0.9, async 202 + SSE) ---------------------------------------------


def _require_project_role(
    db: Session, user: models.User, project_id: str, minimum: ProjectRole
) -> None:
    """RBAC for job endpoints: the project comes from the *job row*, not the URL.

    ``auth.require_role`` reads ``{project_id}`` from the path, so job-scoped
    routes check inline. Non-members and below-minimum roles get 403 (never
    404) — the build bible's no-existence-leak rule (§31.3).
    """
    role = membership.get_project_role(db, project_id, user.id)
    if role is None:
        raise HTTPException(status_code=403, detail="no role for this project")
    if not role_at_least(ProjectRole(role), minimum):
        raise HTTPException(status_code=403, detail=f"requires {minimum.value} role (has {role})")


@requirements_router.post("/analyze", status_code=202, response_model=schemas.JobCreated)
def analyze_requirement(
    body: schemas.AnalyzeRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """Analyze a requirement: **202 + job_id**, AI work runs async (build bible §11).

    No AI work runs in this request (§31.2) — track the job via
    ``GET /api/v1/jobs/{job_id}`` or the SSE feed
    ``GET /api/v1/events?job_id=...``. Requires ``member`` or above on the
    target project; unknown projects 403 (no existence leak).
    """
    project = db.get(models.Project, body.project_id)
    if project is None:
        # 403, not 404: a non-member must not be able to tell whether the
        # project exists (§31.3). Membership rows cannot exist for a missing
        # project, so this branch is effectively "403 for everyone" — on
        # purpose.
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    job_input = {
        "title": body.title,
        "content": body.content,
        "acceptance_criteria": body.acceptance_criteria,
    }
    job = models.Job(
        project_id=project.id,
        type=JobType.REQUIREMENT_ANALYSIS,
        # S0.9 keeps the inline input as the ref (VARCHAR(1024) — dev scale);
        # S1.x creates the requirement row and points ``input_ref`` at it.
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@requirements_router.post("/test-cases", status_code=202, response_model=schemas.JobCreated)
def design_test_cases(
    body: schemas.TestDesignRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """Design test cases for a requirement: **202 + job_id** (S1.2, §11).

    No AI work runs in this request (§31.2) — the Test Design Agent runs as a
    ``test_case_generation`` job; track it via ``GET /api/v1/jobs/{job_id}``
    or the SSE feed ``GET /api/v1/events?job_id=...``. ``member`` or above on
    the target project; unknown projects 403 (no existence leak, §31.3).
    """
    project = db.get(models.Project, body.project_id)
    if project is None:
        # 403, not 404: a non-member must not be able to tell whether the
        # project exists (§31.3).
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    job_input = {
        "title": body.title,
        "content": body.content,
        "acceptance_criteria": body.acceptance_criteria,
    }
    job = models.Job(
        project_id=project.id,
        type=JobType.TEST_CASE_GENERATION,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_test_design_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@requirements_router.get("/{requirement_id}", response_model=schemas.RequirementOut)
def get_requirement(
    requirement_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.RequirementOut:
    """Read a persisted requirement + its designed test cases (S1.3, §10/§12).

    The ``test_case_generation`` job stores the requirement id in its
    ``output_ref`` — this is the endpoint the shell calls to render the
    suite. ``viewer`` or above on the requirement's project (§31.3);
    unknown ids 404.
    """
    try:
        uuid.UUID(requirement_id)
    except ValueError:
        # Not a UUID → can't be a row: 404 without a DB round-trip.
        raise HTTPException(status_code=404, detail="requirement not found") from None
    requirement = db.get(models.Requirement, requirement_id)
    if requirement is None:
        raise HTTPException(status_code=404, detail="requirement not found")
    _require_project_role(db, user, requirement.project_id, ProjectRole.VIEWER)
    return schemas.RequirementOut(
        id=requirement.id,
        project_id=requirement.project_id,
        title=requirement.title,
        content=requirement.content,
        acceptance_criteria=list(requirement.acceptance_criteria),
        risk=requirement.risk.value,
        created_at=requirement.created_at,
        test_cases=[
            schemas.TestCaseOut(
                id=case.id,
                title=case.title,
                type=case.type.value,
                priority=case.priority.value,
                preconditions=list(case.preconditions),
                steps=list(case.steps),
                expected_results=list(case.expected_results),
                risk=case.risk.value,
                created_at=case.created_at,
            )
            for case in requirement.test_cases
        ],
    )


@projects_router.get(
    "/{project_id}/requirements", response_model=list[schemas.RequirementSummaryOut]
)
def list_requirements(
    project_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.RequirementSummaryOut]:
    """The project's requirements (history), newest first — ``viewer`` or above.

    Summary rows for the web shell's "past requirements" list (S1.3); the full
    suite of one row still comes from ``GET /requirements/{id}``. Same
    RBAC shape as the other project list endpoints (S2.4/S3.2): unknown
    projects 404, non-members 403 (no existence leak, §31.3).
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    rows = repo_requirements.list_requirements(db, project.id)
    return [
        schemas.RequirementSummaryOut(
            id=requirement.id,
            title=requirement.title,
            risk=requirement.risk.value,
            created_at=requirement.created_at,
            test_case_count=case_count,
        )
        for requirement, case_count in rows
    ]


@jobs_router.get("/{job_id}", response_model=schemas.JobOut)
def get_job(
    job_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobOut:
    """Job status, progress and result/error refs — ``viewer`` or above (§11).

    Non-members get 403 (never the job's data) — the no-existence-leak rule
    (§31.3). Job ids are UUIDs, so 404-vs-403 enumeration is not a realistic
    threat; unknown ids still 404.
    """
    job = db.get(models.Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.project_id is None:
        raise HTTPException(status_code=403, detail="no role for this job")
    _require_project_role(db, user, job.project_id, ProjectRole.VIEWER)
    return schemas.JobOut(
        id=job.id,
        project_id=job.project_id,
        type=job.type.value,
        status=job.status.value,
        progress=job.progress,
        input_ref=job.input_ref,
        output_ref=job.output_ref,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


@events_router.get("")
def stream_events(
    request: Request,
    job_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
) -> StreamingResponse:
    """SSE feed of job progress events (build bible §11; the S0.7 web contract).

    - ``?job_id=...`` — one job's events; finite (closes on the terminal event)
    - ``?project_id=...`` — every job's events in the project (open-ended feed)

    ``viewer`` or above on the job's project (or the given project). RBAC +
    the job snapshot run in a short-lived session that is **closed before**
    streaming starts, so a long-lived SSE connection never holds the pooled
    connection used for those queries (the bus/runner are in-process —
    see ``jobs.py``; a Redis pub/sub backend can replace the bus later).
    """
    if job_id is None and project_id is None:
        raise HTTPException(status_code=422, detail="provide job_id or project_id")

    engine = request.app.state.engine
    snapshot: jobs.JobSnapshot | None = None
    with repo_db.session_scope(engine) as session:
        if job_id is not None:
            job = session.get(models.Job, job_id)
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            if job.project_id is None:
                raise HTTPException(status_code=403, detail="no role for this job")
            _require_project_role(session, user, job.project_id, ProjectRole.VIEWER)
            snapshot = jobs.JobSnapshot(
                status=job.status,
                project_id=job.project_id,
                error=job.error,
                output_ref=job.output_ref,
            )
        else:
            # The 422 guard above guarantees exactly one of the two is set.
            assert project_id is not None
            _require_project_role(session, user, project_id, ProjectRole.VIEWER)

    bus: jobs.EventBus = request.app.state.jobs_bus
    return StreamingResponse(
        jobs.sse_stream(bus, job_id=job_id, project_id=project_id, snapshot=snapshot),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
        },
    )


# --- S2.4: automation generation + generated-test review (§19 S2.4) -----------


def _generated_test_out(row: models.GeneratedTest) -> schemas.GeneratedTestOut:
    """ORM row → API schema (enum values as wire strings)."""
    return schemas.GeneratedTestOut(
        id=row.id,
        project_id=row.project_id,
        job_id=row.job_id,
        test_case_id=row.test_case_id,
        file_path=row.file_path,
        file_path_pattern=row.file_path_pattern,
        language=row.language,
        framework=row.framework,
        content=row.content,
        notes=list(row.notes),
        repository_path=row.repository_path,
        status=row.status.value,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        review_note=row.review_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_generated_test_for_review(
    db: Session, user: models.User, generated_test_id: str
) -> models.GeneratedTest:
    """Fetch one review row + enforce the reviewer floor (``member`` or above)."""
    try:
        uuid.UUID(generated_test_id)
    except ValueError:
        # Not a UUID → can't be a row: 404 without a DB round-trip.
        raise HTTPException(status_code=404, detail="generated test not found") from None
    row = db.get(models.GeneratedTest, generated_test_id)
    if row is None:
        raise HTTPException(status_code=404, detail="generated test not found")
    _require_project_role(db, user, row.project_id, ProjectRole.MEMBER)
    return row


def _record_review_audit(
    db: Session,
    project_id: str,
    user: models.User,
    *,
    action: str,
    generated_test_id: str,
) -> None:
    """Audit a human review action (§31.1): ``ai_sessions`` anchor + ``ai_actions``.

    The reviewer's free-text note lives on the row (``review_note``); the
    audit trail records the action, the actor, and the reviewed artifact.
    """
    session = models.AISession(
        project_id=project_id,
        user_id=user.id,
        task_type="generated_test_review",
        status="completed",
    )
    db.add(session)
    db.flush()
    db.add(
        models.AIAction(
            session_id=session.id,
            agent="human-review",
            model="human",
            approval_status=action,
            output_ref=generated_test_id,
        )
    )
    db.flush()


def _write_applied_file(row: models.GeneratedTest) -> Path:
    """Write the generated test file under the row's repository root (apply).

    Guards: the root must exist, the repo-relative ``file_path`` must stay
    under it, and the target must not already exist — V1 policy is no silent
    overwrite (409; re-generating a test creates a new row).
    """
    if not row.repository_path:
        raise FileNotFoundError("generated test has no repository_path to apply to")
    root = Path(row.repository_path)
    if not root.is_dir():
        raise FileNotFoundError(f"repository path not found: {row.repository_path}")
    root_resolved = root.resolve()
    target = (root_resolved / row.file_path).resolve()
    if root_resolved not in target.parents:
        raise FileNotFoundError(f"file_path {row.file_path!r} escapes the repository root")
    if target.exists():
        raise FileExistsError(f"target file already exists: {row.file_path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(row.content, encoding="utf-8")
    return target


def _transition(
    db: Session,
    user: models.User,
    generated_test_id: str,
    target: GeneratedTestStatus,
    note: str | None,
) -> schemas.GeneratedTestOut:
    """One review transition (approve / reject): state machine + audit.

    The domain is the single source of truth for the review vocabulary
    (``qa_copilot_domain.enums``): an illegal or no-op transition raises
    ``ValueError`` in the repository → ``409 Conflict`` here.
    """
    row = _get_generated_test_for_review(db, user, generated_test_id)
    try:
        repo_generated_tests.set_review_status(db, row, target=target, user_id=user.id, note=note)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _record_review_audit(db, row.project_id, user, action=target.value, generated_test_id=row.id)
    db.commit()
    return _generated_test_out(row)


@automation_router.post("/generate", status_code=202, response_model=schemas.JobCreated)
def generate_automation_test(
    body: schemas.AutomationRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """Automate an approved test case: **202 + job_id** (S2.4, §11).

    The S2.3 Automation Agent runs as an ``automation_generation`` job; its
    output lands as a **pending** ``generated_tests`` row — the job's
    ``output_ref`` is that row id — and is reviewed via the
    ``/generated-tests`` endpoints (approve / apply / reject). Track via
    ``GET /api/v1/jobs/{job_id}`` or ``GET /api/v1/events?job_id=...``.
    ``member`` or above on the target project; unknown projects 403 (no
    existence leak, §31.3).
    """
    project = db.get(models.Project, body.project_id)
    if project is None:
        # 403, not 404: a non-member must not be able to tell whether the
        # project exists (§31.3).
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    try:
        uuid.UUID(body.test_case_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="test case not found") from None
    test_case = db.get(models.TestCase, body.test_case_id)
    if test_case is None:
        raise HTTPException(status_code=404, detail="test case not found")
    # The case must belong to this project (via the §10 M:N join) — a case
    # from another project is not "found" here.
    linked = db.scalar(
        select(models.RequirementTestCase.requirement_id)
        .join(
            models.Requirement,
            models.RequirementTestCase.requirement_id == models.Requirement.id,
        )
        .where(
            models.Requirement.project_id == project.id,
            models.RequirementTestCase.test_case_id == body.test_case_id,
        )
        .limit(1)
    )
    if linked is None:
        raise HTTPException(status_code=404, detail="test case not found")

    job_input = {
        "test_case_id": body.test_case_id,
        "repository_path": body.repository_path,
    }
    job = models.Job(
        project_id=project.id,
        type=JobType.AUTOMATION_GENERATION,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_automation_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@projects_router.get("/{project_id}/generated-tests", response_model=list[schemas.GeneratedTestOut])
def list_generated_tests(
    project_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.GeneratedTestOut]:
    """The project's generated-test review queue (S2.4), newest first.

    ``viewer`` or above; unknown projects 404 (no existence leak, §31.3).
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    rows = repo_generated_tests.list_generated_tests(db, project.id)
    return [_generated_test_out(row) for row in rows]


@generated_tests_router.get("/{generated_test_id}", response_model=schemas.GeneratedTestOut)
def get_generated_test(
    generated_test_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.GeneratedTestOut:
    """One generated test (S2.4 review row) — ``viewer`` or above (§31.3).

    The ``automation_generation`` job stores the row id in its
    ``output_ref`` (§11) — this is the endpoint the shell renders the
    review/diff view from.
    """
    try:
        uuid.UUID(generated_test_id)
    except ValueError:
        # Not a UUID → can't be a row: 404 without a DB round-trip.
        raise HTTPException(status_code=404, detail="generated test not found") from None
    row = db.get(models.GeneratedTest, generated_test_id)
    if row is None:
        raise HTTPException(status_code=404, detail="generated test not found")
    _require_project_role(db, user, row.project_id, ProjectRole.VIEWER)
    return _generated_test_out(row)


@generated_tests_router.post(
    "/{generated_test_id}/approve", response_model=schemas.GeneratedTestOut
)
def approve_generated_test(
    generated_test_id: str,
    body: schemas.GeneratedTestReviewIn | None = None,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.GeneratedTestOut:
    """Approve a generated test (``pending → approved``; §19 S2.4).

    ``member`` or above. The reviewer trail (actor, time, note) is written
    to the row and audited (§31.1). Illegal/no-op transitions → 409.
    """
    note = body.note if body is not None else None
    return _transition(db, user, generated_test_id, GeneratedTestStatus.APPROVED, note)


@generated_tests_router.post("/{generated_test_id}/reject", response_model=schemas.GeneratedTestOut)
def reject_generated_test(
    generated_test_id: str,
    body: schemas.GeneratedTestReviewIn | None = None,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.GeneratedTestOut:
    """Reject a generated test (terminal; ``member``+; §19 S2.4).

    Re-generating a test creates a new row (V1: no re-opening, §10).
    Illegal/no-op transitions → 409.
    """
    note = body.note if body is not None else None
    return _transition(db, user, generated_test_id, GeneratedTestStatus.REJECTED, note)


@generated_tests_router.post("/{generated_test_id}/apply", response_model=schemas.GeneratedTestOut)
def apply_generated_test(
    generated_test_id: str,
    body: schemas.GeneratedTestReviewIn | None = None,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.GeneratedTestOut:
    """Apply a generated test: write the file into the target repository.

    ``pending|approved → applied`` (terminal). The file is written **only if**
    the transition is legal and the target path is free — an existing file is
    a 409 (V1 policy: no silent overwrite). ``member`` or above; audited
    (§31.1).
    """
    row = _get_generated_test_for_review(db, user, generated_test_id)
    target = GeneratedTestStatus.APPLIED
    note = body.note if body is not None else None
    try:
        # Validate the transition *before* any file side effect (409 semantics).
        repo_generated_tests.set_review_status(db, row, target=target, user_id=user.id, note=note)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    try:
        _write_applied_file(row)
    except FileExistsError as exc:
        # V1 policy: no silent overwrite (409; re-generating creates a new row).
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        # A write failure must not leave the row "applied" without a file.
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"failed to write {row.file_path}: {exc}"
        ) from exc
    _record_review_audit(db, row.project_id, user, action="applied", generated_test_id=row.id)
    db.commit()
    return _generated_test_out(row)


# --- S3.2: run history, results, artifacts (§10, §15) -------------------------


def _failure_out(failure: models.Failure) -> schemas.FailureOut:
    """ORM failure row → API schema (enum values as wire strings)."""
    return schemas.FailureOut(
        id=failure.id,
        category=failure.category.value,
        root_cause=failure.root_cause,
        confidence=failure.confidence,
        evidence=list(failure.evidence),
        suggested_fix=failure.suggested_fix,
        needs_human_approval=failure.needs_human_approval,
        jira_issue_key=failure.jira_issue_key,
    )


def _artifact_out(run_id: str, artifact: models.Artifact) -> schemas.ArtifactOut:
    """One artifact row, with its ``/content`` download endpoint for the UI."""
    return schemas.ArtifactOut(
        id=artifact.id,
        test_result_id=artifact.test_result_id,
        type=artifact.type.value,
        uri=artifact.uri,
        metadata=dict(artifact.metadata_ or {}),
        created_at=artifact.created_at,
        download_url=f"/api/v1/runs/{run_id}/artifacts/{artifact.id}/content",
    )


def _result_out(run_id: str, result: models.TestResult) -> schemas.TestResultOut:
    """One test outcome + its diagnosis + artifacts."""
    return schemas.TestResultOut(
        id=result.id,
        run_id=result.run_id,
        test_case_id=result.test_case_id,
        status=result.status.value,
        duration=result.duration,
        failure=_failure_out(result.failure) if result.failure is not None else None,
        artifacts=[_artifact_out(run_id, artifact) for artifact in result.artifacts],
    )


def _duration_s(run: models.TestRun) -> float | None:
    """Run wall-clock duration in seconds (``None`` when timestamps are absent)."""
    if run.started_at is None or run.completed_at is None:
        return None
    return max(0.0, (run.completed_at - run.started_at).total_seconds())


def _run_list_item(run: models.TestRun) -> schemas.RunListItem:
    """ORM run row → list schema (S3.2 run-history list row)."""
    return schemas.RunListItem(
        id=run.id,
        project_id=run.project_id,
        commit_sha=run.commit_sha,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _run_detail(run: models.TestRun, results: list[models.TestResult]) -> schemas.RunDetail:
    """Run + results + artifacts; totals and duration computed here (not stored)."""
    totals = {
        "total": len(results),
        "passed": sum(1 for r in results if r.status == TestResultStatus.PASSED),
        "failed": sum(1 for r in results if r.status == TestResultStatus.FAILED),
        "flaky": sum(1 for r in results if r.status == TestResultStatus.FLAKY),
        "skipped": sum(1 for r in results if r.status == TestResultStatus.SKIPPED),
        "pending": sum(1 for r in results if r.status == TestResultStatus.PENDING),
    }
    return schemas.RunDetail(
        id=run.id,
        project_id=run.project_id,
        commit_sha=run.commit_sha,
        status=run.status.value,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        duration_s=_duration_s(run),
        totals=totals,
        results=[_result_out(run.id, result) for result in results],
        artifacts=[
            _artifact_out(run.id, artifact) for result in results for artifact in result.artifacts
        ],
    )


def _get_run_for_read(db: Session, user: models.User, run_id: str) -> models.TestRun:
    """Fetch one run + enforce the ``viewer`` floor (S3.2 read path, §31.3).

    Non-members and below-``viewer`` roles get 403 (never 404) — the build
    bible's no-existence-leak rule. A non-UUID id is a 404 without a round-trip.
    """
    try:
        uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="run not found") from None
    run = db.get(models.TestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    _require_project_role(db, user, run.project_id, ProjectRole.VIEWER)
    return run


@projects_router.get("/{project_id}/runs", response_model=list[schemas.RunListItem])
def list_runs(
    project_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.RunListItem]:
    """A project's runs, newest first (S3.2 run history, ``viewer`` or above)."""
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    return [_run_list_item(run) for run in repo_runs.list_runs(db, project.id)]


# --- S5.3: project knowledge (build bible §7, §14, §19 Phase 5) ---------------


def _knowledge_document_out(
    row: models.KnowledgeDocument,
) -> schemas.KnowledgeDocumentOut:
    """A ``knowledge_documents`` row → the API document shape (S5.3).

    ``title`` is stored inside ``metadata`` (the table has no ``title``
    column); restore it with a fallback from ``source_ref``.
    """
    metadata = dict(row.metadata_ or {})
    title = metadata.get("title") or row.source_ref or "document"
    return schemas.KnowledgeDocumentOut(
        id=str(row.id),
        source_type=row.source_type or "",
        title=str(title),
        source_ref=row.source_ref or str(row.id),
        content=row.content,
        metadata=metadata,
        created_at=row.created_at,
    )


def _knowledge_hit_out(hit: SearchHit) -> schemas.KnowledgeHit:
    """A domain search hit (``SearchHit.chunk``) → the API hit shape (S5.3)."""
    chunk = hit.chunk
    return schemas.KnowledgeHit(
        score=hit.score,
        document_ref=chunk.document_ref,
        source_type=chunk.source_type.value,
        title=chunk.title,
        chunk_index=chunk.chunk_index,
        content=chunk.content,
        metadata=dict(chunk.metadata),
        matched_terms=list(hit.matched_terms),
    )


@projects_router.post(
    "/{project_id}/knowledge/index", status_code=202, response_model=schemas.JobCreated
)
def index_project_knowledge(
    project_id: str,
    body: schemas.KnowledgeIndexRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """Index the project's knowledge corpus: **202 + job_id** (S5.3, §11).

    No AI work runs in this request (§31.2) — the ``knowledge_index`` job
    assembles the corpus (repository files when provided + the project's
    persisted requirements/test-cases/run history) and persists it. Track it
    via ``GET /api/v1/jobs/{job_id}`` or the SSE feed. ``member`` or above;
    unknown projects 403 (no existence leak, §31.3).
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    job_input = {"repository_path": body.repository_path}
    job = models.Job(
        project_id=project.id,
        type=JobType.KNOWLEDGE_INDEX,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_knowledge_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@projects_router.post(
    "/{project_id}/knowledge/ask", status_code=202, response_model=schemas.JobCreated
)
def ask_project_knowledge(
    project_id: str,
    body: schemas.KnowledgeAskRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """Ask the project knowledge base: **202 + job_id** (S5.5, §11, §14).

    No AI work runs in this request (§31.2) — the ``knowledge_ask`` job
    retrieves the project's top-k chunks (S5.3) and grounds the answer (S5.4).
    The answer text and citations ride the ``knowledge.answer`` SSE event — the
    full payload, since ``jobs.output_ref`` only holds a stable
    ``knowledge-ask://`` reference. Track via ``GET /api/v1/jobs/{job_id}`` or
    the SSE feed. ``member`` or above; unknown projects 403 (no existence
    leak, §31.3).
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    job_input = {"question": body.question}
    job = models.Job(
        project_id=project.id,
        type=JobType.KNOWLEDGE_ASK,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_knowledge_ask_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@projects_router.post(
    "/{project_id}/regression/analyze",
    status_code=202,
    response_model=schemas.JobCreated,
)
def analyze_project_regression(
    project_id: str,
    body: schemas.RegressionAnalysisRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """Analyze a change for regression risk: **202 + job_id** (S6.4, §19, §11).

    No AI work runs in this request (§31.2) — the ``regression_analysis`` job
    computes the deterministic S6.1 change-impact set, joins it with the
    project's S6.2 test history and ranks it (S6.3), and adds the optional
    S6.5 advisor brief. The recommendation rides the ``regression.set`` SSE
    event (``jobs.output_ref`` only holds a stable ``regression://`` reference).
    ``member`` or above; unknown projects 403 (no existence leak, §31.3).
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    # S7.2: the ``pull_request`` source needs the S7.1 GitHub integration —
    # fail fast (409) instead of queueing a job that would fail at runtime.
    # The PAT is never part of the error (§17: "PAT never appears in logs or
    # audit").
    if body.pull_request is not None:
        try:
            jobs.github_integration_config(db, project.id)
        except jobs.GitHubIntegrationNotConfiguredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    job_input = body.model_dump()
    job = models.Job(
        project_id=project.id,
        type=JobType.REGRESSION_ANALYSIS,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_regression_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@projects_router.post(
    "/{project_id}/regression/pr-comment",
    status_code=202,
    response_model=schemas.JobCreated,
)
def post_regression_pr_comment(
    body: schemas.RegressionPrCommentRequest,
    request: Request,
    response: Response,
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """S7.2: post the ranked regression set to a GitHub PR — **202 + job_id**.

    ``owner`` or above (it *writes* to the PR, §19 S7.2). The
    ``regression_pr_comment`` job resolves ``pull_request`` through the
    project's S7.1 GitHub integration, computes the deterministic S6.1/S6.2/
    S6.3 set from ``repository_path``, and upserts the idempotent marker
    comment (first post creates, re-posts update, identical re-posts are a
    no-op). The ``regression.comment`` SSE event carries
    ``action`` / ``comment_id`` / ``html_url``. 409 when the S7.1 GitHub
    integration (or its secret) is missing — the PAT is never part of the
    error (§17); unknown projects 403 (no existence leak, §31.3).
    """
    user, project_id = ctx
    # Fail fast (409) when the S7.1 integration or its secret is missing,
    # before a job could fail at runtime (§19 S7.2, §17).
    try:
        jobs.github_integration_config(db, project_id)
    except jobs.GitHubIntegrationNotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    job_input = body.model_dump()
    job = models.Job(
        project_id=project_id,
        type=JobType.REGRESSION_PR_COMMENT,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_regression_pr_comment_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@projects_router.post(
    "/{project_id}/failures/{failure_id}/jira",
    status_code=202,
    response_model=schemas.JobCreated,
)
def post_failure_jira_link(
    failure_id: str,
    body: schemas.JiraLinkRequest,
    request: Request,
    response: Response,
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """S7.4: file/link a failure as a Jira issue — **202 + job_id** (owner or above).

    ``owner`` or above (it *writes* to Jira, §19 S7.4). The ``jira_link`` job
    builds the deterministic issue payload from the failure + its S4.1 diagnosis
    and create-or-updates it in Jira: first link **creates**, a re-link
    **updates** the stored key in place (never duplicates), and a stale key
    (issue deleted in Jira, 404 on update) is **recreated** and the link
    re-pointed (self-healing). The ``jira.issue`` SSE event carries
    ``action`` / ``key`` / ``url`` / ``project_key``; on success
    ``failures.jira_issue_key`` is persisted (the idempotency anchor).

    409 when the S7.1 Jira integration (or its secret) is missing — the API
    token is never part of the error (§17); 404 when the failure is not part
    of the project; unknown projects 403 (no existence leak, §31.3).
    """
    user, project_id = ctx
    # Fail fast (409) when the S7.1 Jira integration or its secret is missing,
    # before a job could fail at runtime (§19 S7.4, §17).
    try:
        jobs.jira_integration_config(db, project_id)
    except jobs.JiraIntegrationNotConfiguredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Fail fast (404) when the failure is not part of this project.
    if repo_runs.get_failure_in_project(db, project_id, failure_id) is None:
        raise HTTPException(status_code=404, detail=f"failure {failure_id} not found")

    job_input = {"failure_id": failure_id, "project_key": body.project_key}
    job = models.Job(
        project_id=project_id,
        type=JobType.JIRA_LINK,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_jira_link_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@webhooks_router.post("/github")
async def github_webhook(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> JSONResponse:
    """Inbound GitHub webhook (S7.3, §19) — the signature **is** the auth.

    ``X-Hub-Signature-256`` is verified (HMAC-SHA256, constant-time)
    against the project's webhook secret *before* anything else; an
    invalid or missing signature answers **401** (no bearer token, no RBAC
    on this endpoint — §19 S7.3 "the signature IS the auth"). A
    ``pull_request`` ``opened``/``synchronize`` delivery resolves
    ``repository.full_name`` → project and spawns a
    ``regression_analysis`` job (**202 + Location**; the ranked set rides
    the ``regression.set`` SSE event, S6.4). Other events are recorded and
    acknowledged **200** (``status: ignored``).

    The ``X-GitHub-Delivery`` id is unique in ``webhook_events`` — a
    re-sent delivery answers **200** (``status: duplicate``) and never
    spawns a second job (S7.3 exit criterion). 401 when the project has
    no webhook secret configured (the secret's value never appears in any
    response, §17); 409 when no project matches the payload repository,
    or the project has no ``repository_path`` to analyze (fixable — the
    delivery was not recorded, so a corrected re-send is accepted).
    """
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be a JSON object")

    event = request.headers.get("x-github-event") or ""
    action = payload.get("action")
    action_str = str(action) if action is not None else None

    repository = payload.get("repository")
    full_name = str(repository.get("full_name") or "") if isinstance(repository, dict) else ""
    owner, sep, name = full_name.partition("/")
    if not sep or not owner.strip() or not name.strip():
        # No repository in the payload: cannot be bound to a project, so
        # the signature cannot be verified — same 401 as a bad signature
        # (no existence leak for unauthenticated callers, §31.3).
        raise HTTPException(status_code=401, detail="invalid signature")

    project = repo_webhooks.find_project_by_repository(db, owner, name)
    if project is None:
        # 409 (fixable) — no project is configured for this owner/repo;
        # the delivery was not recorded, so a re-send is accepted once a
        # project is set up for this repository.
        raise HTTPException(
            status_code=409,
            detail=f"no project matches GitHub repository {full_name}",
        )

    try:
        secret = jobs.webhook_secret(db, project.id)
    except jobs.WebhookSecretNotConfiguredError:
        # 401 — the signature is the auth; a missing secret is an auth
        # failure, and the detail never carries the secret itself (§17).
        raise HTTPException(status_code=401, detail="invalid signature") from None

    signature_header = request.headers.get("x-hub-signature-256")
    if not webhook_core.verify_github_signature(secret, raw, signature_header):
        raise HTTPException(status_code=401, detail="invalid signature")

    delivery_id = request.headers.get("x-github-delivery") or str(uuid.uuid4())
    row, created = repo_webhooks.record_delivery(
        db,
        project_id=project.id,
        delivery_id=delivery_id,
        event=event,
        action=action_str,
    )
    if not created:
        db.commit()
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "delivery_id": delivery_id},
        )

    if event == "pull_request" and action_str in _WEBHOOK_TRIGGER_ACTIONS:
        repository_path = (project.settings or {}).get("repository_path")
        if not isinstance(repository_path, str) or not repository_path.strip():
            # Roll back the uncommitted delivery row so a corrected
            # re-send (same delivery id) is accepted once configured.
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="project has no repository_path configured (settings.repository_path)",
            )
        pull_request = payload.get("pull_request")
        number = pull_request.get("number") if isinstance(pull_request, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            db.rollback()
            raise HTTPException(status_code=400, detail="payload is missing pull_request.number")

        job_input = {
            "repository_path": repository_path,
            "pull_request": {"owner": owner, "repo": name, "number": number},
            "top_n": 10,
        }
        job = models.Job(
            project_id=project.id,
            type=JobType.REGRESSION_ANALYSIS,
            input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
        )
        db.add(job)
        db.flush()
        row.job_id = job.id
        db.commit()

        state = request.app.state
        if not state.jobs_runner.start(
            job.id, agent=state.jobs_regression_agent, user_id=None, job_input=job_input
        ):
            # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
            raise HTTPException(status_code=409, detail="job is already running")
        return JSONResponse(
            status_code=202,
            content={"job_id": job.id, "status": job.status.value},
            headers={"Location": f"/api/v1/jobs/{job.id}"},
        )

    db.commit()
    return JSONResponse(
        status_code=200,
        content={
            "status": "ignored",
            "event": event,
            "action": action_str,
            "delivery_id": delivery_id,
        },
    )


@projects_router.post("/{project_id}/runs", status_code=202, response_model=schemas.JobCreated)
def run_project_tests(
    project_id: str,
    body: schemas.RunRequest,
    request: Request,
    response: Response,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.JobCreated:
    """S6.4 "Run this set": **202 + job_id** (§19 S6.4 exit criteria, §11).

    No AI work runs in this request (§31.2) — the ``run_execution`` job reuses
    the existing S3.2 execution path: it drives
    ``qa_copilot_execution.run_playwright`` over the selected test files (the
    ``regression.set`` recommendation, repo-relative) and persists the run via
    ``qa_copilot_repository.persist_run`` (so the S6.2 flaky/failure history
    keeps learning from re-runs). The result rides the ``run.result`` SSE
    event — ``run_id`` + ``status`` + per-status ``totals`` — and the job's
    ``output_ref`` is the persisted run id. ``member`` or above; unknown
    projects 403 (no existence leak, §31.3).
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=403, detail="no role for this project")
    _require_project_role(db, user, project.id, ProjectRole.MEMBER)

    job_input = body.model_dump()
    job = models.Job(
        project_id=project.id,
        type=JobType.RUN_EXECUTION,
        input_ref=json.dumps(job_input, separators=(",", ":"))[:1000],
    )
    db.add(job)
    db.commit()

    state = request.app.state
    if not state.jobs_runner.start(
        job.id, agent=state.jobs_run_execution_agent, user_id=user.id, job_input=job_input
    ):
        # Unreachable for a fresh UUID — defensive, keeps start() idempotent.
        raise HTTPException(status_code=409, detail="job is already running")

    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    return schemas.JobCreated(job_id=job.id, status=job.status.value)


@projects_router.get("/{project_id}/knowledge/status", response_model=schemas.KnowledgeStatus)
def get_knowledge_status(
    project_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.KnowledgeStatus:
    """What is indexed for a project (S5.3, §14) — ``viewer`` or above."""
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    return schemas.KnowledgeStatus(**knowledge_store.knowledge_status(db, project.id))


@projects_router.get("/{project_id}/knowledge", response_model=schemas.KnowledgeSearchResult)
def search_project_knowledge_route(
    project_id: str,
    q: str = Query(..., min_length=1, max_length=512, description="Search query"),
    top_k: int = Query(default=5, ge=1, le=5, description="Max chunks (≤ 5, §14)"),
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.KnowledgeSearchResult:
    """Search the project's knowledge (S5.3, §14) — ``viewer`` or above.

    Lexical (BM25) retrieval over the project's stored documents, hard-capped
    at five chunks (top-k ≤ 5, §14) to fit the agent context budget.
    """
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    result = knowledge_store.search_project_knowledge(db, project.id, q, top_k=top_k)
    return schemas.KnowledgeSearchResult(
        query=result.query,
        total_candidates=result.total_candidates,
        truncated=result.truncated,
        hits=[_knowledge_hit_out(hit) for hit in result.hits],
    )


@projects_router.get(
    "/{project_id}/knowledge/documents",
    response_model=list[schemas.KnowledgeDocumentOut],
)
def list_knowledge_documents(
    project_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.KnowledgeDocumentOut]:
    """The project's stored knowledge documents, newest first (S5.3)."""
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    rows = knowledge_store.list_project_knowledge_documents(
        db, project.id, limit=limit, offset=offset
    )
    return [_knowledge_document_out(row) for row in rows]


@projects_router.get(
    "/{project_id}/knowledge/documents/{document_id}",
    response_model=schemas.KnowledgeDocumentOut,
)
def get_knowledge_document(
    project_id: str,
    document_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.KnowledgeDocumentOut:
    """One stored knowledge document (S5.3) — ``viewer`` or above."""
    project = db.get(models.Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    _require_project_role(db, user, project.id, ProjectRole.VIEWER)
    row = db.scalar(
        select(models.KnowledgeDocument).where(
            models.KnowledgeDocument.id == document_id,
            models.KnowledgeDocument.project_id == project.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="document not found")
    return _knowledge_document_out(row)


@runs_router.get("/{run_id}", response_model=schemas.RunDetail)
def get_run(
    run_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.RunDetail:
    """One run + its results and artifacts (S3.2, ``viewer`` or above)."""
    run = _get_run_for_read(db, user, run_id)
    return _run_detail(run, list(repo_runs.list_results(db, run.id)))


@runs_router.get("/{run_id}/results", response_model=list[schemas.TestResultOut])
def list_results(
    run_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.TestResultOut]:
    """A run's test outcomes (S3.2, ``viewer`` or above)."""
    run = _get_run_for_read(db, user, run_id)
    return [_result_out(run.id, result) for result in repo_runs.list_results(db, run.id)]


@runs_router.get("/{run_id}/artifacts", response_model=list[schemas.ArtifactOut])
def list_artifacts(
    run_id: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.ArtifactOut]:
    """A run's artifact rows (S3.2, ``viewer`` or above)."""
    run = _get_run_for_read(db, user, run_id)
    return [_artifact_out(run.id, artifact) for artifact in repo_runs.list_artifacts(db, run.id)]


@runs_router.get("/{run_id}/artifacts/{artifact_id}/content")
def get_artifact_content(
    run_id: str,
    artifact_id: str,
    request: Request,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FileResponse:
    """Stream an artifact's file bytes (S3.2 download, ``viewer`` or above).

    The file is resolved through :class:`ArtifactStore` (never a raw path): a
    store-relative URI resolves under the store root; anything that escapes the
    root, or that is missing on disk (e.g. a seed ``file://`` placeholder),
    yields 404.
    """
    run = _get_run_for_read(db, user, run_id)
    artifact = db.scalar(
        select(models.Artifact)
        .join(models.TestResult, models.Artifact.test_result_id == models.TestResult.id)
        .where(models.Artifact.id == artifact_id, models.TestResult.run_id == run.id)
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")

    store_root = request.app.state.settings.artifact_store_root or "data/artifacts"
    store = ArtifactStore(store_root)
    try:
        path = store.resolve(artifact.uri)
    except ArtifactStoreError:
        raise HTTPException(status_code=404, detail="artifact not found in store") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="artifact not found in store")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


# --- integrations (S7.1, §19 S7.1) ----------------------------------------------

#: Provider slug: lowercase alphanumerics + ``_``/``-``. Kept open (V1:
#: ``github``; S7.4 adds ``jira``) so the table needs no per-provider migration.
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _validate_provider(provider: str) -> str:
    if not _PROVIDER_RE.fullmatch(provider):
        raise HTTPException(
            status_code=422,
            detail="provider must be 1-32 chars of [a-z0-9_-] starting with an alphanumeric",
        )
    return provider


def _integration_out(config: models.IntegrationConfig) -> schemas.IntegrationConfigOut:
    # §17: the token value is never in the payload — only whether a ref is set.
    return schemas.IntegrationConfigOut(
        project_id=config.project_id,
        provider=config.provider,
        base_url=config.base_url,
        token_ref=config.token_ref,
        token_configured=bool(config.token_ref),
        enabled=config.enabled,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


@integrations_router.get(
    "/{project_id}/integrations", response_model=list[schemas.IntegrationConfigOut]
)
def list_integrations(
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.MEMBER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.IntegrationConfigOut]:
    """S7.1: the project's integration configs — ``member`` or above (§19 S7.1).

    401 unauthenticated, 403 for non-members / viewers (no existence leak).
    """
    _, project_id = ctx
    return [_integration_out(c) for c in repo_integrations.list_integrations(db, project_id)]


@integrations_router.put(
    "/{project_id}/integrations/{provider}", response_model=schemas.IntegrationConfigOut
)
def upsert_integration(
    provider: str,
    body: schemas.IntegrationConfigIn,
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.IntegrationConfigOut:
    """S7.1: create-or-update the project's config for *provider* — ``owner``+.

    Idempotent PUT: one row per (project, provider) (unique constraint).
    Stores ``token_ref`` (the secret's name) — never a token value (§17).
    """
    provider = _validate_provider(provider)
    _, project_id = ctx
    config = repo_integrations.upsert_integration(
        db,
        project_id,
        provider,
        base_url=body.base_url,
        token_ref=body.token_ref,
        enabled=body.enabled,
    )
    db.commit()
    db.refresh(config)
    return _integration_out(config)


@integrations_router.delete("/{project_id}/integrations/{provider}", status_code=204)
def delete_integration(
    provider: str,
    ctx: tuple[models.User, str] = Depends(auth.require_role(ProjectRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """S7.1: remove the project's config for *provider* — ``owner`` or above."""
    provider = _validate_provider(provider)
    _, project_id = ctx
    if not repo_integrations.delete_integration(db, project_id, provider):
        raise HTTPException(status_code=404, detail="no integration config for this project")
    db.commit()


# --- Teams: organizations, members, invites (S8.2, bible §19) ------------------


def _org_member_row(
    db: Session, organization_id: str, member_id: str
) -> models.OrganizationMember | None:
    """The (org, user) membership row, or ``None``."""
    return db.scalar(
        select(models.OrganizationMember).where(
            models.OrganizationMember.organization_id == organization_id,
            models.OrganizationMember.user_id == member_id,
        )
    )


def _org_member_out(db: Session, row: models.OrganizationMember) -> schemas.OrgMemberOut:
    user = db.get(models.User, row.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="member user no longer exists")
    return schemas.OrgMemberOut(
        id=row.user_id,
        email=user.email,
        role=row.role,
        joined_at=row.created_at,
    )


@organizations_router.get("", response_model=list[schemas.OrganizationOut])
def list_organizations(
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.OrganizationOut]:
    """S8.2: the caller's organizations with their org role + member count."""
    out: list[schemas.OrganizationOut] = []
    for membership_row in membership.user_orgs(db, user.id):
        org = db.get(models.Organization, membership_row.organization_id)
        if org is None:
            continue
        member_count = db.scalar(
            select(func.count())
            .select_from(models.OrganizationMember)
            .where(models.OrganizationMember.organization_id == org.id)
        )
        out.append(
            schemas.OrganizationOut(
                id=org.id,
                name=org.name,
                role=membership_row.role,
                member_count=int(member_count or 0),
                created_at=org.created_at,
            )
        )
    return out


@organizations_router.get("/{organization_id}/members", response_model=list[schemas.OrgMemberOut])
def list_org_members(
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.MEMBER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.OrgMemberOut]:
    """S8.2/S8.3: the org's member roster — any member may read.

    The ``require_org_role`` dependency enforces membership (non-member →
    403, audited as ``org.gate.denied``) — never a 404 leak.
    """
    _, organization_id = ctx
    rows = db.scalars(
        select(models.OrganizationMember)
        .where(models.OrganizationMember.organization_id == organization_id)
        .order_by(models.OrganizationMember.created_at, models.OrganizationMember.user_id)
    ).all()
    return [_org_member_out(db, row) for row in rows]


@organizations_router.post(
    "/{organization_id}/members", status_code=201, response_model=schemas.OrgMemberOut
)
def add_org_member(
    request: Request,
    body: schemas.AddOrgMemberRequest,
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.OrgMemberOut:
    """S8.2/S8.3: add an **existing** account (by email) to the org — ``owner`` only.

    New people join via a code invite (``POST .../invites``). Unknown email →
    404; already a member → 409; a second ``owner`` → 409 (one owner per org,
    enforced at the DB level). S8.3: every outcome is audited as
    ``org.membership.add`` (success or failure) — the gate itself audits
    denials as ``org.gate.denied``.
    """
    user, organization_id = ctx
    ip = _client_ip(request)
    target = db.scalar(select(models.User).where(models.User.email == body.email))
    if target is None:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_ADD,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=404, detail="no user with that email")
    if membership.get_org_role(db, organization_id, target.id) is not None:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_ADD,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="user is already a member of this organization")
    if body.role == "owner" and membership.get_org_owner_id(db, organization_id) is not None:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_ADD,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="organization already has an owner")
    row = models.OrganizationMember(
        organization_id=organization_id,
        user_id=target.id,
        role=OrgRole(body.role),
    )
    db.add(row)
    # S8.3: the success row commits with the membership row.
    _audit(
        db,
        AuditAction.ORG_MEMBERSHIP_ADD,
        actor_id=user.id,
        target=organization_id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_ADD,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="organization membership conflict") from exc
    db.refresh(row)
    return _org_member_out(db, row)


@organizations_router.patch(
    "/{organization_id}/members/{member_id}", response_model=schemas.OrgMemberOut
)
def update_org_member(
    request: Request,
    member_id: str,
    body: schemas.UpdateOrgMemberRequest,
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.OrgMemberOut:
    """S8.2/S8.3: change a member's org role — ``owner`` only.

    Promoting someone to ``owner`` **transfers** ownership (the acting owner
    steps down to ``member`` — one owner per org, §19 S8.2). Demoting the
    org's sole ``owner`` → 409. Unknown member → 404. S8.3: audited as
    ``org.membership.update`` (success or failure).
    """
    user, organization_id = ctx
    ip = _client_ip(request)
    row = _org_member_row(db, organization_id, member_id)
    if row is None:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_UPDATE,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=404, detail="member not found")
    current_owner_id = membership.get_org_owner_id(db, organization_id)
    if body.role == "owner" and row.role is not OrgRole.OWNER:
        if current_owner_id is not None and current_owner_id not in (member_id, user.id):
            _audit(
                db,
                AuditAction.ORG_MEMBERSHIP_UPDATE,
                actor_id=user.id,
                target=organization_id,
                outcome=AuditOutcome.FAILURE,
                ip=ip,
            )
            raise HTTPException(status_code=409, detail="cannot reassign ownership")
        if current_owner_id == user.id:
            # Ownership transfer: step the acting owner down to ``member`` and
            # flush **before** promoting the target — Postgres checks the
            # single-owner index per statement, so the intermediate state must
            # never hold two owners. Both changes stay in one transaction
            # (a flush does not commit), so the transfer is atomic.
            actor_row = _org_member_row(db, organization_id, user.id)
            if actor_row is not None:
                actor_row.role = OrgRole.MEMBER
                db.flush()
    elif body.role == "member" and row.role is OrgRole.OWNER and current_owner_id == member_id:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_UPDATE,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="cannot demote the organization owner")
    row.role = OrgRole(body.role)
    # S8.3: the success row commits with the role change.
    _audit(
        db,
        AuditAction.ORG_MEMBERSHIP_UPDATE,
        actor_id=user.id,
        target=organization_id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_UPDATE,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="organization already has an owner") from exc
    return _org_member_out(db, row)


@organizations_router.delete("/{organization_id}/members/{member_id}", status_code=204)
def remove_org_member(
    request: Request,
    member_id: str,
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.MEMBER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """S8.2/S8.3: remove a member — the org's ``owner`` (or the member themselves).

    Any member may remove **themselves** (leave); only the ``owner`` may
    remove others (403 otherwise). Removing the org's sole ``owner`` → 409;
    unknown member → 404. S8.3: audited as ``org.membership.remove`` /
    ``org.membership.leave`` (self-removal); a non-owner removing someone
    else is a gate denial (``org.gate.denied``).
    """
    user, organization_id = ctx
    ip = _client_ip(request)
    row = _org_member_row(db, organization_id, member_id)
    if row is None:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_REMOVE,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=404, detail="member not found")
    role = membership.get_org_role(db, organization_id, user.id)
    if member_id != user.id and role is not OrgRole.OWNER:
        _audit(
            db,
            AuditAction.ORG_GATE_DENIED,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.DENIED,
            ip=ip,
        )
        raise HTTPException(status_code=403, detail="only the owner can remove other members")
    if row.role is OrgRole.OWNER and membership.get_org_owner_id(db, organization_id) == member_id:
        _audit(
            db,
            AuditAction.ORG_MEMBERSHIP_REMOVE,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=409, detail="cannot remove the organization owner")
    action = (
        AuditAction.ORG_MEMBERSHIP_LEAVE
        if member_id == user.id
        else AuditAction.ORG_MEMBERSHIP_REMOVE
    )
    # S8.3: the success row commits with the membership deletion.
    _audit(
        db,
        action,
        actor_id=user.id,
        target=organization_id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    db.delete(row)
    db.commit()


@organizations_router.post(
    "/{organization_id}/invites", status_code=201, response_model=schemas.InviteOut
)
def create_invite(
    request: Request,
    body: schemas.InviteRequest,
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.InviteOut:
    """S8.2/S8.3: create a one-time invite code — ``owner`` only.

    The single-use ``code`` is returned **exactly once** (only its SHA-256
    hash is stored, §17) and expires 7 days out (§19 S8.2). S8.3: audited as
    ``org.invite.create``.
    """
    user, organization_id = ctx
    row, code = repo_invites.issue_invite(
        db,
        organization_id=organization_id,
        email=body.email,
        role=OrgRole(body.role),
        now=datetime.now(UTC),
    )
    # S8.3: the success row commits with the invite row.
    _audit(
        db,
        AuditAction.ORG_INVITE_CREATE,
        actor_id=user.id,
        target=organization_id,
        outcome=AuditOutcome.SUCCESS,
        ip=_client_ip(request),
        commit=False,
    )
    db.commit()
    db.refresh(row)
    return schemas.InviteOut(
        id=row.id,
        email=row.email,
        role=row.role,
        code=code,
        created_at=row.created_at,
        expires_at=row.expires_at,
    )


@organizations_router.get("/{organization_id}/audit", response_model=list[schemas.AuditEventOut])
def export_org_audit(
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[schemas.AuditEventOut]:
    """S8.3: the org's audit trail, newest first — the org's ``owner`` only.

    Every event targeting the org (membership changes, invites, deletion,
    gate denials…) — capped at the most recent 200 rows (§19 S8.3).
    Non-owners → 403 (audited as ``org.gate.denied``); the audit trail is
    never exposed to members or outsiders.
    """
    _, organization_id = ctx
    rows = repo_security_audit.list_for_target(db, organization_id, limit=200)
    return [
        schemas.AuditEventOut(
            actor_id=row.actor_id,
            action=row.action,
            target=row.target,
            outcome=row.outcome,
            ip=row.ip,
            at=row.at,
        )
        for row in rows
    ]


@organizations_router.delete("/{organization_id}", status_code=204)
def delete_organization(
    request: Request,
    body: schemas.DeleteOrganizationRequest,
    ctx: tuple[models.User, str] = Depends(auth.require_org_role(OrgRole.OWNER)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> None:
    """S8.2/S8.3: hard-delete the organization — the org's ``owner``, re-authenticated.

    S8.3 re-authentication: the body carries the owner's **current password**;
    a wrong password → 401 (``org.delete.reauth_failure``) and *nothing* is
    deleted. The password is verified against the hash and never stored or
    audited (§17).

    Deletion cascade (§17): the org's **projects** are deleted first —
    ``projects.organization_id`` has no ON DELETE rule, and deleting a
    project cascades its memberships, requirements, test cases, runs,
    results, artifacts, sessions, jobs and webhooks — then the org row
    itself (``organization_members`` / ``organization_invites`` are ON
    DELETE CASCADE). Every former member's active refresh tokens are
    revoked, so a deleted org leaves no path back in; the users themselves
    (and their other orgs) are untouched. Non-owners → 403 (audited as
    ``org.gate.denied``; membership check precedes lookup — never a 404
    leak).
    """
    user, organization_id = ctx
    ip = _client_ip(request)
    if not auth.check_password(body.current_password, user.password_hash):
        _audit(
            db,
            AuditAction.ORG_DELETE_REAUTH_FAILURE,
            actor_id=user.id,
            target=organization_id,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=401, detail="current password is incorrect")
    member_ids = list(
        db.scalars(
            select(models.OrganizationMember.user_id).where(
                models.OrganizationMember.organization_id == organization_id
            )
        )
    )
    # S8.3: the success row commits with the whole cascade — and outlives it
    # (``target`` is an opaque id; ``actor_id`` nulls out if the owner then
    # deletes their account, §17).
    _audit(
        db,
        AuditAction.ORG_DELETE,
        actor_id=user.id,
        target=organization_id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    db.execute(delete(models.Project).where(models.Project.organization_id == organization_id))
    db.execute(delete(models.Organization).where(models.Organization.id == organization_id))
    if member_ids:
        db.execute(
            update(models.UserRefreshToken)
            .where(
                models.UserRefreshToken.user_id.in_(member_ids),
                models.UserRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
    db.commit()


@invites_router.post("/{code}/accept", response_model=schemas.InviteAcceptResult)
def accept_invite(
    request: Request,
    code: str,
    user: models.User = Depends(auth.get_current_user),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> schemas.InviteAcceptResult:
    """S8.2/S8.3: accept a one-time invite code — join the org with the invited role.

    The account's email must match the invite's email (403). Unknown,
    expired, or already-used codes all → 404 (no reason leak — §17).
    Already a member → 409. S8.3: audited as ``org.invite.accept`` —
    success (with the joined org as target) or failure (org id is unknown,
    so the target is ``None``). The invite code itself is never audited
    (§17).
    """
    ip = _client_ip(request)
    try:
        member, org = repo_invites.accept_invite(db, code=code, user=user, now=datetime.now(UTC))
    except repo_invites.InviteError as exc:
        status_code = {
            repo_invites.InviteErrorKind.UNKNOWN: 404,
            repo_invites.InviteErrorKind.EMAIL_MISMATCH: 403,
            repo_invites.InviteErrorKind.ALREADY_MEMBER: 409,
        }[exc.kind]
        _audit(
            db,
            AuditAction.ORG_INVITE_ACCEPT,
            actor_id=user.id,
            target=None,
            outcome=AuditOutcome.FAILURE,
            ip=ip,
        )
        raise HTTPException(status_code=status_code, detail=exc.detail) from exc
    # S8.3: the success row commits with the membership join.
    _audit(
        db,
        AuditAction.ORG_INVITE_ACCEPT,
        actor_id=user.id,
        target=org.id,
        outcome=AuditOutcome.SUCCESS,
        ip=ip,
        commit=False,
    )
    db.commit()
    return schemas.InviteAcceptResult(
        organization=schemas.OrganizationRef(id=org.id, name=org.name, role=member.role),
        role=member.role,
    )
