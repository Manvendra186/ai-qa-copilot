"""S0.9 async jobs API tests (build bible §11, §31.2).

Covers the mandatory ``202 + job_id`` pattern end-to-end:

- ``POST /api/v1/requirements/analyze`` → 202 + ``{job_id}`` + ``Location``
  (member+; 401 unauthenticated; 403 for viewers and non-members; 403 — not
  404 — for unknown projects: no existence leak)
- ``GET /api/v1/jobs/{id}`` → status/progress/result refs (viewer+; non-member
  403 with no job data in the body; 404 for unknown ids)
- ``GET /api/v1/events`` → SSE: full event sequence in order, exactly-once
  delivery for live *and* late (replay) subscribers, terminal close, 422
  without a scope, RBAC on job- and project-scoped streams
- failure path: agent raises → row failed + error, ``job.failed`` terminal
- reaper: a job left ``running`` by a crash → failed; SSE still closes with a
  synthesized terminal frame from the row snapshot (buffer never existed)
- audit anchor: one ``ai_sessions`` row per job (active → completed/failed)
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth
from qa_copilot_api.config import Settings
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import JobStatus, JobType, ProjectRole
from qa_copilot_repository import db, models
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: each test gets a UNIQUE db name (pid + random) so that
# concurrent pytest invocations can never race on DROP/CREATE of one shared
# database ("database is being accessed by other users").
TEST_DB_PREFIX = "qa_copilot_jobs"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"

# ids are Postgres UUIDs — deterministic values, stable across runs
NS = NAMESPACE_DNS
ORG_ID = str(uuid5(NS, "org-acme"))
ACME_ID = str(uuid5(NS, "acme-store"))
BETA_ID = str(uuid5(NS, "beta-app"))
ALICE_ID = str(uuid5(NS, "user-alice"))
CAROL_ID = str(uuid5(NS, "user-carol"))
DAVE_ID = str(uuid5(NS, "user-dave"))

EMAILS = {
    "alice": "alice@local.dev",  # owner of acme
    "carol": "carol@local.dev",  # viewer of acme
    "dave": "dave@local.dev",  # owner of beta, NOT a member of acme
}
USER_IDS = {"alice": ALICE_ID, "carol": CAROL_ID, "dave": DAVE_ID}

ANALYZE_BODY = {
    "project_id": ACME_ID,
    "title": "Login flow",
    "content": "Users can log in with email + password and are redirected to /home.",
    "acceptance_criteria": ["valid credentials -> 200", "invalid password -> 401"],
}

DESIGN_BODY = {
    "project_id": ACME_ID,
    "title": "Order history",
    "content": "Users can view their order history.",
    "acceptance_criteria": ["Orders are listed newest first", "Each order shows status"],
}

STAGES = (
    "requirement",
    "test_design",
    "automation",
    "execution",
    "failure_analysis",
    "fix",
)
TICKS = 4  # StubAgent progress ticks per stage (qa_copilot_api.jobs.StubAgent)


def _admin(sql: str) -> None:
    """Run DDL against the ``postgres`` maintenance database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _drop_db(dbname: str) -> None:
    """Defensively drop a scratch DB: terminate lingering sessions first.

    ``DROP DATABASE`` fails if *any* session is attached to the target db
    (e.g. pooled connections a crashed test never closed). Killing them from
    the ``postgres`` maintenance db makes the drop deterministic.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :d AND pid <> pg_backend_pid()"
                ),
                {"d": dbname},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
    finally:
        engine.dispose()


def _auth(user: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {auth.create_access_token(USER_IDS[user], EMAILS[user], SECRET)}"
    }


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    """Scratch Postgres DB + migrated schema + users/projects/roles + app."""
    import os

    dbname = f"{TEST_DB_PREFIX}_{os.getpid()}_{uuid4().hex[:8]}"
    url = f"postgresql+psycopg://qa:qa@localhost:5433/{dbname}"

    _admin(f'CREATE DATABASE "{dbname}"')

    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic env.py: env var wins
    engine = db.make_engine(url)
    command.upgrade(Config(str(ALEMBIC_INI)), "head")

    with db.make_session_factory(engine)() as session:
        for user in EMAILS:
            session.add(
                models.User(
                    id=USER_IDS[user],
                    email=EMAILS[user],
                    role="developer",
                    password_hash=auth.hash_password(PASSWORD),
                )
            )
        session.add(models.Organization(id=ORG_ID, name="Acme Inc"))
        session.add(models.Project(id=ACME_ID, organization_id=ORG_ID, name="Acme Store"))
        session.add(models.Project(id=BETA_ID, organization_id=ORG_ID, name="Beta App"))
        session.flush()
        session.add_all(
            [
                models.ProjectMember(project_id=ACME_ID, user_id=ALICE_ID, role=ProjectRole.OWNER),
                models.ProjectMember(project_id=ACME_ID, user_id=CAROL_ID, role=ProjectRole.VIEWER),
                models.ProjectMember(project_id=BETA_ID, user_id=DAVE_ID, role=ProjectRole.OWNER),
            ]
        )
        session.commit()

    app = create_app(
        settings=Settings(
            database_url=url,
            auth_token_secret=SECRET,
            job_tick_delay_s=0.01,  # fast stub pacing
            # S1.1: pin the stub agent explicitly. These tests assert the
            # S0.9 stub contract and must stay hermetic: init kwargs beat
            # process env vars (pydantic-settings), so a real LLM in the
            # environment (or the repo `.env` leaked into os.environ by
            # alembic's `_load_dotenv`) can never pull in the real agent.
            llm_base_url=None,
            llm_model=None,
            _env_file=None,  # type: ignore[call-arg]  # pydantic private kwarg; invisible to mypy
        )
    )

    yield {"app": app, "engine": engine, "dbname": dbname}

    # close pooled connections before DROP DATABASE, or Postgres refuses
    app.state.engine.dispose()
    engine.dispose()
    if saved_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = saved_url
    _drop_db(dbname)


@pytest.fixture()
def client(env: dict[str, Any]) -> Iterator[TestClient]:
    with TestClient(env["app"]) as test_client:
        yield test_client


def _analyze(client: TestClient, user: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = client.post("/api/v1/requirements/analyze", json=body or ANALYZE_BODY, headers=_auth(user))
    assert r.status_code == 202, r.text
    data: dict[str, Any] = r.json()
    return data


def _design(client: TestClient, user: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = client.post(
        "/api/v1/requirements/test-cases", json=body or DESIGN_BODY, headers=_auth(user)
    )
    assert r.status_code == 202, r.text
    data: dict[str, Any] = r.json()
    return data


def _wait_terminal(
    client: TestClient, user: str, job_id: str, timeout: float = 10.0
) -> dict[str, Any]:
    """Poll ``GET /jobs/{id}`` until the job reaches a terminal status."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/jobs/{job_id}", headers=_auth(user))
        assert r.status_code == 200
        last = r.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.02)
    raise AssertionError(f"job did not reach a terminal state within {timeout}s: {last}")


