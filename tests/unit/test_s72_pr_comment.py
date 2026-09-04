"""S7.2 PR regression comment endpoint + job tests (build bible §19 S7.2, §17).

The full contract, end to end, against a scratch Postgres DB and an
in-process fake GitHub (no network, no model):

- ``POST /projects/{id}/regression/pr-comment`` is **owner-only** (§21:
  member / viewer / non-member / unknown-project all 403, never 404) and
  validates the body before any side effect (missing/blank
  ``repository_path``, missing ``pull_request``, bad owner/repo pattern,
  ``number < 1`` → 422);
- GitHub must be configured (S6.4 ``integrations`` row, enabled, with a
  ``token_ref`` whose secret is in the environment) or the route answers
  **409** with a token-free detail (never the secret itself);
- a valid request answers **202** + ``Location`` and creates a
  ``regression_pr_comment`` job whose input is the PR ref plus the local
  checkout path;
- the job is **LLM-free and deterministic**: fetch PR changed files →
  §5.5 impact set → §9 recommendation → upsert the marker comment. The
  fake GitHub stands in as the server for the real
  :func:`upsert_regression_comment` flow: create when the marker comment
  is absent, update in place when stale, no-op when the body is already
  current; human comments are never touched;
- the SSE contract carries a ``regression.comment`` event with
  ``action`` / ``comment_id`` / ``html_url`` / ``owner`` / ``repo`` /
  ``number``;
- the secret (a sentinel PAT in the environment) never appears in a
  response body, an SSE payload, a job row, or the persisted integration
  config — only in the ``Authorization`` header of the client it is
  wired into.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth, jobs
from qa_copilot_api.config import Settings
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import JobStatus, JobType, ProjectRole
from qa_copilot_integrations.github import (
    MARKER,
    GitHubError,
    IssueComment,
    PullRequestInfo,
)
from qa_copilot_repository import db, models
from sqlalchemy import create_engine, select, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
TEST_DB_PREFIX = "qa_copilot_s72"

SECRET = "test-secret-0123456789abcdef"
PASSWORD = "correct-horse-battery-staple"

NS = NAMESPACE_DNS
ORG_ID = str(uuid5(NS, "org-acme"))
ACME_ID = str(uuid5(NS, "acme-store"))
GHOST_ID = str(uuid5(NS, "ghost-project"))
ALICE_ID = str(uuid5(NS, "user-alice"))  # owner of Acme Store
BOB_ID = str(uuid5(NS, "user-bob"))  # member
CAROL_ID = str(uuid5(NS, "user-carol"))  # viewer
DAVE_ID = str(uuid5(NS, "user-dave"))  # non-member

EMAILS = {
    "alice": f"alice@{ORG_ID}.example.test",
    "bob": f"bob@{ORG_ID}.example.test",
    "carol": f"carol@{ORG_ID}.example.test",
    "dave": f"dave@{ORG_ID}.example.test",
}
USER_IDS = {name: str(uuid5(NS, f"user-{name}")) for name in EMAILS}

TEST_FILE_REL = "tests/test_app.py"
PR_OWNER = "acme"
PR_REPO = "web"
PR_NUMBER = 7
PR_URL = f"https://github.com/{PR_OWNER}/{PR_REPO}/pull/{PR_NUMBER}"
STABLE_OUTPUT_REF = f"regression-comment://{PR_OWNER}/{PR_REPO}/pull/{PR_NUMBER}"

TOKEN_REF = "GITHUB_PAT"
SENTINEL_PAT = "ghp_S72PrCommentSentinel0123"  # must never leak anywhere

ROUTE = f"/api/v1/projects/{ACME_ID}/regression/pr-comment"

# --- fakes & helpers -----------------------------------------------------------


class FakeGitHub:
    """In-process stand-in for the ``GitHubClient`` surface S7.2 uses.

    It plays the GitHub *server* behind the real :func:`upsert_regression_comment`
    flow: it records ``create`` / ``update`` calls and keeps its comment list
    current, so the marker-upsert branches (created / updated / unchanged)
    are exercised against the real product code.
    """

    def __init__(self, *, comments: tuple[IssueComment, ...] = ()) -> None:
        self.comments: list[IssueComment] = list(comments)
        self.created_bodies: list[str] = []
        self.updated: list[tuple[int, str]] = []
        self.fetch_calls: list[tuple[str, str, int]] = []
        self.closed = False
        self._next_id = 5000

    async def fetch_pull_request(self, owner: str, repo: str, number: int) -> PullRequestInfo:
        self.fetch_calls.append((owner, repo, number))
        assert (owner, repo, number) == (PR_OWNER, PR_REPO, PR_NUMBER)
        return PullRequestInfo(
            number=PR_NUMBER,
            title="Fix checkout total rounding",
            state="open",
            html_url=PR_URL,
            head_sha="e" * 40,
            head_ref="fix/checkout-total",
            base_sha="b" * 40,
            base_ref="main",
            changed_files=(TEST_FILE_REL,),
        )

    async def fetch_issue_comments(
        self, owner: str, repo: str, number: int
    ) -> tuple[IssueComment, ...]:
        return tuple(self.comments)

    async def create_issue_comment(
        self, owner: str, repo: str, number: int, body: str
    ) -> IssueComment:
        self._next_id += 1
        comment = IssueComment(
            id=self._next_id,
            user="qa-copilot",
            body=body,
            html_url=f"{PR_URL}#issuecomment-{self._next_id}",
            created_at="2026-01-01T00:00:00Z",
            updated_at=None,
        )
        self.comments.append(comment)
        self.created_bodies.append(body)
        return comment

    async def update_issue_comment(
        self, owner: str, repo: str, number: int, comment_id: int, body: str
    ) -> IssueComment:
        for index, comment in enumerate(self.comments):
            if comment.id == comment_id:
                self.comments[index] = replace(
                    comment, body=body, updated_at="2026-01-02T00:00:00Z"
                )
                self.updated.append((comment_id, body))
                return self.comments[index]
        raise AssertionError(f"no comment with id {comment_id}")

    async def aclose(self) -> None:
        self.closed = True


class FailingGitHub(FakeGitHub):
    """Simulates a hard GitHub failure (e.g. 404) mid-job."""

    async def fetch_pull_request(self, owner: str, repo: str, number: int) -> PullRequestInfo:
        raise GitHubError("GitHub API returned 404 (pull request not found)")


def _patch_build_client(monkeypatch: pytest.MonkeyPatch, fake: FakeGitHub) -> None:
    """Point the S7.2 agent at *fake* instead of the real GitHubClient."""

    def factory(engine: Any, project_id: str) -> Any:
        assert project_id == ACME_ID
        return fake

    monkeypatch.setattr(jobs, "build_github_client", factory)


def _admin(sql: str) -> None:
    engine = create_engine(ADMIN_URL)
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT")
        connection.execute(text(sql))
    engine.dispose()


def _drop_db(dbname: str) -> None:
    _admin(f'DROP DATABASE IF EXISTS "{dbname}"')


def _auth(user: str) -> dict[str, str]:
    """JWT bearer header (S0.8 ``auth.get_current_user`` verifies HS256 + expiry)."""
    token = auth.create_access_token(USER_IDS[user], EMAILS[user], SECRET)
    return {"Authorization": f"Bearer {token}"}


def _pull_request_ref() -> dict[str, Any]:
    return {"owner": PR_OWNER, "repo": PR_REPO, "number": PR_NUMBER}


def _body(repository_path: str) -> dict[str, Any]:
    return {
        "repository_path": repository_path,
        "pull_request": _pull_request_ref(),
        "top_n": 5,
    }


def _make_repo(root: Path) -> Path:
    """A small repo checkout for the S7.2 job to scan (S6.1-style layout)."""
    repo = root / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "src" / "app.py").write_text(
        "from .auth import require_login\n\n\n"
        "def checkout_total(items):\n"
        "    total = sum(i.price for i in items)\n"
        "    if require_login():\n"
        "        total *= 0.9\n"
        "    return total\n",
        encoding="utf-8",
    )
    (repo / "src" / "auth.py").write_text(
        "def require_login():\n    return True\n", encoding="utf-8"
    )
    (repo / "src" / "db.py").write_text("def connect():\n    return object()\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "from src.app import checkout_total\n\n\n"
        "def test_checkout_total():\n"
        "    assert checkout_total([]) == 0\n",
        encoding="utf-8",
    )
    return repo


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


# --- fixtures (scratch DB per test, S6.4-style) --------------------------------


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    dbname = f"{TEST_DB_PREFIX}_{os.getpid()}_{uuid4().hex[:8]}"
    url = f"postgresql+psycopg://qa:qa@localhost:5433/{dbname}"
    _admin(f'CREATE DATABASE "{dbname}"')
    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    engine = db.make_engine(url)
    command.upgrade(Config(str(ALEMBIC_INI)), "head")
    with db.make_session_factory(engine)() as session:
        for name in EMAILS:
            session.add(
                models.User(
                    id=USER_IDS[name],
                    email=EMAILS[name],
                    role="developer",
                    password_hash=auth.hash_password(PASSWORD),
                )
            )
        session.add(models.Organization(id=ORG_ID, name="Acme Inc"))
        session.add(models.Project(id=ACME_ID, organization_id=ORG_ID, name="Acme Store"))
        session.flush()
        session.add_all(
            [
                models.ProjectMember(project_id=ACME_ID, user_id=ALICE_ID, role=ProjectRole.OWNER),
                models.ProjectMember(project_id=ACME_ID, user_id=BOB_ID, role=ProjectRole.MEMBER),
                models.ProjectMember(project_id=ACME_ID, user_id=CAROL_ID, role=ProjectRole.VIEWER),
            ]
        )
        session.commit()
    app = create_app(
        # ``_env_file=None`` is pydantic-settings' private init kwarg (keep
        # tests from reading the dev .env) — mypy can't see it in the stubs.
        settings=Settings(  # type: ignore[call-arg]
            database_url=url,
            auth_token_secret=SECRET,
            job_tick_delay_s=0.01,
            llm_base_url=None,
            llm_model=None,
            _env_file=None,
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


def _wait_terminal(client: TestClient, email: str, job_id: str) -> dict[str, Any]:
    url = f"/api/v1/jobs/{job_id}"
    deadline = time.time() + 30
    last: dict[str, Any] = {}
    while time.time() < deadline:
        response = client.get(url, headers=_auth(email))
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in ("completed", "failed", "cancelled"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last}")


def _stream_events(client: TestClient, email: str, url: str) -> list[tuple[str, dict[str, Any]]]:
    """Read the SSE stream for *url* until it ends; return ``(event, data)`` pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    with client.stream("GET", url, headers=_auth(email)) as response:
        assert response.status_code == 200
        event_name = "message"
        for line in response.iter_lines():
            if not line:
                event_name = "message"
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((event_name, json.loads(line.split(":", 1)[1].strip())))
    return events


