"""S6.4 regression/analyze + "Run this set" API tests (§7, §11, §19 S6.4).

Covers the canonical contracts:

* ``POST /projects/{id}/regression/analyze`` → **202 + job_id** and the
  ``regression.set`` SSE event that carries the S6.1 change-impact set, the
  S6.2 risk ranking, the S6.3 top-N recommendation, and the optional S6.5
  advisor brief:

  - route:
    - 202 + ``job_id`` + ``Location`` (member+; 401 unauthenticated)
    - 403 viewer / non-member; 403 (not 404) for an unknown project — no
      existence leak (§31.3)
    - 422 for a blank / missing ``repository_path``; 422 when the change
      source is missing or doubled (``files`` vs ``base_ref``/``head_ref``)
    - S7.2: the ``pull_request`` change source — 422 when doubled with the
      other sources; 409 (token-free detail) when the S7.1 GitHub integration
      is missing; 202 → ``regression.set`` with the impact derived from the
      PR's changed files (resolved via the project's S7.1 integration)
  - ``regression.set`` event (delivered over SSE):
    - carries ``recommendation`` / ``impact`` / ``ranking`` / ``advice``; the
      ``advice`` degrades safely to the stub (``source="stub"``) when no LLM
      is configured, so a flaky model can never change *which* tests are
      re-run
  - job agent (``RegressionJobAgent``):
    - reads ``ctx.input`` (repository_path / files / top_n) and emits
      ``stage.started`` / ``progress`` / ``regression.set`` /
      ``stage.completed``; ``output_ref`` is a stable ``regression://<project>``
  - ``ai_actions`` audit (§19 S6.4: "``output_ref`` = stable
    ``regression://<project>`` ref → ``ai_actions`` audit row", §31.5):
    - stub path (no LLM configured): one row, ``agent="regression-advisor"``,
      ``model="stub"``, ``output_ref="regression://<project>"`` — the job's AI
      activity is audited even when the advisor degrades
    - LLM path (agent-level, fake gateway): one row carrying the model-call
      stats (§31.1) — ``model`` / ``tokens_in`` / ``tokens_out`` /
      ``input_hash``
    - contrast: the S3 "Run this set" job makes no model call → **no**
      ``ai_actions`` row (§31.1 "one row per model call")

* ``POST /projects/{id}/runs`` ("Run this set", §19 S6.4 exit criteria) →
  **202 + job_id** — a ``run_execution`` job that reuses the S3 execution
  path (``run_playwright`` + ``persist_run``), emitting ``run.result``:

  - route: 202 + ``job_id`` + ``Location``; 401 unauthenticated; 403
    viewer / non-member / unknown project; 422 blank path / empty ``tests``
  - ``run.result`` event (delivered over SSE):
    - carries ``run_id`` / ``status`` / ``totals`` (per-status counts); the
      run is persisted to the §10 ``test_runs`` rows (the S6.2 history feed)
  - job agent (``RunExecutionJobAgent``):
    - reads ``ctx.input`` (repository_path / tests / timeout_s), drives the
      Playwright worker (mocked here), persists the run, emits
      ``stage.started`` / ``progress`` / ``run.result`` / ``stage.completed``;
      ``output_ref`` is the persisted run id
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_ai import InMemoryPromptStore, LLMGateway, PromptSpec
from qa_copilot_api import auth, jobs
from qa_copilot_api.config import Settings
from qa_copilot_api.jobs import JobContext, RegressionJobAgent, RunExecutionJobAgent
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import JobType, ProjectRole, RunStatus, TestResultStatus
from qa_copilot_execution.report import RunReport, RunTotals, TestResultReport
from qa_copilot_integrations.github import PullRequestInfo
from qa_copilot_repository import db, models
from sqlalchemy import create_engine, func, select, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: a UNIQUE db name per test (pid + random) so concurrent
# pytest invocations never race on DROP/CREATE of one shared database.
TEST_DB_PREFIX = "qa_copilot_regression"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"

NS = NAMESPACE_DNS
ORG_ID = str(uuid5(NS, "org-acme"))
ACME_ID = str(uuid5(NS, "acme-store"))
ALICE_ID = str(uuid5(NS, "user-alice"))
CAROL_ID = str(uuid5(NS, "user-carol"))
DAVE_ID = str(uuid5(NS, "user-dave"))

EMAILS = {
    "alice": "alice@local.dev",  # owner of acme
    "carol": "carol@local.dev",  # viewer of acme
    "dave": "dave@local.dev",  # not a member of acme
}
USER_IDS = {"alice": ALICE_ID, "carol": CAROL_ID, "dave": DAVE_ID}

TEST_FILE_REL = "tests/test_app.py"
VALID_BODY = {"repository_path": "/tmp/qa-copilot-repo", "files": [TEST_FILE_REL]}
VALID_RUN_BODY = {"repository_path": "/tmp/qa-copilot-repo", "tests": [TEST_FILE_REL]}

# S7.2: the ``pull_request`` change source (§19 S7.2 "PR input → 202 →
# regression.set with PR-derived impact"), resolved via the S7.1 integration.
TOKEN_REF = "GITHUB_PAT"
SENTINEL_PAT = "ghp_S72AnalyzeSentinel0123"  # must never leak anywhere
PR_OWNER = "acme"
PR_REPO = "web"
PR_NUMBER = 7
PR_REF = {"owner": PR_OWNER, "repo": PR_REPO, "number": PR_NUMBER}
ANALYZE_ROUTE = f"/api/v1/projects/{ACME_ID}/regression/analyze"


def _admin(sql: str) -> None:
    """Run DDL against the ``postgres`` maintenance database."""
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _drop_db(dbname: str) -> None:
    """Defensively drop a scratch DB: terminate lingering sessions first."""
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


def _make_repo(base: Path) -> Path:
    """A minimal repo whose changed file is a test file (direct S6.1 impact)."""
    root = base / "repo"
    (root / "tests").mkdir(parents=True)
    (root / TEST_FILE_REL).write_text(
        "# test_app.py\n\ndef test_app() -> None:\n    assert True\n", encoding="utf-8"
    )
    return root


def _body(repo: Path) -> dict[str, Any]:
    return {"repository_path": str(repo), "files": [TEST_FILE_REL], "top_n": 10}


class FakePrGitHub:
    """In-process stand-in for the ``GitHubClient`` surface S7.2 analyze uses.

    ``fetch_pull_request`` returns the PR's changed files in exactly the S6.1
    ``files[]`` shape — the analyze request carries *no* ``files``, so the
    impact set can only be derived from what this fake "GitHub" returns.
    """

    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, str, int]] = []
        self.closed = False

    async def fetch_pull_request(self, owner: str, repo: str, number: int) -> PullRequestInfo:
        self.fetch_calls.append((owner, repo, number))
        return PullRequestInfo(
            number=PR_NUMBER,
            title="Fix checkout total rounding",
            state="open",
            html_url=f"https://github.com/{PR_OWNER}/{PR_REPO}/pull/{PR_NUMBER}",
            head_sha="e" * 40,
            head_ref="fix/checkout-total",
            base_sha="b" * 40,
            base_ref="main",
            changed_files=(TEST_FILE_REL,),
        )

    async def aclose(self) -> None:
        self.closed = True


def _set_github_config(
    env: dict[str, Any], *, enabled: bool = True, token_ref: str | None = TOKEN_REF
) -> None:
    """(Up)sert the S6.4 ``integrations`` row for Acme (S7.1/S7.2 contract)."""
    with db.make_session_factory(env["engine"])() as session:
        config = session.scalar(
            select(models.IntegrationConfig).where(models.IntegrationConfig.project_id == ACME_ID)
        )
        if config is None:
            config = models.IntegrationConfig(project_id=ACME_ID, provider="github")
            session.add(config)
        config.base_url = None
        config.token_ref = token_ref
        config.enabled = enabled
        session.commit()


def _patch_build_client(monkeypatch: pytest.MonkeyPatch, fake: FakePrGitHub) -> None:
    """Point the S7.2 agent at *fake* instead of the real GitHubClient."""

    def factory(engine: Any, project_id: str) -> Any:
        assert project_id == ACME_ID
        return fake

    monkeypatch.setattr(jobs, "build_github_client", factory)


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
        session.flush()
        session.add(
            models.ProjectMember(project_id=ACME_ID, user_id=ALICE_ID, role=ProjectRole.OWNER)
        )
        session.add(
            models.ProjectMember(project_id=ACME_ID, user_id=CAROL_ID, role=ProjectRole.VIEWER)
        )
        session.commit()

    app = create_app(
        settings=Settings(
            database_url=url,
            auth_token_secret=SECRET,
            job_tick_delay_s=0.01,  # fast pacing
            llm_base_url=None,  # no LLM → S6.5 advisor uses the stub summary
            llm_model=None,
            _env_file=None,  # type: ignore[call-arg]  # pydantic private kwarg
        )
    )

    yield {"app": app, "engine": engine, "dbname": dbname}

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


def _wait_terminal(
    client: TestClient, user: str, job_id: str, timeout: float = 10.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        r = client.get(f"/api/v1/jobs/{job_id}", headers=_auth(user))
        assert r.status_code == 200
        last = r.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal status: {last}")


def _stream_events(
    client: TestClient, user: str, url: str, timeout: float = 10.0
) -> list[tuple[str, dict[str, Any]]]:
    """Consume an SSE stream until the server closes it (terminal event)."""
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


# --- route: 202 + RBAC + validation -------------------------------------------


def test_regression_returns_202_job_and_location(client: TestClient, tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/regression/analyze",
        json=_body(repo),
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    job_id = body["job_id"]
    assert job_id
    assert body["status"] == "pending"
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"  # §11

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed"
    assert job["type"] == "regression_analysis"
    assert job["project_id"] == ACME_ID
    assert job["progress"] == 1.0
    # output_ref is the stable reference; the recommendation rides the SSE event.
    assert job["output_ref"] == f"regression://{ACME_ID}"
    assert job["error"] is None


def test_regression_requires_auth(client: TestClient) -> None:
    assert (
        client.post(f"/api/v1/projects/{ACME_ID}/regression/analyze", json=VALID_BODY).status_code
        == 401
    )


def test_regression_requires_member_or_above(client: TestClient) -> None:
    # viewer may not start work (§31.3)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/regression/analyze",
            json=VALID_BODY,
            headers=_auth("carol"),
        ).status_code
        == 403
    )
    # non-member of the project
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/regression/analyze",
            json=VALID_BODY,
            headers=_auth("dave"),
        ).status_code
        == 403
    )
    # unknown project: 403, not 404 — no existence leak (§31.3)
    ghost = str(uuid5(NS, "ghost-project"))
    assert (
        client.post(
            f"/api/v1/projects/{ghost}/regression/analyze",
            json=VALID_BODY,
            headers=_auth("alice"),
        ).status_code
        == 403
    )


def test_regression_validation(client: TestClient) -> None:
    # blank repository_path → 422 (min_length=1)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/regression/analyze",
            json={"repository_path": ""},
            headers=_auth("alice"),
        ).status_code
        == 422
    )
    # missing repository_path → 422 (required)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/regression/analyze",
            json={},
            headers=_auth("alice"),
        ).status_code
        == 422
    )
    # both change sources → 422 (exactly one of files / base_ref+head_ref)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/regression/analyze",
            json={
                "repository_path": "/tmp/qa-copilot-repo",
                "files": [TEST_FILE_REL],
                "base_ref": "main",
                "head_ref": "HEAD",
            },
            headers=_auth("alice"),
        ).status_code
        == 422
    )
    # no change source → 422
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/regression/analyze",
            json={"repository_path": "/tmp/qa-copilot-repo"},
            headers=_auth("alice"),
        ).status_code
        == 422
    )


# --- regression.set event over SSE ------------------------------------------


def test_regression_result_event_over_sse(client: TestClient, tmp_path: Path) -> None:
    """No-LLM app: the deterministic recommendation still honours the SSE contract."""
    repo = _make_repo(tmp_path)
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/regression/analyze",
        json=_body(repo),
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    names = [name for name, _ in events]
    assert "stage.started" in names
    assert "regression.set" in names
    assert "stage.completed" in names
    assert "job.completed" in names

    result = next(d for n, d in events if n == "regression.set")
    # The four S6.x sections are required; the SSE envelope also adds project_id/job_id.
    assert {"recommendation", "impact", "ranking", "advice"} <= set(result)
    # S6.1: the direct test-file change is impacted.
    assert any(i["path"] == TEST_FILE_REL for i in result["impact"]["impacted"])
    # S6.3: a non-empty top-N recommendation, ranked from 1.
    recs = result["recommendation"]["recommendations"]
    assert len(recs) >= 1
    assert [rec["rank"] for rec in recs] == list(range(1, len(recs) + 1))
    # S6.5: the advisor brief is present and sourced from the stub (no LLM).
    assert result["advice"]["source"] == "stub"
    assert result["advice"]["summary"]


# --- S7.2: the ``pull_request`` change source (§19 S7.2) ----------------------


def test_analyze_pull_request_rejects_doubled_sources(client: TestClient) -> None:
    """Exactly one of ``files`` / ``base_ref``+``head_ref`` / ``pull_request``."""
    base = {"repository_path": "/tmp/qa-copilot-repo"}
    doubled = [
        {**base, "files": [TEST_FILE_REL], "pull_request": PR_REF},
        {**base, "base_ref": "main", "head_ref": "HEAD", "pull_request": PR_REF},
    ]
    for body in doubled:
        response = client.post(ANALYZE_ROUTE, json=body, headers=_auth("alice"))
        assert response.status_code == 422, (body, response.text)


def test_analyze_pull_request_409_without_integration(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PR source fails fast (409, token-free) before any job is queued."""
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(
        ANALYZE_ROUTE,
        json={"repository_path": "/tmp/qa-copilot-repo", "pull_request": PR_REF},
        headers=_auth("alice"),
    )
    assert response.status_code == 409, response.text
    # §17: the detail names the missing config, never a secret.
    assert response.json()["detail"] == "project has no GitHub integration configured"
    # Fail-fast means no job row may have been created either.
    with db.make_session_factory(env["engine"])() as session:
        assert session.scalar(select(func.count()).select_from(models.Job)) == 0