def _read_sse(
    client: TestClient, url: str, user: str, timeout: float = 20.0
) -> list[tuple[str, dict[str, Any]]]:
    """Consume an SSE stream until the *server* closes it (terminal event).

    Parses ``event:``/``data:`` frames (the S0.7 web contract) and skips
    keepalive comment frames (``: ...``).
    """
    events: list[tuple[str, dict[str, Any]]] = []
    name: str | None = None
    data: str | None = None
    with client.stream("GET", url, headers=_auth(user), timeout=timeout) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for raw in r.iter_lines():
            if not raw:
                if name is not None and data is not None:
                    events.append((name, json.loads(data)))
                name, data = None, None
                continue
            if raw.startswith(":"):
                continue  # keepalive comment frame
            if raw.startswith("event: "):
                name = raw.removeprefix("event: ")
            elif raw.startswith("data: "):
                data = raw.removeprefix("data: ")
    if name is not None and data is not None:
        events.append((name, json.loads(data)))
    return events


# --- 202 job creation + RBAC -----------------------------------------------------


def test_analyze_returns_202_job_and_location(client: TestClient) -> None:
    r = client.post("/api/v1/requirements/analyze", json=ANALYZE_BODY, headers=_auth("alice"))
    assert r.status_code == 202
    body = r.json()
    job_id = body["job_id"]
    assert job_id
    assert body["status"] == "pending"
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"  # §11

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed"
    assert job["type"] == "requirement_analysis"
    assert job["project_id"] == ACME_ID
    assert job["progress"] == 1.0
    assert job["output_ref"] == "stub-output/requirement_analysis"
    assert job["error"] is None
    assert job["created_at"] and job["started_at"] and job["completed_at"]


def test_analyze_requires_auth(client: TestClient) -> None:
    assert client.post("/api/v1/requirements/analyze", json=ANALYZE_BODY).status_code == 401
    assert client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000").status_code == 401
    assert client.get("/api/v1/events?job_id=x").status_code == 401


