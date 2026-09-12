"""S8.4 organization billing (plans, quotas, usage) — build bible §19 S8.4.

Covers the billing core and the HTTP layer end-to-end:

- plan catalog: closed set (``free``/``pro``/``enterprise``), ``dev``/NULL/
  unknown stored values fall back to ``free`` (a stale value never unlocks
  premium capacity), monotonic caps
- ``check_quota``: fixed order ``concurrent_jobs`` → ``runs_per_month``
  (``run_execution`` only) → ``tokens_per_month``
- ``org_usage``: live metering — this-month runs/tokens (never counters),
  point-in-time in-flight jobs and projects
- dispatch gate: a quota violation removes the job row, publishes the
  terminal ``job.rejected`` SSE event, answers **409** with the
  ``plan_limit`` body (``plan``/``limit``/``used``/``allowed`` +
  org/project/job ids), records ``org.quota.denied`` (actor + client IP),
  and starts no AI work
- ``GET /organizations/{id}/plan`` + ``/usage`` (member or above; outsider
  403 audited ``org.gate.denied``; unauthenticated 401)
- ``PATCH /organizations/{id}`` plan assignment: owner only, closed plan
  set (422 for anything else — RBAC first: a non-owner's invalid body is a
  403), audited ``org.plan.updated``, no-op PATCHs audit nothing
- upgrading the plan relaxes the caps (free 409 → pro 202)

DB-backed tests follow the S8.3 pattern: one scratch Postgres database per
test, skipped when Postgres is not reachable.
"""

from __future__ import annotations

import os
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth
from qa_copilot_api import jobs as api_jobs
from qa_copilot_api import main as api_main
from qa_copilot_api.config import Settings
from qa_copilot_domain.enums import (
    AuditAction,
    AuditOutcome,
    JobStatus,
    JobType,
    OrgRole,
    ProjectRole,
    RunStatus,
)
from qa_copilot_repository import billing as repo_billing
from qa_copilot_repository import db as repo_db
from qa_copilot_repository import models
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
TEST_DB = "qa_copilot_s84_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"

SECRET = "s84-billing-test-secret-0123456789"  # 16+ chars, test-only
FREE = repo_billing.PLAN_CATALOG["free"]

# ids are Postgres UUIDs — deterministic values, stable across runs (S8.3)
NS = uuid.NAMESPACE_DNS
USER_IDS = {
    "alice": str(uuid.uuid5(NS, "s84-user-alice")),
    "bob": str(uuid.uuid5(NS, "s84-user-bob")),
    "carol": str(uuid.uuid5(NS, "s84-user-carol")),
}
EMAILS = {name: f"{name}.s84@local.dev" for name in USER_IDS}