def test_analyze_pull_request_202_and_pr_derived_regression_set(
    client: TestClient, env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR input → 202 → ``regression.set`` with PR-derived impact (§19 S7.2)."""
    repo = _make_repo(tmp_path)
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    fake = FakePrGitHub()
    _patch_build_client(monkeypatch, fake)

    r = client.post(
        ANALYZE_ROUTE,
        json={"repository_path": str(repo), "pull_request": PR_REF, "top_n": 10},
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    body = r.json()
    job_id = body["job_id"]
    assert body["status"] == "pending"
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job
    assert job["type"] == "regression_analysis"
    assert job["progress"] == 1.0
    assert job["output_ref"] == f"regression://{ACME_ID}"
    assert job["error"] is None

    # The PR was resolved through the project's S7.1 GitHub integration...
    assert fake.fetch_calls == [(PR_OWNER, PR_REPO, PR_NUMBER)]
    assert fake.closed
    # ...and never leaked: the sentinel PAT only existed in the environment.
    assert SENTINEL_PAT not in str(job)

    # The request carried *no* ``files`` — the impact set is derived from the
    # PR's changed files that the fake "GitHub" returned (S6.1 input).
    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    names = [name for name, _ in events]
    assert "stage.started" in names
    assert "regression.set" in names
    assert "stage.completed" in names
    assert "job.completed" in names

    result = next(d for n, d in events if n == "regression.set")
    assert {"recommendation", "impact", "ranking", "advice"} <= set(result)
    assert any(i["path"] == TEST_FILE_REL for i in result["impact"]["impacted"])
    recs = result["recommendation"]["recommendations"]
    assert len(recs) >= 1
    assert [rec["rank"] for rec in recs] == list(range(1, len(recs) + 1))
    assert result["advice"]["source"] == "stub"
    assert result["advice"]["summary"]


# --- job agent: event contract + stub advisor ---------------------------------


def _drive_agent(
    engine: Any,
    repository_path: str,
    files: list[str],
    *,
    ai_session_id: str | None = None,
    gateway: LLMGateway | None = None,
) -> tuple[str | None, list[tuple[str, dict[str, Any]]]]:
    """Run a :class:`RegressionJobAgent` and capture events.

    ``gateway=None`` (default) → the S6.5 advisor uses the deterministic stub
    summary; pass a fake :class:`LLMGateway` to exercise the LLM path (the
    ``ai_actions`` row then carries the model-call stats). ``ai_session_id``
    is the ``ai_sessions`` anchor the S6.4 audit row must link to.
    """
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, data: dict[str, Any]) -> None:
        captured.append((event, data))

    ctx = JobContext(
        job_id=str(uuid4()),
        project_id=ACME_ID,
        job_type=JobType.REGRESSION_ANALYSIS,
        input={
            "repository_path": repository_path,
            "files": files,
            "top_n": 10,
        },
        emit=_emit,
        ai_session_id=ai_session_id,
    )

    async def _go() -> str | None:
        store = InMemoryPromptStore([ADVISOR_PROMPT] if gateway is not None else [])
        return await RegressionJobAgent(store, gateway, engine).run(ctx)

    return asyncio.run(_go()), captured


def test_regression_agent_emits_result_and_stub_advice(env: dict[str, Any], tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    output_ref, events = _drive_agent(env["engine"], str(repo), [TEST_FILE_REL])

    names = [name for name, _ in events]
    assert names == [
        "stage.started",
        "progress",
        "progress",
        "progress",
        "progress",
        "regression.set",
        "stage.completed",
    ]

    result = next(d for n, d in events if n == "regression.set")
    assert set(result) == {"recommendation", "impact", "ranking", "advice"}
    assert result["recommendation"]["project_id"] == ACME_ID
    assert any(i["path"] == TEST_FILE_REL for i in result["impact"]["impacted"])
    assert len(result["recommendation"]["recommendations"]) >= 1
    assert result["advice"]["source"] == "stub"
    assert result["advice"]["summary"]

    # Stable reference — the full recommendation rides the SSE event, not this.
    assert output_ref == f"regression://{ACME_ID}"


# --- ai_actions audit (S6.4, §19 S6.4 / §31.5) ---------------------------------


def test_regression_analysis_writes_ai_actions_audit(
    client: TestClient, env: dict[str, Any], tmp_path: Path
) -> None:
    """§19 S6.4: the stable output_ref lands in an ``ai_actions`` audit row.

    No LLM is configured in this app, so the advisor degrades to the stub —
    the job's AI activity is still audited (``model="stub"``).
    """
    repo = _make_repo(tmp_path)
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/regression/analyze",
        json=_body(repo),
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, "alice", r.json()["job_id"])
    assert job["status"] == "completed"
    assert job["output_ref"] == f"regression://{ACME_ID}"

    engine = env["engine"]
    with db.make_session_factory(engine)() as session:
        sessions = session.scalars(
            select(models.AISession).where(models.AISession.project_id == ACME_ID)
        ).all()
        assert len(sessions) == 1  # the job's audit anchor (§31.5)
        assert sessions[0].task_type == "regression_analysis"
        actions = session.scalars(
            select(models.AIAction).where(models.AIAction.session_id == sessions[0].id)
        ).all()
    assert len(actions) == 1
    assert actions[0].agent == "regression-advisor"
    assert actions[0].model == "stub"
    assert actions[0].tokens_in == 0
    assert actions[0].tokens_out == 0
    assert actions[0].output_ref == f"regression://{ACME_ID}"


ADVISOR_PROMPT = PromptSpec(
    name="regression-advisor",
    version=1,
    body=(
        "Recommend the top regression tests.\n"
        "Changed: {{changed}}\n"
        "Ranked recommendations:\n"
        "{{recommendations}}\n"
        'Answer with one JSON object: {"summary": str, "focus": str|null}.'
    ),
    model_class="coder",
    input_budget=8000,
    output_budget=4096,
    schema_ref="regression-summary/v1",
    temperature=0.1,
)

VALID_SUMMARY = {
    "summary": "Run the highest-risk impacted test first.",
    "focus": TEST_FILE_REL,
}


def _assistant(payload: dict[str, Any]) -> dict[str, object]:
    """One OpenAI-style chat-completion response body."""
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 210},
    }


class _AsyncMockTransport(httpx.AsyncBaseTransport):
    """Async-transport shim so ``AsyncClient`` accepts a sync fake handler."""

    def __init__(self, handler: Any) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response: httpx.Response = self._handler(request)
        return response


def _fake_gateway(handler: Any) -> LLMGateway:
    return LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=0,
        transport=_AsyncMockTransport(handler),
    )


def test_regression_agent_audits_the_llm_call(env: dict[str, Any], tmp_path: Path) -> None:
    """LLM advisor path: the ``ai_actions`` row carries the model-call stats.

    §31.1: one row per model call — model, tokens in/out, latency, input hash.
    """
    engine = env["engine"]
    with db.make_session_factory(engine)() as session:
        anchor = models.AISession(
            project_id=ACME_ID, user_id=ALICE_ID, task_type="regression_analysis"
        )
        session.add(anchor)
        session.commit()
        anchor_id = anchor.id

    gateway = _fake_gateway(lambda request: httpx.Response(200, json=_assistant(VALID_SUMMARY)))
    try:
        output_ref, events = _drive_agent(
            engine,
            str(_make_repo(tmp_path)),
            [TEST_FILE_REL],
            ai_session_id=anchor_id,
            gateway=gateway,
        )
    finally:
        asyncio.run(gateway.aclose())

    result = next(d for n, d in events if n == "regression.set")
    assert result["advice"]["source"] == "llm"
    assert result["advice"]["summary"] == VALID_SUMMARY["summary"]
    assert output_ref == f"regression://{ACME_ID}"

    with db.make_session_factory(engine)() as session:
        actions = session.scalars(
            select(models.AIAction).where(models.AIAction.session_id == anchor_id)
        ).all()
    assert len(actions) == 1
    assert actions[0].agent == "regression-advisor"
    assert actions[0].model == "fake-model"
    assert actions[0].tokens_in == 40
    assert actions[0].tokens_out == 210
    assert actions[0].latency_ms >= 0
    assert actions[0].input_hash
    assert actions[0].output_ref == f"regression://{ACME_ID}"


# --- "Run this set": route + run.result event + agent (§19 S6.4) --------------


def _fake_report() -> RunReport:
    """A completed Playwright report: one pass, one fail (totals add up)."""
    return RunReport(
        schema_version=1,
        status=RunStatus.COMPLETED,
        target_dir="/tmp/qa-copilot-repo",
        started_at="2026-01-01T00:00:00.000+00:00",
        completed_at="2026-01-01T00:00:05.000+00:00",
        duration_ms=5000,
        totals=RunTotals(total=2, passed=1, failed=1, flaky=0, skipped=0),
        results=[
            TestResultReport(
                title="app loads",
                file=TEST_FILE_REL,
                status=TestResultStatus.PASSED,
                duration_ms=10,
                slug="app-loads",
            ),
            TestResultReport(
                title="checkout",
                file=TEST_FILE_REL,
                status=TestResultStatus.FAILED,
                duration_ms=20,
                error="boom",
                slug="checkout",
            ),
        ],
    )


def _mock_playwright(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the S3 worker in ``qa_copilot_api.jobs``; record its config."""
    calls: list[dict[str, Any]] = []

    def _fake(config: Any, run_id: str) -> RunReport:
        calls.append({"target_dir": config.target_dir, "test_filter": config.test_filter})
        return _fake_report()

    monkeypatch.setattr("qa_copilot_api.jobs.run_playwright", _fake)
    return calls


def test_run_returns_202_job_and_location(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The S3 execution path runs in-process (mocked worker) and persists."""
    calls = _mock_playwright(monkeypatch)

    r = client.post(
        f"/api/v1/projects/{ACME_ID}/runs",
        json=VALID_RUN_BODY,
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert job_id
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"  # §11

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed"
    assert job["type"] == "run_execution"
    assert job["project_id"] == ACME_ID
    assert job["progress"] == 1.0
    run_id = job["output_ref"]  # the persisted run id (S3.2 read path)
    assert run_id
    assert job["error"] is None

    # The worker ran the selected file in the repo (S3 path reuse).
    assert len(calls) == 1
    assert calls[0]["test_filter"] == TEST_FILE_REL

    # The run persisted onto the §10 rows (the S6.2 history feed).
    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.TestRun, run_id)
        assert row is not None
        assert row.status == RunStatus.COMPLETED
        results = session.scalars(
            select(models.TestResult).where(models.TestResult.run_id == run_id)
        ).all()
    assert len(results) == 2
    assert {r.status for r in results} == {TestResultStatus.PASSED, TestResultStatus.FAILED}


def test_run_requires_auth(client: TestClient) -> None:
    assert client.post(f"/api/v1/projects/{ACME_ID}/runs", json=VALID_RUN_BODY).status_code == 401


def test_run_requires_member_or_above(client: TestClient) -> None:
    # viewer may not start work (§31.3)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/runs",
            json=VALID_RUN_BODY,
            headers=_auth("carol"),
        ).status_code
        == 403
    )
    # non-member of the project
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/runs",
            json=VALID_RUN_BODY,
            headers=_auth("dave"),
        ).status_code
        == 403
    )
    # unknown project: 403, not 404 — no existence leak (§31.3)
    ghost = str(uuid5(NS, "ghost-project"))
    assert (
        client.post(
            f"/api/v1/projects/{ghost}/runs",
            json=VALID_RUN_BODY,
            headers=_auth("alice"),
        ).status_code
        == 403
    )


def test_run_validation(client: TestClient) -> None:
    # blank repository_path → 422 (min_length=1)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/runs",
            json={"repository_path": "", "tests": [TEST_FILE_REL]},
            headers=_auth("alice"),
        ).status_code
        == 422
    )
    # empty tests → 422 (min_length=1)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/runs",
            json={"repository_path": "/tmp/qa-copilot-repo", "tests": []},
            headers=_auth("alice"),
        ).status_code
        == 422
    )


def test_run_result_event_over_sse(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``run.result`` SSE event carries the persisted run id and totals."""
    _mock_playwright(monkeypatch)
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/runs",
        json=VALID_RUN_BODY,
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    names = [name for name, _ in events]
    assert "stage.started" in names
    assert "run.result" in names
    assert "stage.completed" in names
    assert "job.completed" in names

    result = next(d for n, d in events if n == "run.result")
    assert {"run_id", "status", "totals"} <= set(result)
    assert result["status"] == "completed"
    assert result["totals"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "flaky": 0,
        "skipped": 0,
    }
    # The run is persisted (the S6.2 history feed learns from this re-run).
    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.TestRun, result["run_id"])
        assert row is not None
        assert row.status == RunStatus.COMPLETED


def _drive_run_agent(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ai_session_id: str | None = None,
) -> tuple[str, list[tuple[str, dict[str, Any]]], list[dict[str, Any]]]:
    """Run a :class:`RunExecutionJobAgent` (worker mocked) and capture events.

    ``ai_session_id`` (optional) links the run to an ``ai_sessions`` anchor so
    tests can assert the job writes **no** ``ai_actions`` row (no model call).
    """
    calls = _mock_playwright(monkeypatch)
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, data: dict[str, Any]) -> None:
        captured.append((event, data))

    ctx = JobContext(
        job_id=str(uuid4()),
        project_id=ACME_ID,
        job_type=JobType.RUN_EXECUTION,
        input={
            "repository_path": "/tmp/qa-copilot-repo",
            "tests": [TEST_FILE_REL, "tests/test_cart.py"],
            "timeout_s": 300.0,
        },
        emit=_emit,
        ai_session_id=ai_session_id,
    )

    async def _go() -> str:
        return await RunExecutionJobAgent(engine).run(ctx)

    return asyncio.run(_go()), captured, calls


