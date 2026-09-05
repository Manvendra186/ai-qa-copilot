"""S7.3 inbound GitHub CI/CD webhook (build bible §19) unit tests.

Contract under test: ``POST /api/v1/webhooks/github`` — HMAC
``X-Hub-Signature-256`` (verified against the project's ``whsec_`` secret,
resolved from the env var named by ``integration_configs.token_ref`` and never
stored) authenticates the delivery; a ``pull_request`` ``opened``/``synchronize``
event maps ``repository.full_name`` → project → ``regression_analysis`` job
(202 + Location, reusing the S6.4 job path); and every delivery is recorded in
``webhook_events`` with a **unique** ``delivery_id`` so a re-sent delivery is
deduped (200) and never spawns a second job.

Auth is HMAC-only (no bearer token); the webhook secret must never leak into
any response body. LLM-free; the S6.4 ``RegressionJobAgent`` does the work.
"""

from __future__ import annotations

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
from qa_copilot_domain.enums import JobType, ProjectRole
from qa_copilot_integrations.github import PullRequestInfo
from qa_copilot_integrations.webhook import compute_github_signature, verify_github_signature
from qa_copilot_repository import db, models
from sqlalchemy import create_engine, func, select, text

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"

ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
TEST_DB_PREFIX = "qa_copilot_webhook"

SECRET = "test-secret-0123456789abcdef"  # auth token secret (>=16 chars)
PASSWORD = "correct-horse-battery-staple"

NS = NAMESPACE_DNS
ORG_ID = str(uuid5(NS, "org-acme"))
ACME_ID = str(uuid5(NS, "acme-store"))
ALICE_ID = str(uuid5(NS, "user-alice"))
REPO_ID = str(uuid5(NS, "repo-acme-web"))

# S7.3 webhook secret (GitHub "whsec_"-style): resolved from the env var named
# by token_ref; never persisted, never echoed into a response.
WEBHOOK_SECRET_REF = "S73_WEBHOOK_SECRET"
WEBHOOK_SECRET = "whsec_S73WebhookSentinel0123"

WEBHOOK_ROUTE = "/api/v1/webhooks/github"

# GitHub payload identity (acme/web #7) — matches the seeded repository URL.
PR_OWNER = "acme"
PR_REPO = "web"
PR_NUMBER = 7
FULL_NAME = f"{PR_OWNER}/{PR_REPO}"
REPO_URL = f"https://github.com/{FULL_NAME}"
TEST_FILE_REL = "tests/test_app.py"


def _admin(sql: str) -> None:
    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


def _drop_db(dbname: str) -> None:
    try:
        _admin(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{dbname}' AND pid <> pg_backend_pid();"
        )
        _admin(f'DROP DATABASE IF EXISTS "{dbname}"')
    except Exception as exc:  # pragma: no cover
        print(f"WARNING: could not drop db {dbname!r}: {exc}")


def _auth(_user: str = "alice") -> dict[str, str]:
    """JWT bearer header (S0.8 ``auth.get_current_user`` verifies HS256 + ``sub``)."""
    token = auth.create_access_token(ALICE_ID, "alice@local.dev", SECRET)
    return {"Authorization": f"Bearer {token}"}


def _make_repo(base: Path) -> Path:
    repo = base / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("def handler():\n    return ok()\n", encoding="utf-8")
    (repo / "src" / "ok.py").write_text("def ok():\n    return 42\n", encoding="utf-8")
    return repo


class FakePrGitHub:
    """Minimal GitHub PR stub (the S6.4 ``FakeGitHub`` PR subset)."""

    def __init__(self) -> None:
        self.fetch_calls: list[tuple[str, str, int]] = []
        self.closed = False

    async def fetch_pull_request(self, owner: str, repo: str, number: int) -> PullRequestInfo:
        self.fetch_calls.append((owner, repo, number))
        return PullRequestInfo(
            number=number,
            title="Fix checkout total rounding",
            state="open",
            html_url=f"https://github.com/{owner}/{repo}/pull/{number}",
            head_sha="e" * 40,
            head_ref="fix/checkout-total",
            base_sha="b" * 40,
            base_ref="main",
            changed_files=(TEST_FILE_REL,),
        )

    async def aclose(self) -> None:
        self.closed = True