def test_analyze_requires_member_or_above(client: TestClient) -> None:
    # viewer may read jobs but not start work (§31.3)
    r = client.post("/api/v1/requirements/analyze", json=ANALYZE_BODY, headers=_auth("carol"))
    assert r.status_code == 403
    # non-member of the project
    assert (
        client.post(
            "/api/v1/requirements/analyze", json=ANALYZE_BODY, headers=_auth("dave")
        ).status_code
        == 403
    )
    # unknown project: 403, not 404 — no existence leak (§31.3)
    ghost = dict(ANALYZE_BODY, project_id=str(uuid5(NS, "ghost-project")))
    assert (
        client.post("/api/v1/requirements/analyze", json=ghost, headers=_auth("alice")).status_code
        == 403
    )


def test_analyze_on_own_project_ok(client: TestClient) -> None:
    body = _analyze(client, "dave", dict(ANALYZE_BODY, project_id=BETA_ID))
    _wait_terminal(client, "dave", body["job_id"])  # completes in beta too


def test_analyze_validation(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/requirements/analyze",
            json=dict(ANALYZE_BODY, title=""),
            headers=_auth("alice"),
        ).status_code
        == 422
    )


# --- S1.2 test_case_generation endpoint (same 202 + SSE contract) -------------


def test_design_returns_202_job_and_location(client: TestClient) -> None:
    r = client.post("/api/v1/requirements/test-cases", json=DESIGN_BODY, headers=_auth("alice"))
    assert r.status_code == 202
    body = r.json()
    job_id = body["job_id"]
    assert job_id
    assert body["status"] == "pending"
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"  # §11

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed"
    assert job["type"] == "test_case_generation"
    assert job["project_id"] == ACME_ID
    assert job["progress"] == 1.0
    assert job["output_ref"] == "stub-output/test_case_generation"
    assert job["error"] is None


def test_design_requires_auth_and_member_role(client: TestClient) -> None:
    assert client.post("/api/v1/requirements/test-cases", json=DESIGN_BODY).status_code == 401
    # viewer may read jobs but not start work (§31.3)
    assert (
        client.post(
            "/api/v1/requirements/test-cases", json=DESIGN_BODY, headers=_auth("carol")
        ).status_code
        == 403
    )
    # non-member of the project
    assert (
        client.post(
            "/api/v1/requirements/test-cases", json=DESIGN_BODY, headers=_auth("dave")
        ).status_code
        == 403
    )
    # unknown project: 403, not 404 — no existence leak (§31.3)
    ghost = dict(DESIGN_BODY, project_id=str(uuid5(NS, "ghost-project-design")))
    assert (
        client.post(
            "/api/v1/requirements/test-cases", json=ghost, headers=_auth("alice")
        ).status_code
        == 403
    )


def test_design_validation(client: TestClient) -> None:
    assert (
        client.post(
            "/api/v1/requirements/test-cases",
            json=dict(DESIGN_BODY, title=""),
            headers=_auth("alice"),
        ).status_code
        == 422
    )