def _postgres_up(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


needs_postgres = pytest.mark.skipif(
    not _postgres_up("localhost", 5433),
    reason="Postgres is not reachable (start the S0.2 stack / docker-compose)",
)


def _admin(sql: str) -> None:
    """Run DDL against the ``postgres`` maintenance database (S8.3)."""
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


class StubAgent:
    """Hermetic job agent for the allowed-dispatch test.

    Conforms to the ``JobAgent`` protocol (``stages`` + ``async run(ctx)``).
    The runner itself creates the job's ``ai_sessions`` audit anchor before
    calling the agent (``jobs.JobRunner._run``), so the stub only returns
    an output ref — the session row is proof the gate let work start.
    """

    stages: tuple[str, ...] = ("stub",)

    async def run(self, ctx: api_jobs.JobContext) -> str:
        return "s84-stub-output"


@pytest.fixture()
def api() -> Iterator[TestClient]:
    """Scratch Postgres database + alembic + the API app (the S8.3 pattern)."""
    try:
        _admin("SELECT 1")
    except Exception:  # noqa: BLE001 — any failure means "no database"
        pytest.skip("no Postgres reachable — S8.4 API tests need a database")

    saved_url = os.environ.get("DATABASE_URL")
    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin(f"CREATE DATABASE {TEST_DB}")

    os.environ["DATABASE_URL"] = TEST_URL  # alembic env.py: env var wins
    engine = repo_db.make_engine(TEST_URL)
    command.upgrade(Config(str(ALEMBIC_INI)), "head")

    app = api_main.create_app(
        # ``_env_file=None`` is pydantic-settings' private init kwarg (keep
        # tests from reading the dev .env) — mypy can't see it in the stubs.
        settings=Settings(  # type: ignore[call-arg]
            database_url=TEST_URL, auth_token_secret=SECRET, _env_file=None
        )
    )
    with TestClient(app) as test_client:
        yield test_client

    # close pooled connections before DROP DATABASE, or Postgres refuses
    app.state.engine.dispose()
    engine.dispose()
    if saved_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved_url
    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scalar(api: TestClient, sql: str, params: dict | None = None) -> int | None:
    with repo_db.session_scope(api.app.state.engine) as db:
        return db.scalar(text(sql), params or {})


def _audit_rows(api: TestClient, action: str) -> list[dict]:
    """Audit rows for *action*; Postgres UUID columns normalized to ``str``."""
    with repo_db.session_scope(api.app.state.engine) as db:
        rows = (
            db.execute(
                text(
                    "SELECT actor_id, action, target, outcome, ip, at FROM audit_log "
                    "WHERE action=:a ORDER BY at DESC, id DESC"
                ),
                {"a": action},
            )
            .mappings()
            .all()
        )
    out: list[dict] = []
    for row in rows:
        d = dict(row)
        for key in ("actor_id", "target"):
            if d.get(key) is not None:
                d[key] = str(d[key])
        out.append(d)
    return out


def _month_anchors() -> tuple[datetime, datetime]:
    """(this-month, last-month) timestamps safe across month boundaries.

    ``month_start + 2d`` is always the current calendar month;
    ``month_start - 1d`` is always the previous one — no 1st-of-month flake.
    """
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_start + timedelta(days=2), month_start - timedelta(days=1)


def _job(project_id: str, status: JobStatus) -> models.Job:
    return models.Job(project_id=project_id, type=JobType.REQUIREMENT_ANALYSIS, status=status)


def _run(project_id: str, at: datetime) -> models.TestRun:
    return models.TestRun(
        id=uuid.uuid4().hex, project_id=project_id, status=RunStatus.COMPLETED, created_at=at
    )


def _seed_runs(api: TestClient, project_id: str, count: int, at: datetime) -> None:
    with repo_db.session_scope(api.app.state.engine) as db:
        for _ in range(count):
            db.add(_run(project_id, at))
        db.commit()


def _seed_tokens(api: TestClient, project_id: str, total: int, at: datetime) -> None:
    """Seed ``total`` LLM tokens (in+out) in one session's action row."""
    with repo_db.session_scope(api.app.state.engine) as db:
        session = models.AISession(
            id=uuid.uuid4().hex,
            project_id=project_id,
            task_type="s84seed",
            status="active",
            created_at=at,
        )
        db.add(session)
        db.flush()
        db.add(
            models.AIAction(
                id=uuid.uuid4().hex,
                session_id=session.id,
                agent="s84seed",
                model="stub",
                tokens_in=total // 2,
                tokens_out=total - total // 2,
                latency_ms=1,
                created_at=at,
            )
        )
        db.commit()


def _seed_job(api: TestClient, project_id: str, status: JobStatus) -> None:
    with repo_db.session_scope(api.app.state.engine) as db:
        db.add(_job(project_id, status))
        db.commit()


@pytest.fixture()
def world(api: TestClient) -> dict[str, Any]:
    """alice=org owner, bob=org member, carol=outsider; one project.

    The org keeps its stored plan ``dev`` (the S0.5 default) until a test
    assigns one — so the plan endpoint doubles as the fallback test.
    """
    with repo_db.session_scope(api.app.state.engine) as db:
        for name in USER_IDS:
            db.add(models.User(id=USER_IDS[name], email=EMAILS[name], role="developer"))
        org = models.Organization(name="S8.4 Org")
        db.add(org)
        db.flush()
        db.add(
            models.OrganizationMember(
                organization_id=org.id, user_id=USER_IDS["alice"], role=OrgRole.OWNER
            )
        )
        db.add(
            models.OrganizationMember(
                organization_id=org.id, user_id=USER_IDS["bob"], role=OrgRole.MEMBER
            )
        )
        project = models.Project(name="S8.4 Project", organization_id=org.id)
        db.add(project)
        db.flush()
        db.add(
            models.ProjectMember(
                project_id=project.id, user_id=USER_IDS["alice"], role=ProjectRole.OWNER
            )
        )
        db.add(
            models.ProjectMember(
                project_id=project.id, user_id=USER_IDS["bob"], role=ProjectRole.MEMBER
            )
        )
        db.commit()
        org_id, project_id = org.id, project.id

    users = {
        name: {
            "id": USER_IDS[name],
            "token": auth.create_access_token(USER_IDS[name], EMAILS[name], SECRET),
        }
        for name in USER_IDS
    }
    return {"users": users, "org_id": org_id, "project_id": project_id}


# --- billing core: plan catalog -------------------------------------------------


def test_plan_for_falls_back_to_free_for_seed_null_and_unknown() -> None:
    for raw in ("dev", None, "", "unknown-plan", "ENTERPRISE"):
        assert repo_billing.plan_for(raw) is FREE


def test_plan_for_resolves_every_catalog_name() -> None:
    for name, spec in repo_billing.PLAN_CATALOG.items():
        assert repo_billing.plan_for(name) is spec
    assert set(repo_billing.ASSIGNABLE_PLANS) == {"free", "pro", "enterprise"}
    assert repo_billing.DEFAULT_PLAN == "free"


def test_plan_caps_are_monotonic_and_positive() -> None:
    for cap in ("max_projects", "runs_per_month", "tokens_per_month", "concurrent_jobs"):
        values = [getattr(repo_billing.PLAN_CATALOG[n], cap) for n in ("free", "pro", "enterprise")]
        assert values == sorted(values)
        assert all(v > 0 for v in values)


def test_month_label_format() -> None:
    y, m = repo_billing.month_label().split("-")
    assert len(y) == 4 and y.isdigit() and 1 <= int(m) <= 12


def test_job_rejected_is_a_terminal_sse_event() -> None:
    assert "job.rejected" in api_jobs.TERMINAL_EVENTS


# --- billing core: quota order (direct repo calls) -------------------------------


@needs_postgres
def test_check_quota_fixed_order(api: TestClient, world: dict) -> None:
    this_month, _ = _month_anchors()
    org_id, project_id = world["org_id"], world["project_id"]
    with repo_db.session_scope(api.app.state.engine) as db:
        # free org, 1 running + 1 pending job, 25 runs this month, 100k
        # tokens: every cap is at/over its limit — the fixed *order* must
        # decide.
        db.add(_job(project_id, JobStatus.RUNNING))
        db.flush()
        current = _job(project_id, JobStatus.PENDING)
        db.add(current)
        db.flush()
        for _ in range(25):
            db.add(_run(project_id, this_month))
        session = models.AISession(
            id=uuid.uuid4().hex,
            project_id=project_id,
            task_type="s84",
            status="active",
            created_at=this_month,
        )
        db.add(session)
        db.flush()
        db.add(
            models.AIAction(
                id=uuid.uuid4().hex,
                session_id=session.id,
                agent="s84",
                model="stub",
                tokens_in=50_000,
                tokens_out=50_000,
                latency_ms=1,
                created_at=this_month,
            )
        )
        db.commit()
        # 1) concurrent_jobs fires first — 2 in-flight >= 1 — and the
        #    current job's own slot is excluded (1 >= 1 still denies).
        result = repo_billing.check_quota(
            db, org_id, job_type=JobType.REQUIREMENT_ANALYSIS, exclude_job_id=current.id
        )
        assert result.limit == "concurrent_jobs"
        assert (result.used, result.allowed) == (1, FREE.concurrent_jobs)
        result = repo_billing.check_quota(db, org_id, job_type=JobType.REQUIREMENT_ANALYSIS)
        assert result.limit == "concurrent_jobs"
        assert (result.used, result.allowed) == (2, FREE.concurrent_jobs)
        # 2) No in-flight jobs: a run_execution job hits runs_per_month
        #    (25 >= 25) *before* tokens_per_month.
        db.execute(text("DELETE FROM jobs WHERE project_id=:p"), {"p": project_id})
        db.commit()
        result = repo_billing.check_quota(db, org_id, job_type=JobType.RUN_EXECUTION)
        assert (result.limit, result.used, result.allowed) == (
            "runs_per_month",
            FREE.runs_per_month,
            FREE.runs_per_month,
        )
        # 3) Non-run job: the runs gate is skipped → tokens_per_month
        #    (100k >= 100k) is the last gate.
        result = repo_billing.check_quota(db, org_id, job_type=JobType.REQUIREMENT_ANALYSIS)
        assert (result.limit, result.used, result.allowed) == (
            "tokens_per_month",
            FREE.tokens_per_month,
            FREE.tokens_per_month,
        )
        # 4) A different org with no usage is within budget (limit=None).
        other_org = models.Organization(name="S8.4 Other")
        db.add(other_org)
        db.commit()
        result = repo_billing.check_quota(db, other_org.id, job_type=JobType.RUN_EXECUTION)
        assert result.limit is None
        assert (result.used, result.allowed) == (0, 0)


@needs_postgres
def test_org_usage_metering_is_live_and_month_scoped(api: TestClient, world: dict) -> None:
    this_month, last_month = _month_anchors()
    project_id, alice = world["project_id"], world["users"]["alice"]
    with repo_db.session_scope(api.app.state.engine) as db:
        # 2 runs this month + 3 last month (excluded)
        for at in (this_month, this_month, last_month, last_month, last_month):
            db.add(_run(project_id, at))
        # 70k in + 30k out this month + 50k last month (excluded)
        for at, tok_in, tok_out in ((this_month, 70_000, 30_000), (last_month, 50_000, 0)):
            session = models.AISession(
                id=uuid.uuid4().hex,
                project_id=project_id,
                user_id=alice["id"],
                task_type="s84",
                status="active",
                created_at=at,
            )
            db.add(session)
            db.flush()
            db.add(
                models.AIAction(
                    id=uuid.uuid4().hex,
                    session_id=session.id,
                    agent="s84",
                    model="stub",
                    tokens_in=tok_in,
                    tokens_out=tok_out,
                    latency_ms=1,
                    created_at=at,
                )
            )
        # 1 in-flight job (counts) + 1 completed (does not)
        db.add(_job(project_id, JobStatus.PENDING))
        db.add(_job(project_id, JobStatus.COMPLETED))
        db.commit()
    with repo_db.session_scope(api.app.state.engine) as db:
        usage = repo_billing.org_usage(db, world["org_id"])
        assert usage.runs == 2
        assert usage.tokens == 100_000
        assert usage.active_jobs == 1
        assert usage.projects == 1


# --- plan + usage endpoints (RBAC, fallback, metering over HTTP) -----------------


@needs_postgres
def test_plan_endpoint_resolves_dev_to_free_and_exposes_caps(api: TestClient, world: dict) -> None:
    r = api.get(
        f"/api/v1/organizations/{world['org_id']}/plan",
        headers=_auth(world["users"]["bob"]["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "free"  # stored "dev" resolved through the catalog
    assert body["limits"] == {
        "max_projects": FREE.max_projects,
        "runs_per_month": FREE.runs_per_month,
        "tokens_per_month": FREE.tokens_per_month,
        "concurrent_jobs": FREE.concurrent_jobs,
    }


@needs_postgres
def test_plan_and_usage_endpoints_rbac(api: TestClient, world: dict) -> None:
    base = f"/api/v1/organizations/{world['org_id']}"
    carol, alice = world["users"]["carol"], world["users"]["alice"]

    # Outsider → 403 (not 404) on both endpoints, each audited org.gate.denied.
    for path in (f"{base}/plan", f"{base}/usage"):
        before = len(_audit_rows(api, AuditAction.ORG_GATE_DENIED.value))
        r = api.get(path, headers=_auth(carol["token"]))
        assert r.status_code == 403
        rows = _audit_rows(api, AuditAction.ORG_GATE_DENIED.value)
        assert len(rows) == before + 1
        assert rows[0]["target"] == world["org_id"]
        assert rows[0]["actor_id"] == carol["id"]
        assert rows[0]["outcome"] == AuditOutcome.DENIED.value

    # Unauthenticated → 401 (no token).
    assert api.get(f"{base}/plan").status_code == 401
    assert api.get(f"{base}/usage").status_code == 401

    # Members (owner too) can read.
    assert api.get(f"{base}/usage", headers=_auth(alice["token"])).status_code == 200


@needs_postgres
def test_usage_endpoint_reflects_live_metering(api: TestClient, world: dict) -> None:
    this_month, last_month = _month_anchors()
    _seed_runs(api, world["project_id"], 7, this_month)
    _seed_runs(api, world["project_id"], 3, last_month)  # must not count
    _seed_tokens(api, world["project_id"], 123_456, this_month)
    _seed_job(api, world["project_id"], JobStatus.PENDING)

    r = api.get(
        f"/api/v1/organizations/{world['org_id']}/usage",
        headers=_auth(world["users"]["bob"]["token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["month"] == repo_billing.month_label()
    assert body["runs"] == 7
    assert body["tokens"] == 123_456
    assert body["active_jobs"] == 1
    assert body["projects"] == 1


# --- plan assignment (owner only, closed set, audited) ---------------------------


@needs_postgres
def test_plan_assignment_owner_only_and_audited(api: TestClient, world: dict) -> None:
    org_url = f"/api/v1/organizations/{world['org_id']}"
    alice, bob, carol = world["users"]["alice"], world["users"]["bob"], world["users"]["carol"]

    # Member and outsider → 403 (RBAC first; plan untouched).
    for member in (bob, carol):
        r = api.patch(org_url, headers=_auth(member["token"]), json={"plan": "pro"})
        assert r.status_code == 403
    assert api.get(org_url + "/plan", headers=_auth(alice["token"])).json()["name"] == "free"
    assert _audit_rows(api, AuditAction.ORG_PLAN_UPDATED.value) == []
    # Owner → 200 + pro caps + exactly one audited success row.
    r = api.patch(org_url, headers=_auth(alice["token"]), json={"plan": "pro"})
    assert r.status_code == 200, r.text
    pro = repo_billing.PLAN_CATALOG["pro"]
    assert r.json() == {
        "name": "pro",
        "limits": {
            "max_projects": pro.max_projects,
            "runs_per_month": pro.runs_per_month,
            "tokens_per_month": pro.tokens_per_month,
            "concurrent_jobs": pro.concurrent_jobs,
        },
    }
    rows = _audit_rows(api, AuditAction.ORG_PLAN_UPDATED.value)
    assert len(rows) == 1
    assert rows[0]["actor_id"] == alice["id"]
    assert rows[0]["target"] == world["org_id"]
    assert rows[0]["outcome"] == AuditOutcome.SUCCESS.value
    assert rows[0]["ip"]  # client IP captured

    # No-op PATCH (same plan) → 200, still exactly one audit row.
    r = api.patch(org_url, headers=_auth(alice["token"]), json={"plan": "pro"})
    assert r.status_code == 200
    assert len(_audit_rows(api, AuditAction.ORG_PLAN_UPDATED.value)) == 1


@needs_postgres
def test_plan_assignment_rejects_out_of_catalog_values(api: TestClient, world: dict) -> None:
    org_url = f"/api/v1/organizations/{world['org_id']}"
    alice = world["users"]["alice"]

    for bad in ("enterprise-plus", "dev", "Free", 42, None):
        payload = {"plan": bad} if bad is not None else {}
        assert api.patch(org_url, headers=_auth(alice["token"]), json=payload).status_code == 422

    # Plan unchanged; nothing audited.
    assert api.get(org_url + "/plan", headers=_auth(alice["token"])).json()["name"] == "free"
    assert _audit_rows(api, AuditAction.ORG_PLAN_UPDATED.value) == []

    # RBAC outranks body validation: a non-owner's invalid body is a 403.
    r = api.patch(
        org_url,
        headers=_auth(world["users"]["bob"]["token"]),
        json={"plan": "bogus"},
    )
    assert r.status_code == 403


@needs_postgres
def test_plan_upgrade_relaxes_quota_caps(api: TestClient, world: dict) -> None:
    org_id, project_id = world["org_id"], world["project_id"]
    alice = world["users"]["alice"]
    # free org at its concurrent cap (1 in-flight job) → dispatch is denied.
    _seed_job(api, project_id, JobStatus.RUNNING)
    body = {"project_id": project_id, "title": "login", "content": "a login form"}
    r = api.post("/api/v1/requirements/analyze", headers=_auth(alice["token"]), json=body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["limit"] == "concurrent_jobs"

    # Owner assigns pro (5 concurrent) → the same dispatch now goes through.
    r = api.patch(
        f"/api/v1/organizations/{org_id}",
        headers=_auth(alice["token"]),
        json={"plan": "pro"},
    )
    assert r.status_code == 200, r.text
    api.app.state.jobs_agent = StubAgent()  # hermetic agent for the allowed dispatch
    r = api.post("/api/v1/requirements/analyze", headers=_auth(alice["token"]), json=body)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    # The gate let the dispatch through: the job runs to completion. The
    # runner creates the ai_sessions audit anchor before calling the agent,
    # so one session row proves real AI work started.
    deadline = time.monotonic() + 5
    status = ""
    while time.monotonic() < deadline:
        status = _scalar(api, "SELECT status FROM jobs WHERE id=:j", {"j": job_id}) or ""
        if status in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.1)
    assert status == "completed", f"job ended {status!r}"
    assert _scalar(api, "SELECT count(*) FROM ai_sessions") == 1


# --- dispatch gate: 409 plan_limit body + audit + no partial state ----------------


def _assert_plan_limit_body(
    api: TestClient,
    world: dict,
    r,
    expected_limit: str,
    expected_used: int,
    sessions_before: int = 0,
) -> None:
    detail = r.json()["detail"]
    assert detail["code"] == "plan_limit"
    assert detail["plan"] == "free"
    assert detail["limit"] == expected_limit
    assert detail["used"] == expected_used
    assert detail["allowed"] == getattr(FREE, expected_limit)
    assert detail["org_id"] == world["org_id"]
    assert detail["project_id"] == world["project_id"]
    assert isinstance(detail["job_id"], str) and detail["job_id"]
    # No *new* AI work started (token-metering seeds may legitimately exist),
    # and the org got exactly one quota-denial audit row (actor + client IP
    # captured, outcome ``denied``).
    assert _scalar(api, "SELECT count(*) FROM ai_sessions") == sessions_before
    rows = _audit_rows(api, AuditAction.ORG_QUOTA_DENIED.value)
    assert len(rows) == 1
    assert rows[0]["target"] == world["org_id"]
    assert rows[0]["actor_id"] == world["users"]["alice"]["id"]
    assert rows[0]["outcome"] == AuditOutcome.DENIED.value
    assert rows[0]["ip"]


@needs_postgres
def test_dispatch_rejected_by_concurrent_jobs(api: TestClient, world: dict) -> None:
    project_id = world["project_id"]
    _seed_job(api, project_id, JobStatus.RUNNING)
    jobs_before = _scalar(api, "SELECT count(*) FROM jobs WHERE project_id=:p", {"p": project_id})
    r = api.post(
        "/api/v1/requirements/analyze",
        headers=_auth(world["users"]["alice"]["token"]),
        json={"project_id": project_id, "title": "login", "content": "a login form"},
    )
    assert r.status_code == 409, r.text
    _assert_plan_limit_body(api, world, r, "concurrent_jobs", 1)
    # The rejected job row was removed — the count is back to the seed.
    jobs_after = _scalar(api, "SELECT count(*) FROM jobs WHERE project_id=:p", {"p": project_id})
    assert jobs_after == jobs_before


@needs_postgres
def test_dispatch_rejected_by_runs_per_month(api: TestClient, world: dict) -> None:
    this_month, _ = _month_anchors()
    _seed_runs(api, world["project_id"], FREE.runs_per_month, this_month)
    r = api.post(
        f"/api/v1/projects/{world['project_id']}/runs",
        headers=_auth(world["users"]["alice"]["token"]),
        json={"repository_path": "/tmp/repo", "tests": ["tests/test_login.py"]},
    )
    assert r.status_code == 409, r.text
    _assert_plan_limit_body(api, world, r, "runs_per_month", FREE.runs_per_month)


@needs_postgres
def test_dispatch_rejected_by_tokens_per_month(api: TestClient, world: dict) -> None:
    this_month, _ = _month_anchors()
    _seed_tokens(api, world["project_id"], FREE.tokens_per_month, this_month)
    sessions_before = _scalar(api, "SELECT count(*) FROM ai_sessions")
    r = api.post(
        "/api/v1/requirements/analyze",
        headers=_auth(world["users"]["alice"]["token"]),
        json={"project_id": world["project_id"], "title": "login", "content": "a login form"},
    )
    assert r.status_code == 409, r.text
    _assert_plan_limit_body(
        api, world, r, "tokens_per_month", FREE.tokens_per_month, sessions_before
    )