def _patch_build_client(monkeypatch: pytest.MonkeyPatch, fake: FakePrGitHub) -> None:
    def fake_client(engine: Any, project_id: str) -> FakePrGitHub:  # noqa: ARG001
        return fake

    monkeypatch.setattr(jobs, "build_github_client", fake_client)


def _configure_project(
    env: dict[str, Any],
    *,
    repository_path: str | None,
    webhook_config: bool = True,
    link_repository: bool = True,
) -> None:
    """Seed Acme's S7.3 dependencies: repository link + settings + webhook secret."""
    with db.make_session_factory(env["engine"])() as session:
        project = session.get(models.Project, ACME_ID)
        if link_repository:
            session.add(
                models.Repository(
                    id=REPO_ID, provider="github", url=REPO_URL, default_branch="main"
                )
            )
            project.repository_id = REPO_ID
        project.settings = {"repository_path": repository_path} if repository_path else {}
        if webhook_config:
            session.add(
                models.IntegrationConfig(
                    project_id=ACME_ID,
                    provider="github_webhook",
                    token_ref=WEBHOOK_SECRET_REF,
                    enabled=True,
                )
            )
        session.commit()


def _payload(
    *,
    action: str | None,
    full_name: str = FULL_NAME,
    with_number: bool = True,
    with_pull_request: bool = True,
) -> dict[str, Any]:
    """Build a GitHub webhook payload (the exact shape the route consumes)."""
    out: dict[str, Any] = {
        "action": action,
        "repository": {
            "full_name": full_name,
            "name": PR_REPO,
            "owner": {"login": PR_OWNER},
        },
    }
    if with_pull_request:
        pr: dict[str, Any] = {
            "html_url": f"https://github.com/{full_name}/pull/{PR_NUMBER}",
            "title": "Fix checkout total rounding",
        }
        if with_number:
            pr["number"] = PR_NUMBER
        out["pull_request"] = pr
    return out


def _post(
    client: TestClient,
    payload: dict[str, Any],
    *,
    event: str,
    delivery_id: str,
    sign_with: str | None,
):
    """POST the exact payload bytes, signed with ``sign_with`` when given."""
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-github-event": event,
        "x-github-delivery": delivery_id,
    }
    if sign_with is not None:
        headers["x-hub-signature-256"] = compute_github_signature(sign_with, body)
    return client.post(WEBHOOK_ROUTE, content=body, headers=headers)


def _count(env: dict[str, Any], model: Any, *filters: Any) -> int:
    with db.make_session_factory(env["engine"])() as session:
        stmt = select(func.count()).select_from(model)
        for f in filters:
            stmt = stmt.where(f)
        return int(session.scalar(stmt) or 0)


def _get_job(env: dict[str, Any], job_id: str) -> Any:
    with db.make_session_factory(env["engine"])() as session:
        return session.get(models.Job, job_id)


def _get_webhook_event(env: dict[str, Any], delivery_id: str) -> Any:
    with db.make_session_factory(env["engine"])() as session:
        return session.scalar(
            select(models.WebhookEvent).where(models.WebhookEvent.delivery_id == delivery_id)
        )


def _wait_terminal(
    client: TestClient, user: str, job_id: str, timeout: float = 60.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}", headers=_auth(user)).json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach a terminal state in {timeout}s")


def _stream_events(
    client: TestClient, user: str, url: str, timeout: float = 30.0
) -> list[tuple[str, dict[str, Any]]]:
    """Consume an SSE stream until the server closes it (terminal event)."""
    events: list[tuple[str, dict[str, Any]]] = []
    name: str | None = None
    data: str | None = None
    with client.stream("GET", url, headers=_auth(user), timeout=timeout) as resp:
        assert resp.status_code == 200, resp.read()
        assert resp.headers["content-type"].startswith("text/event-stream")
        for raw in resp.iter_lines():
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