def test_design_completed_job_leaves_ai_session_completed(
    env: dict[str, Any], client: TestClient
) -> None:
    job_id = _design(client, "alice")["job_id"]
    _wait_terminal(client, "alice", job_id)
    with db.make_session_factory(env["engine"])() as session:
        rows = session.scalars(select(models.AISession)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "completed"
    assert row.task_type == "test_case_generation"
    assert row.project_id == ACME_ID
    assert row.user_id == ALICE_ID  # the requesting user, for the audit trail


def test_get_job_viewer_ok_nonmember_403_no_leak(client: TestClient) -> None:
    job_id = _analyze(client, "alice")["job_id"]
    _wait_terminal(client, "alice", job_id)

    r = client.get(f"/api/v1/jobs/{job_id}", headers=_auth("carol"))  # viewer
    assert r.status_code == 200
    assert r.json()["id"] == job_id

    # non-member: 403 and none of the job's data in the body
    r = client.get(f"/api/v1/jobs/{job_id}", headers=_auth("dave"))
    assert r.status_code == 403
    assert "id" not in r.json()

    # unknown id: 404
    assert (
        client.get(f"/api/v1/jobs/{str(uuid5(NS, 'nope'))}", headers=_auth("alice")).status_code
        == 404
    )


# --- SSE: ordering, exactly-once, replay, terminal close ------------------------


def _expected_sequence() -> list[str]:
    """The S0.7 mock contract: started -> per stage (started, TICKSx progress, done)."""
    expected = ["job.started"]
    for _ in STAGES:
        expected += ["stage.started", *["progress"] * TICKS, "stage.completed"]
    expected += ["job.completed"]
    return expected


def test_sse_event_order_and_exact_once(client: TestClient) -> None:
    job_id = _analyze(client, "alice")["job_id"]
    # subscribe right after the 202: the job is already running (or done) —
    # replay + live delivery together must cover the full sequence exactly once
    events = _read_sse(client, f"/api/v1/events?job_id={job_id}", "alice")

    assert [name for name, _ in events] == _expected_sequence()
    started_stages = [d["stage"] for n, d in events if n == "stage.started"]
    assert started_stages == list(STAGES)
    completed_stages = [d["stage"] for n, d in events if n == "stage.completed"]
    assert completed_stages == list(STAGES)

    first_name, first = events[0]
    assert first_name == "job.started"
    assert first["job_id"] == job_id
    assert first["project_id"] == ACME_ID
    assert first["stages"] == list(STAGES)
    assert events[-1][0] == "job.completed"
    assert events[-1][1]["output_ref"] == "stub-output/requirement_analysis"


def test_sse_replay_for_late_subscriber(client: TestClient) -> None:
    job_id = _analyze(client, "alice")["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed"

    # connect *after* the job finished: the full sequence still arrives
    # (replay buffer), in order, exactly once, and the stream closes
    events = _read_sse(client, f"/api/v1/events?job_id={job_id}", "carol")  # viewer floor
    names = [name for name, _ in events]
    assert names == _expected_sequence()
    assert names.count("job.started") == 1
    assert names.count("job.completed") == 1


def test_progress_monotonic_and_final(client: TestClient) -> None:
    job_id = _analyze(client, "alice")["job_id"]
    seen: list[float] = []
    body: dict[str, Any] = {}
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}", headers=_auth("alice")).json()
        seen.append(body["progress"])
        if body["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert body["status"] == "completed"
    assert all(0.0 <= p <= 1.0 for p in seen)
    assert seen == sorted(seen)  # monotonic non-decreasing
    assert seen[-1] == 1.0


# --- failure path ----------------------------------------------------------------


class FailingAgent:
    """Dies mid-pipeline (S1.x stand-in: a real LLM timeout / crash)."""

    stages = ("requirement", "test_design")

    async def run(self, ctx: Any) -> str:
        await ctx.emit("stage.started", {"stage": "requirement"})
        raise RuntimeError("boom: local model unavailable")


def test_job_failure_path(client: TestClient, env: dict[str, Any]) -> None:
    env["app"].state.jobs_agent = FailingAgent()
    r = client.post("/api/v1/requirements/analyze", json=ANALYZE_BODY, headers=_auth("alice"))
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "failed"
    assert "boom" in (job["error"] or "")
    assert job["completed_at"] is not None

    events = _read_sse(client, f"/api/v1/events?job_id={job_id}", "alice")
    names = [name for name, _ in events]
    assert names[0] == "job.started"
    assert names[-1] == "job.failed"
    failed = dict(events)["job.failed"]
    assert failed["job_id"] == job_id
    assert "boom" in failed.get("error", "")


def test_failed_job_leaves_ai_session_failed(env: dict[str, Any], client: TestClient) -> None:
    env["app"].state.jobs_agent = FailingAgent()
    job_id = _analyze(client, "alice")["job_id"]
    _wait_terminal(client, "alice", job_id)
    with db.make_session_factory(env["engine"])() as session:
        rows = session.scalars(select(models.AISession)).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].task_type == "requirement_analysis"


# --- events endpoint: RBAC, project feed, audit anchor, reaper ------------------


def test_events_requires_scope_and_role(client: TestClient) -> None:
    # no scope at all
    assert client.get("/api/v1/events", headers=_auth("alice")).status_code == 422

    job_id = _analyze(client, "alice")["job_id"]
    # non-member of the job's project: 403, stream never starts
    assert client.get(f"/api/v1/events?job_id={job_id}", headers=_auth("dave")).status_code == 403
    # non-member of a project-scoped feed: 403
    assert (
        client.get(f"/api/v1/events?project_id={ACME_ID}", headers=_auth("dave")).status_code == 403
    )
    # unknown job id: 404
    assert (
        client.get(
            f"/api/v1/events?job_id={str(uuid5(NS, 'nope'))}", headers=_auth("alice")
        ).status_code
        == 404
    )


def test_project_feed_delivers_job_events(client: TestClient, env: dict[str, Any]) -> None:
    """The project feed delivers a completed job's events, in order, to a
    late subscriber (replay), ending on the terminal frame.

    Driven through :func:`qa_copilot_api.jobs.sse_stream` — the exact
    generator the route returns — rather than ``TestClient`` streaming: the
    project feed is intentionally *open-ended* (it stays open for the project's
    future jobs), and ``TestClient.handle_request`` blocks until the *server*
    ends the stream, so it can never return for an open-ended feed. RBAC on the
    project feed is covered by ``test_events_requires_scope_and_role``; this
    test verifies the feed actually delivers the job's events.
    """
    import asyncio

    from qa_copilot_api import jobs

    body = _analyze(client, "dave", dict(ANALYZE_BODY, project_id=BETA_ID))
    job_id = body["job_id"]
    _wait_terminal(client, "dave", job_id)  # job is terminal in the DB

    bus = env["app"].state.jobs_bus
    # ``job.completed`` is published just after the terminal DB commit — wait
    # until it lands in the replay buffer so the read below is deterministic.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(
            e.event in jobs.TERMINAL_EVENTS and e.data.get("job_id") == job_id
            for e in bus.snapshot_project(BETA_ID)
        ):
            break
        time.sleep(0.01)

    def _parse_frame(raw: str) -> tuple[str, dict[str, Any]] | None:
        if raw.startswith(":"):  # keepalive comment frame
            return None
        name: str | None = None
        data: str | None = None
        for line in raw.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = line.removeprefix("data: ")
        if name is not None and data is not None:
            return name, json.loads(data)
        return None

    async def _read_until_terminal() -> list[tuple[str, dict[str, Any]]]:
        events: list[tuple[str, dict[str, Any]]] = []
        gen = jobs.sse_stream(bus, project_id=BETA_ID)
        try:
            while True:
                try:
                    raw = await gen.__anext__()
                except StopAsyncIteration:
                    break
                parsed = _parse_frame(raw)
                if parsed is None:
                    # A keepalive (or any unparseable) frame means we fell
                    # into the *live* drain: the terminal event was missing
                    # from the replay. Fail fast instead of idling on the
                    # open-ended feed.
                    raise AssertionError(f"terminal event not in replay; got {raw!r}")
                events.append(parsed)
                if parsed[0] == "job.completed" and parsed[1].get("job_id") == job_id:
                    break  # terminal reached — do not fall into the live drain
        finally:
            await gen.aclose()
        return events

    events = asyncio.run(_read_until_terminal())

    names = [n for n, _ in events]
    assert "job.started" in names
    assert names[-1] == "job.completed"
    assert all(d["job_id"] == job_id for _, d in events if d.get("job_id"))