def test_run_execution_agent_emits_run_result_and_persists(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, events, calls = _drive_run_agent(env["engine"], monkeypatch)

    names = [name for name, _ in events]
    assert names == [
        "stage.started",
        "progress",
        "progress",
        "run.result",
        "stage.completed",
    ]

    result = next(d for n, d in events if n == "run.result")
    assert result["run_id"] == run_id
    assert result["status"] == "completed"
    assert result["totals"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "flaky": 0,
        "skipped": 0,
    }

    # A multi-file selection is an alternation of exact paths (Playwright
    # positional matching) — the S3 filter contract.
    assert calls[0]["test_filter"] == f"{TEST_FILE_REL}|tests/test_cart.py"

    # The run row + its per-test outcomes are persisted (the S6.2 history feed).
    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.TestRun, run_id)
        assert row is not None
        assert row.status == RunStatus.COMPLETED
        assert row.project_id == ACME_ID
        results = session.scalars(
            select(models.TestResult).where(models.TestResult.run_id == run_id)
        ).all()
    assert len(results) == 2


def test_run_execution_writes_no_ai_actions(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """§31.1 "one row per model call": the S3 execution path makes none.

    Contrast with the regression analysis job, whose §19 S6.4 exit criterion
    requires the ``ai_actions`` row even on the stub path.
    """
    engine = env["engine"]
    with db.make_session_factory(engine)() as session:
        anchor = models.AISession(project_id=ACME_ID, user_id=ALICE_ID, task_type="run_execution")
        session.add(anchor)
        session.commit()
        anchor_id = anchor.id

    _drive_run_agent(engine, monkeypatch, ai_session_id=anchor_id)

    with db.make_session_factory(engine)() as session:
        actions = session.scalars(
            select(models.AIAction).where(models.AIAction.session_id == anchor_id)
        ).all()
    assert actions == []