@pytest.fixture()
def env() -> Iterator[dict[str, Any]]:
    """Scratch Postgres DB + migrated schema + user/project + the app."""
    dbname = f"{TEST_DB_PREFIX}_{os.getpid()}_{uuid4().hex[:8]}"
    url = f"postgresql+psycopg://qa:qa@localhost:5433/{dbname}"

    _admin(f'CREATE DATABASE "{dbname}"')

    saved_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic env.py: env var wins over .env
    engine = db.make_engine(url)
    command.upgrade(Config(str(ALEMBIC_INI)), "head")

    with db.make_session_factory(engine)() as session:
        session.add(
            models.User(
                id=ALICE_ID,
                email="alice@local.dev",
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
        session.commit()

    app = create_app(
        settings=Settings(
            database_url=url,
            auth_token_secret=SECRET,
            job_tick_delay_s=0.01,
            llm_base_url=None,
            llm_model=None,
            _env_file=None,  # type: ignore[call-arg]
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
    with TestClient(env["app"]) as c:
        yield c


# ---------------------------------------------------------------------------
# pure signature-helper tests (no app)
# ---------------------------------------------------------------------------


def test_signature_helper_roundtrip() -> None:
    body = b'{"hello": "world"}'
    sig = compute_github_signature(WEBHOOK_SECRET, body)
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64  # 32-byte hex digest
    assert compute_github_signature(WEBHOOK_SECRET, body) == sig  # deterministic
    assert verify_github_signature(WEBHOOK_SECRET, body, sig) is True


def test_signature_helper_rejects_bad_inputs() -> None:
    body = b'{"hello": "world"}'
    sig = compute_github_signature(WEBHOOK_SECRET, body)
    assert verify_github_signature("not-the-secret", body, sig) is False
    assert verify_github_signature(WEBHOOK_SECRET, body, None) is False
    assert verify_github_signature("", body, sig) is False
    assert verify_github_signature(WEBHOOK_SECRET, body, "md5=" + sig[7:]) is False
    assert verify_github_signature(WEBHOOK_SECRET, body, "sha256=deadbeef") is False


# ---------------------------------------------------------------------------
# happy path: signed pull_request → regression_analysis job (reuses S6.4)
# ---------------------------------------------------------------------------


def test_signed_pull_request_opened_creates_and_completes_regression_job(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)
    fake = FakePrGitHub()
    _patch_build_client(monkeypatch, fake)

    delivery = str(uuid4())
    r = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=delivery,
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert r.json()["status"] == "pending"
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"

    # The delivery was recorded and linked to the spawned job.
    row = _get_webhook_event(env, delivery)
    assert row is not None
    assert (row.event, row.action) == ("pull_request", "opened")
    assert row.project_id == ACME_ID
    assert row.job_id == job_id

    # The job reuses the S6.4 regression_analysis path (no new JobType).
    job = _get_job(env, job_id)
    assert job is not None and job.type == JobType.REGRESSION_ANALYSIS
    assert job.project_id == ACME_ID
    input_ref = json.loads(job.input_ref)
    assert input_ref["pull_request"] == {
        "owner": PR_OWNER,
        "repo": PR_REPO,
        "number": PR_NUMBER,
    }
    assert input_ref["repository_path"] == str(repo)

    # The job runs to completion (PR resolved through the fake GitHub).
    done = _wait_terminal(client, "alice", job_id)
    assert done["status"] == "completed", done
    assert done["type"] == "regression_analysis"
    assert fake.fetch_calls == [(PR_OWNER, PR_REPO, PR_NUMBER)]
    assert fake.closed
    assert WEBHOOK_SECRET not in json.dumps(done)

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    assert any(n == "regression.set" for n, _ in events)
    result = next(d for n, d in events if n == "regression.set")
    assert any(i["path"] == TEST_FILE_REL for i in result["impact"]["impacted"])


def test_signed_pull_request_synchronize_creates_regression_job(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)
    _patch_build_client(monkeypatch, FakePrGitHub())

    delivery = str(uuid4())
    r = _post(
        client,
        _payload(action="synchronize"),
        event="pull_request",
        delivery_id=delivery,
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert _count(env, models.Job) == 1
    assert _count(env, models.WebhookEvent, models.WebhookEvent.job_id == job_id) == 1
    assert _get_job(env, job_id).type == JobType.REGRESSION_ANALYSIS


# ---------------------------------------------------------------------------
# auth failures (HMAC-only; secret must never leak)
# ---------------------------------------------------------------------------


def test_invalid_signature_rejected_401(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    r = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with="wrong-secret",
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "invalid signature"
    assert WEBHOOK_SECRET not in r.text  # the secret never leaks
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0


def test_missing_signature_rejected_401(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    r = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with=None,
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "invalid signature"
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0


def test_missing_webhook_secret_rejected_401(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))  # webhook config present
    monkeypatch.delenv(WEBHOOK_SECRET_REF, raising=False)  # but the env secret is unset

    r = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "invalid signature"
    assert WEBHOOK_SECRET not in r.text
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0


def test_missing_webhook_secret_no_config_401(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo), webhook_config=False)  # no config row

    r = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "invalid signature"
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0


# ---------------------------------------------------------------------------
# dedupe + ignored events
# ---------------------------------------------------------------------------


def test_duplicate_delivery_returns_200_and_no_second_job(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)
    _patch_build_client(monkeypatch, FakePrGitHub())

    delivery = str(uuid4())
    first = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=delivery,
        sign_with=WEBHOOK_SECRET,
    )
    assert first.status_code == 202, first.text
    first_job = first.json()["job_id"]

    # Re-send the SAME delivery id (GitHub retries) → 200 duplicate, no 2nd job.
    second = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=delivery,
        sign_with=WEBHOOK_SECRET,
    )
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["status"] == "duplicate"
    assert body2["delivery_id"] == delivery

    assert _count(env, models.Job) == 1
    assert _count(env, models.WebhookEvent) == 1
    assert _get_webhook_event(env, delivery).job_id == first_job


def test_unsupported_pull_request_action_ignored_200(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    delivery = str(uuid4())
    r = _post(
        client,
        _payload(action="closed"),  # not in {opened, synchronize}
        event="pull_request",
        delivery_id=delivery,
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ignored"
    assert (body["event"], body["action"]) == ("pull_request", "closed")
    assert body["delivery_id"] == delivery
    # Recorded for audit, but no job is spawned.
    assert _count(env, models.WebhookEvent) == 1
    assert _count(env, models.Job) == 0
    assert _get_webhook_event(env, delivery).job_id is None


def test_unsupported_event_ignored_200(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    delivery = str(uuid4())
    r = _post(
        client,
        _payload(action=None, with_pull_request=False),  # e.g. a push event
        event="push",
        delivery_id=delivery,
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ignored"
    assert body["event"] == "push"
    assert body["action"] is None
    assert _count(env, models.WebhookEvent) == 1
    assert _count(env, models.Job) == 0


# ---------------------------------------------------------------------------
# project binding + payload validation
# ---------------------------------------------------------------------------


def test_no_matching_project_409(
    client: TestClient,
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No repository is linked to any project → the payload's owner/repo can't bind.
    _configure_project(env, repository_path=None, link_repository=False, webhook_config=False)
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    r = _post(
        client,
        _payload(action="opened"),  # full_name acme/web matches no repository
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "no project matches GitHub repository acme/web"
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0


def test_missing_repository_path_409(
    client: TestClient,
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Repository linked + webhook secret set, but NO settings.repository_path.
    _configure_project(env, repository_path=None)
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    r = _post(
        client,
        _payload(action="opened"),
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "project has no repository_path configured (settings.repository_path)"
    )
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0


def test_missing_pull_request_number_400(
    client: TestClient,
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _make_repo(tmp_path)
    _configure_project(env, repository_path=str(repo))
    monkeypatch.setenv(WEBHOOK_SECRET_REF, WEBHOOK_SECRET)

    r = _post(
        client,
        _payload(action="opened", with_number=False),  # no pull_request.number
        event="pull_request",
        delivery_id=str(uuid4()),
        sign_with=WEBHOOK_SECRET,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "payload is missing pull_request.number"
    assert _count(env, models.Job) == 0
    assert _count(env, models.WebhookEvent) == 0
