"""S7.4 Jira failure-linking endpoint + job tests (build bible §19 S7.4, §17).

The full contract, end to end, against a scratch Postgres DB and an in-process
fake Jira (no network, no model):

- ``POST /projects/{id}/failures/{failure_id}/jira`` is **owner-only** (§31.3:
  member / viewer / non-member / unknown-project all 403, never 404) and
  validates the body before any side effect (missing/blank/invalid
  ``project_key`` → 422);
- Jira must be configured (S7.1 ``integrations`` row, provider ``jira``,
  enabled, with a ``token_ref`` whose secret is in the environment) or the
  route answers **409** with a token-free detail (never the secret itself);
- a valid request answers **202** + ``Location`` and creates a ``jira_link``
  job whose input is the ``failure_id`` plus the target ``project_key``;
- the job is **LLM-free and deterministic**: load the failure → build the
  deterministic ``fields`` payload → create-or-update the issue. The fake Jira
  stands in as the server for the real flow: create on first link, update in
  place on a re-link (never duplicates), and **recreate** on a stale key (404
  on update) and re-point the link (self-healing);
- on success ``failures.jira_issue_key`` is persisted (the idempotency anchor)
  and exposed on the failure read model;
- the SSE contract carries a ``jira.issue`` event with ``action`` / ``key`` /
  ``url`` / ``project_key``;
- the secret (a sentinel token in the environment) never appears in a response
  body, an SSE payload, or a job row — only in the ``Authorization`` header of
  the client it is wired into.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterator
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
from qa_copilot_domain.enums import (
    FailureCategory,
    JobStatus,
    JobType,
    ProjectRole,
    RunStatus,
    TestResultStatus,
    TestType,
)
from qa_copilot_integrations.jira import (
    JiraHTTPError,
    JiraIssue,
    JiraNotFoundError,
    build_issue_payload,
)
from qa_copilot_repository import db, models
from sqlalchemy import create_engine, select, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
TEST_DB_PREFIX = "qa_copilot_s74"

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

TOKEN_REF = "JIRA_API_TOKEN"
SENTINEL_TOKEN = "jira-S74JiraLinkSentinel0123"  # must never leak anywhere
PROJECT_KEY = "QA"
RUN_ID = str(uuid5(NS, "run-s74"))
TEST_CASE_ID = str(uuid5(NS, "test-case-s74"))
FAILURE_ID = str(uuid5(NS, "failure-s74"))
STALE_KEY = "QA-999"  # a key that will not exist in the fake → 404 on update

ROUTE = f"/api/v1/projects/{ACME_ID}/failures/{FAILURE_ID}/jira"


# --- fakes & helpers -----------------------------------------------------------


class FakeJira:
    """In-process stand-in for the ``JiraClient`` surface S7.4 uses.

    It plays the Jira *server* behind the real create-or-update flow: it
    records ``create_issue`` / ``update_issue`` calls and keeps its issue map
    current, so the idempotent branches (created / updated / recreated-on-stale)
    are exercised against the real product code. ``update_issue`` raises
    :class:`JiraNotFoundError` for a key it does not know (a 404), which is the
    stale-link trigger the agent must self-heal by recreating.
    """

    def __init__(self, *, seed: dict[str, dict[str, Any]] | None = None) -> None:
        self.issues: dict[str, dict[str, Any]] = dict(seed or {})
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self._next = 100

    def _url(self, key: str) -> str:
        return f"https://fake.atlassian.net/secure/IssueView.jspa#issue={key}"

    def _issue(self, key: str, fields: dict[str, Any], status: str) -> JiraIssue:
        return JiraIssue(
            key=key,
            id=str(self._next),
            summary=fields.get("summary"),
            status=status,
            url=self._url(key),
        )

    async def create_issue(self, fields: Any) -> JiraIssue:
        self._next += 1
        key = f"{PROJECT_KEY}-{self._next}"
        while key in self.issues:
            self._next += 1
            key = f"{PROJECT_KEY}-{self._next}"
        self.issues[key] = dict(fields)
        self.created.append(dict(fields))
        return self._issue(key, dict(fields), "To Do")

    async def update_issue(self, key: str, fields: Any) -> JiraIssue:
        if key not in self.issues:
            raise JiraNotFoundError(f"Jira API error 404 (no issue {key})")
        self.issues[key] = dict(fields)
        self.updated.append((key, dict(fields)))
        return self._issue(key, dict(fields), "In Progress")

    async def fetch_issue(self, key: str) -> JiraIssue:
        if key not in self.issues:
            raise JiraNotFoundError(f"Jira API error 404 (no issue {key})")
        return self._issue(key, self.issues[key], "To Do")

    async def aclose(self) -> None:
        self.closed = True


class FailingJira(FakeJira):
    """Simulates a hard Jira failure (e.g. 500) mid-job."""

    async def create_issue(self, fields: Any) -> JiraIssue:
        raise JiraHTTPError("Jira API error 500 (internal)", status=500)

    async def update_issue(self, key: str, fields: Any) -> JiraIssue:
        raise JiraHTTPError("Jira API error 500 (internal)", status=500)


def _patch_build_client(monkeypatch: pytest.MonkeyPatch, fake: FakeJira) -> None:
    """Point the S7.4 agent at *fake* instead of the real JiraClient."""

    def factory(engine: Any, project_id: str) -> Any:
        assert project_id == ACME_ID
        return fake

    monkeypatch.setattr(jobs, "build_jira_client", factory)


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


def _body(project_key: str) -> dict[str, Any]:
    return {"project_key": project_key}


def _set_jira_config(
    env: dict[str, Any], *, enabled: bool = True, token_ref: str | None = TOKEN_REF
) -> None:
    """(Up)sert the S7.1 ``jira`` integration row for Acme (S7.4 contract)."""
    with db.make_session_factory(env["engine"])() as session:
        config = session.scalar(
            select(models.IntegrationConfig).where(models.IntegrationConfig.project_id == ACME_ID)
        )
        if config is None:
            config = models.IntegrationConfig(project_id=ACME_ID, provider="jira")
            session.add(config)
        config.base_url = None
        config.token_ref = token_ref
        config.enabled = enabled
        session.commit()


def _make_failure(env: dict[str, Any]) -> None:
    """One run + one failed test result + its S4.1 diagnosis (the S7.4 input)."""
    with db.make_session_factory(env["engine"])() as session:
        session.add(models.TestRun(id=RUN_ID, project_id=ACME_ID, status=RunStatus.COMPLETED))
        session.flush()
        session.add(
            models.TestCase(
                id=TEST_CASE_ID,
                title="Checkout total calculation",
                type=TestType.FUNCTIONAL,
            )
        )
        session.flush()
        result_id = str(uuid4())
        session.add(
            models.TestResult(
                id=result_id,
                run_id=RUN_ID,
                test_case_id=TEST_CASE_ID,
                status=TestResultStatus.FAILED,
            )
        )
        session.flush()
        session.add(
            models.Failure(
                id=FAILURE_ID,
                test_result_id=result_id,
                category=FailureCategory.PRODUCT_DEFECT,
                root_cause="checkout_total ignores tax",
                confidence=0.9,
                evidence=["assert checkout_total([]) == 0", "expected 0.07, got 0.0"],
                suggested_fix="include tax in the total",
            )
        )
        session.commit()


def _set_jira_key(env: dict[str, Any], key: str | None) -> None:
    """Set ``failures.jira_issue_key`` (the idempotency anchor) to *key*."""
    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.Failure, FAILURE_ID)
        assert row is not None
        row.jira_issue_key = key
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


def _drive_jira_agent(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fake: FakeJira
) -> tuple[str, list[tuple[str, dict[str, Any]]]]:
    """Run the S7.4 agent directly (agent-level deterministic event contract)."""
    _patch_build_client(monkeypatch, fake)
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, data: dict[str, Any]) -> None:
        captured.append((event, dict(data)))

    ctx = jobs.JobContext(
        job_id=str(uuid4()),
        project_id=ACME_ID,
        job_type=JobType.JIRA_LINK,
        input={"failure_id": FAILURE_ID, "project_key": PROJECT_KEY},
        emit=_emit,
    )

    async def _go() -> str:
        return await jobs.JiraLinkJobAgent(env["engine"]).run(ctx)

    return asyncio.run(_go()), captured


# --- auth & RBAC (§31.3: owner-only) ------------------------------------------


def test_jira_link_requires_authentication(client: TestClient) -> None:
    response = client.post(ROUTE, json=_body(PROJECT_KEY))
    assert response.status_code == 401


def test_jira_link_denies_member_viewer_and_non_member(client: TestClient) -> None:
    for email in ("bob", "carol", "dave"):
        response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth(email))
        assert response.status_code == 403, (email, response.text)


def test_jira_link_unknown_project_is_forbidden_not_not_found(client: TestClient) -> None:
    ghost_route = f"/api/v1/projects/{GHOST_ID}/failures/{FAILURE_ID}/jira"
    response = client.post(ghost_route, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 403


# --- request validation (422 before any side effect) ----------------------------


def test_jira_link_rejects_invalid_project_key(client: TestClient) -> None:
    bad_bodies = [
        {},  # project_key missing entirely
        {"project_key": ""},  # empty (min_length=1)
        {"project_key": "a b"},  # space (bad pattern)
        {"project_key": "QA-123"},  # looks like an issue key, not a project key
        {"project_key": "1QA"},  # must start with a letter
    ]
    for bad in bad_bodies:
        response = client.post(ROUTE, json=bad, headers=_auth("alice"))
        assert response.status_code == 422, (bad, response.text)


# --- Jira configuration (409, token-free details) --------------------------------


def test_jira_link_409_without_integration(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_failure(env)
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "project has no Jira integration configured"


def test_jira_link_409_when_integration_disabled(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_failure(env)
    _set_jira_config(env, enabled=False)
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "project has no Jira integration configured"


def test_jira_link_409_without_token_ref(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_failure(env)
    _set_jira_config(env, token_ref=None)
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "project's Jira integration has no token_ref configured"


def test_jira_link_409_when_secret_not_in_environment(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.delenv(TOKEN_REF, raising=False)
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert TOKEN_REF in detail
    assert "is not set in the environment" in detail


def test_jira_link_409_response_never_leaks_the_secret(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live secret in the environment must never appear in an error body.
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)
    # The secret is set, so this is NOT a config error — force a missing
    # token_ref to reach the 409 path while the sentinel is live in the env.
    _set_jira_config(env, token_ref=None)
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 409
    assert SENTINEL_TOKEN not in response.text


# --- failure scoping (404) -------------------------------------------------------


def test_jira_link_404_when_failure_is_not_in_project(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)
    # No failure exists in this project -> 404 (not 403, no existence leak).
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 404, response.text


# --- 202 + job creation ---------------------------------------------------------


def test_jira_link_202_creates_jira_link_job(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeJira()
    _patch_build_client(monkeypatch, fake)
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)

    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
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
        assert row.type == JobType.JIRA_LINK
        assert row.project_id == ACME_ID
        assert row.input_ref is not None
        payload = json.loads(row.input_ref)
        assert payload["failure_id"] == FAILURE_ID
        assert payload["project_key"] == PROJECT_KEY


# --- job end to end: create branch ----------------------------------------------


def test_jira_link_job_completes_and_creates_issue(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeJira()
    _patch_build_client(monkeypatch, fake)
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)

    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job
    assert job["output_ref"].startswith("jira-issue://QA/QA-")

    # created exactly one issue; no update; the client was released (aclose).
    assert len(fake.created) == 1
    assert not fake.updated
    assert fake.closed
    new_key = list(fake.issues)[0]
    # the deterministic payload: project key + a [QA] summary.
    assert fake.created[0]["project"]["key"] == PROJECT_KEY
    assert fake.created[0]["summary"].startswith("[QA] product_defect")

    # failures.jira_issue_key persisted (the idempotency anchor).
    with db.make_session_factory(env["engine"])() as session:
        failure = session.get(models.Failure, FAILURE_ID)
        assert failure is not None
        assert failure.jira_issue_key == new_key

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    issue = next(d for name, d in events if name == "jira.issue")
    assert issue["action"] == "created"
    assert issue["key"] == new_key
    assert issue["project_key"] == PROJECT_KEY
    assert issue["url"].startswith("https://fake.atlassian.net")


def _run_link(client: TestClient) -> str:
    """Drive one successful link to completion; return the created issue key."""
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job = _wait_terminal(client, "alice", response.json()["job_id"])
    assert job["status"] == "completed", job
    # output_ref is ``jira-issue://<project_key>/<key>`` — the link is the key.
    return str(job["output_ref"]).split("jira-issue://", 1)[1].split("/", 1)[1]


# --- job end to end: update branch ----------------------------------------------


def test_jira_link_job_updates_existing_issue_in_place(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeJira()
    _patch_build_client(monkeypatch, fake)
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)
    # First link creates + persists the issue key.
    first_key = _run_link(client)

    # Re-link with the stored key present -> update in place, no new issue.
    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job

    assert len(fake.created) == 1  # still only the original
    assert len(fake.updated) == 1  # the re-link updated it
    assert fake.updated[0][0] == first_key

    with db.make_session_factory(env["engine"])() as session:
        failure = session.get(models.Failure, FAILURE_ID)
        assert failure is not None
        assert failure.jira_issue_key == first_key  # unchanged (no duplicate)

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    issue = next(d for name, d in events if name == "jira.issue")
    assert issue["action"] == "updated"
    assert issue["key"] == first_key


# --- job end to end: stale-key recreate branch ----------------------------------


def test_jira_link_job_recreates_stale_issue_and_repoints_link(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeJira()
    _patch_build_client(monkeypatch, fake)
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)
    # The stored key is stale: it does not exist in Jira -> update 404s.
    _set_jira_key(env, STALE_KEY)

    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job

    # no update succeeded (the stale key 404'd); exactly one recreate happened.
    assert not fake.updated
    assert len(fake.created) == 1
    new_key = list(fake.issues)[0]
    assert new_key != STALE_KEY

    # the link was re-pointed to the fresh issue (self-healing).
    with db.make_session_factory(env["engine"])() as session:
        failure = session.get(models.Failure, FAILURE_ID)
        assert failure is not None
        assert failure.jira_issue_key == new_key

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    issue = next(d for name, d in events if name == "jira.issue")
    assert issue["action"] == "recreated"
    assert issue["key"] == new_key


# --- job failure surfaces cleanly (token-free) ----------------------------------


def test_jira_link_failure_fails_the_job_and_stays_token_free(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_build_client(monkeypatch, FailingJira())
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)

    response = client.post(ROUTE, json=_body(PROJECT_KEY), headers=_auth("alice"))
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "failed", job
    assert "500" in (job["error"] or "")
    assert SENTINEL_TOKEN not in (job["error"] or "")

    with db.make_session_factory(env["engine"])() as session:
        row = session.get(models.Job, job_id)
        assert row is not None
        assert row.status == JobStatus.FAILED
        assert SENTINEL_TOKEN not in (row.error or "")
        # no partial link persisted on failure.
        failure = session.get(models.Failure, FAILURE_ID)
        assert failure is not None
        assert failure.jira_issue_key is None

    names = [name for name, _ in _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")]
    assert "job.failed" in names


# --- failure read model exposes jira_issue_key ----------------------------------


def test_failure_read_model_exposes_jira_issue_key(
    client: TestClient, env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeJira()
    _patch_build_client(monkeypatch, fake)
    _make_failure(env)
    _set_jira_config(env)
    monkeypatch.setenv(TOKEN_REF, SENTINEL_TOKEN)

    # Before any link: jira_issue_key is null on the read model.
    before = client.get(f"/api/v1/runs/{RUN_ID}/results", headers=_auth("alice"))
    assert before.status_code == 200, before.text
    assert before.json()[0]["failure"]["jira_issue_key"] is None

    # Link the failure -> the key is persisted and exposed.
    new_key = _run_link(client)

    after = client.get(f"/api/v1/runs/{RUN_ID}/results", headers=_auth("alice"))
    assert after.status_code == 200, after.text
    failure = after.json()[0]["failure"]
    assert failure["jira_issue_key"] == new_key
    assert failure["category"] == "product_defect"


# --- agent-level: deterministic event contract ----------------------------------


def test_agent_event_sequence_is_deterministic(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeJira()
    _make_failure(env)
    output_ref, events = _drive_jira_agent(env, monkeypatch, fake)

    assert [name for name, _ in events] == [
        "stage.started",
        "progress",
        "progress",
        "progress",
        "jira.issue",
        "stage.completed",
    ]
    assert output_ref.startswith("jira-issue://QA/QA-")

    stage = next(d for name, d in events if name == "stage.started")
    assert stage == {"stage": "jira_link"}
    for name, data in events:
        if name == "progress":
            assert 0.0 <= float(data["value"]) <= 1.0

    issue = next(d for name, d in events if name == "jira.issue")
    assert issue["action"] == "created"
    assert issue["project_key"] == PROJECT_KEY
    assert issue["key"] == list(fake.issues)[0]
    assert fake.closed

    # the payload is the deterministic S7.4 mapping (diagnosis + failure facts).
    payload = fake.created[0]
    expected = build_issue_payload(
        {
            "id": FAILURE_ID,
            "test_name": "Checkout total calculation",
            "run_id": RUN_ID,
            "status": "failed",
            "category": "product_defect",
            "confidence": 0.9,
            "root_cause": "checkout_total ignores tax",
            "evidence": ["assert checkout_total([]) == 0", "expected 0.07, got 0.0"],
            "suggested_fix": "include tax in the total",
        },
        PROJECT_KEY,
    )
    assert payload == expected


def test_agent_missing_integration_fails_cleanly(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_failure(env)
    monkeypatch.delenv(TOKEN_REF, raising=False)
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, data: dict[str, Any]) -> None:
        captured.append((event, dict(data)))

    ctx = jobs.JobContext(
        job_id=str(uuid4()),
        project_id=ACME_ID,
        job_type=JobType.JIRA_LINK,
        input={"failure_id": FAILURE_ID, "project_key": PROJECT_KEY},
        emit=_emit,
    )

    async def _go() -> None:
        await jobs.JiraLinkJobAgent(env["engine"]).run(ctx)

    with pytest.raises(jobs.JiraIntegrationNotConfiguredError):
        asyncio.run(_go())

    # no Jira issue was created and nothing was emitted as a jira.issue event.
    assert not [name for name, _ in captured if name == "jira.issue"]