def test_completed_job_leaves_ai_session_completed(env: dict[str, Any], client: TestClient) -> None:
    job_id = _analyze(client, "alice")["job_id"]
    _wait_terminal(client, "alice", job_id)
    with db.make_session_factory(env["engine"])() as session:
        rows = session.scalars(select(models.AISession)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "completed"
    assert row.task_type == "requirement_analysis"
    assert row.project_id == ACME_ID
    assert row.user_id == ALICE_ID  # the requesting user, for the audit trail


def test_reap_orphans_and_synthesized_terminal_frame(
    env: dict[str, Any], client: TestClient
) -> None:
    # simulate a job left "running" when the process died (no events were ever
    # published for it, so there is no replay buffer)
    orphan_id = str(uuid5(NS, "orphan-job"))
    with db.make_session_factory(env["engine"])() as session:
        session.add(
            models.Job(
                id=orphan_id,
                project_id=ACME_ID,
                type=JobType.REQUIREMENT_ANALYSIS,
                status=JobStatus.RUNNING,
            )
        )
        session.commit()

    assert env["app"].state.jobs_runner.reap_orphans() == 1

    job = client.get(f"/api/v1/jobs/{orphan_id}", headers=_auth("alice")).json()
    assert job["status"] == "failed"
    assert "server restarted" in (job["error"] or "")

    # the SSE stream still terminates: the terminal frame is synthesized from
    # the job row snapshot (buffer evicted/never present)
    events = _read_sse(client, f"/api/v1/events?job_id={orphan_id}", "alice")
    assert [n for n, _ in events] == ["job.failed"]
    assert "server restarted" in events[0][1]["error"]