def _drive_pr_comment_agent(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    fake: FakeGitHub,
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Run the S7.2 agent directly (no HTTP, no runner) against *fake*.

    Returns ``(output_ref, events)`` — the events exactly as the agent
    emitted them (no runner wrapping).
    """
    _patch_build_client(monkeypatch, fake)
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, data: dict[str, Any]) -> None:
        captured.append((event, dict(data)))

    ctx = jobs.JobContext(
        job_id=str(uuid4()),
        project_id=ACME_ID,
        job_type=JobType.REGRESSION_PR_COMMENT,
        input={
            # RegressionPrCommentJobAgent reads the PR ref as one nested dict
            # (owner / repo / number) plus the local checkout path.
            "pull_request": {"owner": PR_OWNER, "repo": PR_REPO, "number": PR_NUMBER},
            "repository_path": str(repo),
        },
        emit=_emit,
    )

    async def _go() -> str:
        return await jobs.RegressionPrCommentJobAgent(env["engine"]).run(ctx)

    return asyncio.run(_go()), captured


# --- auth & RBAC (§21: owner-only) ----------------------------------------------


def test_pr_comment_requires_authentication(client: TestClient) -> None:
    response = client.post(ROUTE, json=_body("/srv/checkout"))
    assert response.status_code == 401


def test_pr_comment_denies_member_viewer_and_non_member(client: TestClient) -> None:
    for email in ("bob", "carol", "dave"):
        response = client.post(ROUTE, json=_body("/srv/checkout"), headers=_auth(email))
        assert response.status_code == 403, (email, response.text)


def test_pr_comment_unknown_project_is_forbidden_not_not_found(client: TestClient) -> None:
    ghost_route = f"/api/v1/projects/{GHOST_ID}/regression/pr-comment"
    response = client.post(ghost_route, json=_body("/srv/checkout"), headers=_auth("alice"))
    assert response.status_code == 403


# --- request validation (422 before any side effect) ----------------------------


def test_pr_comment_rejects_invalid_repository_path(client: TestClient) -> None:
    bad_bodies = [
        _pull_request_ref(),  # repository_path missing entirely
        {"repository_path": "", "pull_request": _pull_request_ref()},  # empty (min_length=1)
    ]
    for bad in bad_bodies:
        response = client.post(ROUTE, json=bad, headers=_auth("alice"))
        assert response.status_code == 422, (bad, response.text)


def test_pr_comment_rejects_invalid_pull_request_ref(client: TestClient) -> None:
    bad_refs = [
        {"owner": "ac me", "repo": PR_REPO, "number": PR_NUMBER},
        {"owner": PR_OWNER, "repo": "web/store", "number": PR_NUMBER},
        {"owner": PR_OWNER, "repo": PR_REPO, "number": 0},
    ]
    for ref in bad_refs:
        response = client.post(
            ROUTE,
            json={"repository_path": "/srv/checkout", "pull_request": ref},
            headers=_auth("alice"),
        )
        assert response.status_code == 422, (ref, response.text)


# --- GitHub configuration (409, token-free details) ------------------------------


def test_pr_comment_409_without_integration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(ROUTE, json=_body("/srv/checkout"), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "project has no GitHub integration configured"


def test_pr_comment_409_when_integration_disabled(
    client: TestClient,
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_github_config(env, enabled=False)
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(ROUTE, json=_body("/srv/checkout"), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    # ``github_integration_config`` reports disabled the same as unconfigured —
    # the operator never learns whether a disabled row exists at all.
    assert response.json()["detail"] == "project has no GitHub integration configured"


def test_pr_comment_409_without_token_ref(client: TestClient, env: dict[str, Any]) -> None:
    _set_github_config(env, token_ref=None)
    response = client.post(ROUTE, json=_body("/srv/checkout"), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "project's GitHub integration has no token_ref configured"


def test_pr_comment_409_when_secret_not_in_environment(
    client: TestClient,
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_github_config(env)
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(ROUTE, json=_body("/srv/checkout"), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert TOKEN_REF in detail
    assert "is not set in the environment" in detail


def test_pr_comment_409_response_never_leaks_the_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live secret in the environment must never appear in an error body.
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    response = client.post(ROUTE, json=_body("/srv/checkout"), headers=_auth("alice"))
    assert response.status_code == 409
    assert SENTINEL_PAT not in response.text


# --- 202 + job creation ---------------------------------------------------------


def test_pr_comment_202_creates_regression_pr_comment_job(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHub()
    _patch_build_client(monkeypatch, fake)
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "pending"
    job_id = body["job_id"]
    assert response.headers["location"] == f"/api/v1/jobs/{job_id}"

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job

    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.Job, job_id)
        assert row is not None
        assert row.type == JobType.REGRESSION_PR_COMMENT
        assert row.project_id == ACME_ID
        assert row.input_ref is not None
        payload = json.loads(row.input_ref)
        assert payload["pull_request"] == _pull_request_ref()
        assert payload["repository_path"] == str(repo)
        assert payload["top_n"] == 5

    # the job fetched exactly the PR from the request — nothing else
    assert fake.fetch_calls == [(PR_OWNER, PR_REPO, PR_NUMBER)]


# --- job end to end: upsert branches (created / updated / unchanged) ------------


def test_pr_comment_job_completes_and_creates_comment(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHub()
    _patch_build_client(monkeypatch, fake)
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job
    assert job["output_ref"] == STABLE_OUTPUT_REF

    assert fake.created_bodies and not fake.updated
    assert fake.closed  # client released even on the success path
    body = fake.created_bodies[0]
    assert body.startswith(MARKER)
    assert f"regression set for PR #{PR_NUMBER}" in body
    assert "Fix checkout total rounding" in body  # PR title from the fake
    assert TEST_FILE_REL in body  # the recommendation entry

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    comment = next(d for name, d in events if name == "regression.comment")
    assert comment["action"] == "created"
    assert comment["comment_id"] == fake.comments[0].id
    assert comment["html_url"] == fake.comments[0].html_url


def test_pr_comment_job_updates_stale_marker_comment_in_place(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = IssueComment(
        id=31,
        user="qa-copilot",
        body=MARKER + " (stale run)\nold recommendation",
        html_url=f"{PR_URL}#issuecomment-31",
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
    )
    fake = FakeGitHub(comments=(stale,))
    _patch_build_client(monkeypatch, fake)
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job

    # in-place update: no new comment, no duplicate
    assert fake.created_bodies == []
    assert [comment_id for comment_id, _ in fake.updated] == [31]
    new_body = fake.updated[0][1]
    assert new_body.startswith(MARKER)
    assert new_body != stale.body
    assert len(fake.comments) == 1

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    comment = next(d for name, d in events if name == "regression.comment")
    assert comment["action"] == "updated"
    assert comment["comment_id"] == 31


def test_pr_comment_job_noop_when_comment_is_current(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    # First run establishes the current comment body.
    first = FakeGitHub()
    _patch_build_client(monkeypatch, first)
    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    first_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", first_id)
    assert job["status"] == "completed", job
    current_body = first.created_bodies[0]

    # Second run against the same (deterministic) content: pure no-op.
    seeded = FakeGitHub(
        comments=(
            IssueComment(
                id=44,
                user="qa-copilot",
                body=current_body,
                html_url=f"{PR_URL}#issuecomment-44",
                created_at="2026-01-01T00:00:00Z",
                updated_at=None,
            ),
        )
    )
    _patch_build_client(monkeypatch, seeded)
    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    second_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", second_id)
    assert job["status"] == "completed", job

    assert seeded.created_bodies == []
    assert seeded.updated == []

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={second_id}")
    comment = next(d for name, d in events if name == "regression.comment")
    assert comment["action"] == "unchanged"
    assert comment["comment_id"] == 44


# --- SSE event shape -------------------------------------------------------------


def test_pr_comment_sse_carries_the_comment_event(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeGitHub()
    _patch_build_client(monkeypatch, fake)
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    names = [name for name, _ in events]
    assert "stage.started" in names
    assert "regression.comment" in names
    assert "stage.completed" in names
    assert "job.completed" in names

    comment = next(d for name, d in events if name == "regression.comment")
    assert set(comment) == {
        "job_id",
        "project_id",
        "action",
        "comment_id",
        "html_url",
        "owner",
        "repo",
        "number",
    }
    assert comment["job_id"] == job_id
    assert comment["project_id"] == ACME_ID
    assert comment["action"] == "created"
    assert comment["owner"] == PR_OWNER
    assert comment["repo"] == PR_REPO
    assert comment["number"] == PR_NUMBER
    assert comment["comment_id"] == fake.comments[0].id
    assert comment["html_url"] == fake.comments[0].html_url


# --- secret handling --------------------------------------------------------------


def test_build_github_client_wires_env_secret_into_headers(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    engine = env["engine"]

    with db.make_session_factory(engine)() as session:
        base_url, token = jobs.github_integration_config(session, ACME_ID)
    assert base_url is None  # default api.github.com
    assert token == SENTINEL_PAT

    github = jobs.build_github_client(engine, ACME_ID)
    try:
        assert github._client.headers["Authorization"] == f"Bearer {SENTINEL_PAT}"
        assert github._client.headers["User-Agent"] == "ai-qa-copilot/0.1"
    finally:
        asyncio.run(github.aclose())


def test_pr_comment_persisted_state_never_contains_secret(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build_client(monkeypatch, FakeGitHub())
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    assert SENTINEL_PAT not in response.text  # 202 body
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job
    assert SENTINEL_PAT not in json.dumps(job)  # job status body

    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.Job, job_id)
        assert row is not None
        assert SENTINEL_PAT not in json.dumps(
            {
                "input_ref": row.input_ref,
                "output_ref": row.output_ref,
                "error": row.error,
            },
            default=str,
        )
        config = session.scalar(
            select(models.IntegrationConfig).where(models.IntegrationConfig.project_id == ACME_ID)
        )
        assert config is not None
        assert config.token_ref == TOKEN_REF  # the NAME is persisted, never the value
        assert SENTINEL_PAT not in json.dumps(
            {"token_ref": config.token_ref, "base_url": config.base_url}, default=str
        )

    for _name, data in _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}"):
        assert SENTINEL_PAT not in json.dumps(data)  # every SSE payload


def test_pr_comment_job_makes_no_model_calls(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§31.1: the LLM gateway is off the S7.2 path (deterministic, LLM-free)."""
    _patch_build_client(monkeypatch, FakeGitHub())
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job

    with db.make_session_factory(env["engine"])() as session:
        sessions = list(
            session.scalars(select(models.AISession).where(models.AISession.project_id == ACME_ID))
        )
        assert [s.task_type for s in sessions] == ["regression_pr_comment"]
        actions = list(session.scalars(select(models.AIAction)))
    assert actions == []


def test_github_failure_fails_the_job_and_stays_token_free(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_build_client(monkeypatch, FailingGitHub())
    _set_github_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_PAT)
    repo = _make_repo(tmp_path)

    response = client.post(ROUTE, json=_body(str(repo)), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "failed", job
    assert "404" in (job["error"] or "")
    assert SENTINEL_PAT not in (job["error"] or "")

    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.Job, job_id)
        assert row is not None
        assert row.status == JobStatus.FAILED
        assert SENTINEL_PAT not in (row.error or "")

    names = [name for name, _ in _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")]
    assert "job.failed" in names


# --- agent-level: deterministic event contract ----------------------------------


def test_agent_event_sequence_is_deterministic(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeGitHub()
    repo = _make_repo(tmp_path)
    output_ref, events = _drive_pr_comment_agent(env, monkeypatch, repo, fake)

    assert [name for name, _ in events] == [
        "stage.started",
        "progress",
        "progress",
        "progress",
        "regression.comment",
        "progress",
        "stage.completed",
    ]
    assert output_ref == STABLE_OUTPUT_REF

    stage = next(d for name, d in events if name == "stage.started")
    assert stage == {"stage": "regression"}
    for name, data in events:
        if name == "progress":
            assert 0.0 <= float(data["value"]) <= 1.0

    comment = next(d for name, d in events if name == "regression.comment")
    assert comment["action"] == "created"
    assert comment["owner"] == PR_OWNER
    assert comment["repo"] == PR_REPO
    assert comment["number"] == PR_NUMBER
    assert isinstance(comment["comment_id"], int)
    assert comment["html_url"].startswith(PR_URL)

    body = fake.created_bodies[0]
    assert body.startswith(MARKER)
    assert f"regression set for PR #{PR_NUMBER}" in body
    assert "Fix checkout total rounding" in body  # PR title from the fake
    assert TEST_FILE_REL in body  # changed file flows into the recommendation
    assert fake.closed


def test_agent_skips_non_marker_comments(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    human = IssueComment(
        id=77,
        user="alice",
        body="LGTM, shipping it",
        html_url=f"{PR_URL}#issuecomment-77",
        created_at="2026-01-01T00:00:00Z",
        updated_at=None,
    )
    fake = FakeGitHub(comments=(human,))
    repo = _make_repo(tmp_path)
    _output_ref, events = _drive_pr_comment_agent(env, monkeypatch, repo, fake)

    comment = next(d for name, d in events if name == "regression.comment")
    assert comment["action"] == "created"
    assert comment["comment_id"] != 77
    # the human comment is never modified or deleted
    assert [c.body for c in fake.comments if c.id == 77] == ["LGTM, shipping it"]
