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
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from qa_copilot_domain.enums import (
    GeneratedTestStatus,
    JobType,
    ProjectRole,
    role_at_least,
)
from qa_copilot_repository import db as repo_db
from qa_copilot_repository import generated_tests as repo_generated_tests
from qa_copilot_repository import membership, models
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from . import auth, jobs, schemas
from .db import get_db

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
projects_router = APIRouter(prefix="/api/v1/projects", tags=["projects"])
requirements_router = APIRouter(prefix="/api/v1/requirements", tags=["requirements"])
jobs_router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
events_router = APIRouter(prefix="/api/v1/events", tags=["events"])
automation_router = APIRouter(prefix="/api/v1/automation", tags=["automation"])
generated_tests_router = APIRouter(prefix="/api/v1/generated-tests", tags=["generated-tests"])


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
