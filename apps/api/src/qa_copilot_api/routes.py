"""HTTP routes (build bible §7).

S0.3: ``GET /health`` (inline in ``main.py``).
S0.8: auth baseline (§31.3) — ``POST /api/v1/auth/login``,
``GET /api/v1/auth/me`` and project endpoints gated by project-scoped roles:

- ``GET /api/v1/projects``          — auth (any member)
- ``GET /api/v1/projects/{id}``     — ``viewer`` or above
- ``DELETE /api/v1/projects/{id}``  — ``owner`` (§31.3: project deletion)

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
"""

from __future__ import annotations

import json
import mimetypes
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from qa_copilot_domain.enums import (
    GeneratedTestStatus,
    JobType,
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
from qa_copilot_repository import membership, models
from qa_copilot_repository import requirements as repo_requirements
from qa_copilot_repository import runs as repo_runs
from qa_copilot_repository import webhooks as repo_webhooks
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import FileResponse, JSONResponse, StreamingResponse

from . import auth, jobs, knowledge_store, schemas
from .db import get_db

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

#: S7.3: the only webhook deliveries that spawn a job (§19: "``pull_request``
#: opened/synchronize → ``regression_analysis`` job"); every other event is
#: recorded + acknowledged 200 (``ignored``).
_WEBHOOK_TRIGGER_ACTIONS = frozenset({"opened", "synchronize"})


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
