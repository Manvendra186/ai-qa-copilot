"""S3.2 run history / results / artifacts tests (build bible §10, §15, §31.3).

Read-only surface over persisted execution data:

- ``GET /api/v1/projects/{id}/runs`` — a project's runs, newest first
  (401 unauthenticated; viewer+; 403 non-member; 404 unknown project)
- ``GET /api/v1/runs/{id}`` — run + results + artifacts, with computed
  ``totals`` and ``duration_s`` (403 cross-project; 404 missing / malformed)
- ``GET /api/v1/runs/{id}/results`` — per-test outcomes + diagnosis + artifacts
- ``GET /api/v1/runs/{id}/artifacts`` — artifact rows
- ``GET /api/v1/runs/{id}/artifacts/{id}/content`` — streams the file bytes
  (404 when the store file is absent, e.g. a ``file://`` seed placeholder)
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
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
from qa_copilot_domain.enums import (
    ArtifactType,
    FailureCategory,
    ProjectRole,
    RunStatus,
    TestResultStatus,
    TestType,
)
from qa_copilot_repository import db, models

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: each test gets a UNIQUE db name (pid + random) so that
# concurrent pytest invocations can never race on DROP/CREATE of one shared
# database (same pattern as tests/unit/test_generated_tests.py).
TEST_DB_PREFIX = "qa_copilot_runs"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"

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
    """Defensively drop a scratch DB: terminate lingering sessions first."""
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
def env(tmp_path: Path) -> Iterator[dict[str, Any]]:
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

    # S3.2: the artifact store root is a temp dir so the download endpoint can
    # stream a real file back (and tests can delete it to exercise the 404 path).
    store_root = tmp_path / "artifacts"
    app = create_app(
        settings=Settings(
            database_url=url,
            auth_token_secret=SECRET,
            artifact_store_root=str(store_root),
            job_tick_delay_s=0.01,  # fast stub pacing
            llm_base_url=None,
            llm_model=None,
            _env_file=None,  # type: ignore[call-arg]  # pydantic private kwarg
        )
    )

    yield {"app": app, "engine": engine, "dbname": dbname, "store_root": store_root}

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


def _seed_run(
    env: dict[str, Any], *, project_id: str = ACME_ID, created: datetime | None = None
) -> str:
    """One completed run + a passed/failed result pair + failure + artifact.

    The artifact file is written under the configured store root so the
    download endpoint can stream it back. Returns the run's id.
    """
    created = created or datetime(2026, 1, 2, 9, 0, 0, tzinfo=UTC)
    completed = created + timedelta(minutes=3, seconds=12)
    with db.make_session_factory(env["engine"])() as session:
        case = models.TestCase(title="valid login succeeds", type=TestType.FUNCTIONAL)
        session.add(case)
        session.flush()
        run = models.TestRun(
            project_id=project_id,
            status=RunStatus.COMPLETED,
            commit_sha="0123456789abcdef0123456789abcdef01234567",
            started_at=created,
            completed_at=completed,
            created_at=created,
        )
        session.add(run)
        session.flush()
        session.add(
            models.TestResult(
                run_id=run.id,
                test_case_id=case.id,
                status=TestResultStatus.PASSED,
                duration=1.25,
            )
        )
        failed = models.TestResult(
            run_id=run.id,
            test_case_id=case.id,
            status=TestResultStatus.FAILED,
            duration=3.5,
        )
        session.add(failed)
        session.flush()
        session.add(
            models.Failure(
                test_result_id=failed.id,
                category=FailureCategory.PRODUCT_DEFECT,
                root_cause="the submit button is missing its click handler",
                confidence=0.82,
                evidence=["console error", "no /home navigation"],
                suggested_fix="wire the form onSubmit handler",
                needs_human_approval=True,
            )
        )
        uri = f"runs/{run.id}/{failed.id}/failure.png"
        artifact_path = Path(env["store_root"]) / uri
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes")
        session.add(
            models.Artifact(
                test_result_id=failed.id,
                type=ArtifactType.SCREENSHOT,
                uri=uri,
                metadata_={"size_bytes": artifact_path.stat().st_size},
            )
        )
        session.commit()
    return run.id


# --- authentication -----------------------------------------------------------


def test_runs_requires_auth(client: TestClient) -> None:
    """Unauthenticated → 401 on every S3.2 read endpoint."""
    assert client.get(f"/api/v1/projects/{ACME_ID}/runs").status_code == 401
    assert client.get("/api/v1/runs/00000000-0000-0000-0000-000000000000").status_code == 401


# --- list project runs ---------------------------------------------------------


def test_list_project_runs_newest_first(client: TestClient, env: dict[str, Any]) -> None:
    base = datetime(2026, 1, 2, 9, 0, 0, tzinfo=UTC)
    older = _seed_run(env, created=base)
    newer = _seed_run(env, created=base + timedelta(hours=1))
    body = client.get(f"/api/v1/projects/{ACME_ID}/runs", headers=_auth("alice")).json()
    assert [row["id"] for row in body] == [newer, older]
    row = body[0]
    assert row["project_id"] == ACME_ID
    assert row["status"] == "completed"
    assert row["commit_sha"] == "0123456789abcdef0123456789abcdef01234567"
    expected = {
        "id",
        "project_id",
        "commit_sha",
        "status",
        "started_at",
        "completed_at",
        "created_at",
    }
    assert set(row) >= expected


def test_list_project_runs_viewer(client: TestClient, env: dict[str, Any]) -> None:
    _seed_run(env)
    r = client.get(f"/api/v1/projects/{ACME_ID}/runs", headers=_auth("carol"))
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_list_project_runs_nonmember_403(client: TestClient, env: dict[str, Any]) -> None:
    """A project the caller does not belong to → 403 (never 404, §31.3)."""
    _seed_run(env)
    url = f"/api/v1/projects/{ACME_ID}/runs"
    assert client.get(url, headers=_auth("dave")).status_code == 403


def test_list_project_runs_unknown_project_404(client: TestClient) -> None:
    url = f"/api/v1/projects/{str(uuid4())}/runs"
    assert client.get(url, headers=_auth("alice")).status_code == 404


# --- run detail ----------------------------------------------------------------


def test_get_run_detail(client: TestClient, env: dict[str, Any]) -> None:
    run_id = _seed_run(env)
    r = client.get(f"/api/v1/runs/{run_id}", headers=_auth("alice"))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == run_id
    assert body["status"] == "completed"
    assert body["duration_s"] == pytest.approx(192.0)  # 3m 12s
    expected_totals = {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "flaky": 0,
        "skipped": 0,
        "pending": 0,
    }
    assert body["totals"] == expected_totals
    assert len(body["results"]) == 2
    failed = next(x for x in body["results"] if x["status"] == "failed")
    assert failed["failure"]["category"] == "product_defect"
    assert failed["failure"]["confidence"] == pytest.approx(0.82)
    assert failed["failure"]["needs_human_approval"] is True
    assert failed["artifacts"][0]["type"] == "screenshot"
    assert [a["id"] for a in body["artifacts"]] == [failed["artifacts"][0]["id"]]


def test_run_detail_viewer(client: TestClient, env: dict[str, Any]) -> None:
    run_id = _seed_run(env)
    assert client.get(f"/api/v1/runs/{run_id}", headers=_auth("carol")).status_code == 200


# --- results -------------------------------------------------------------------


def test_list_results(client: TestClient, env: dict[str, Any]) -> None:
    run_id = _seed_run(env)
    body = client.get(f"/api/v1/runs/{run_id}/results", headers=_auth("alice")).json()
    assert len(body) == 2
    assert {x["status"] for x in body} == {"passed", "failed"}
    passed = next(x for x in body if x["status"] == "passed")
    assert passed["failure"] is None
    assert passed["artifacts"] == []
    failed = next(x for x in body if x["status"] == "failed")
    assert failed["test_case_id"] is not None
    assert failed["duration"] == pytest.approx(3.5)
    assert failed["failure"] is not None
    assert failed["artifacts"][0]["download_url"].endswith("/content")


# --- artifacts -----------------------------------------------------------------


def test_list_artifacts(client: TestClient, env: dict[str, Any]) -> None:
    run_id = _seed_run(env)
    body = client.get(f"/api/v1/runs/{run_id}/artifacts", headers=_auth("alice")).json()
    assert len(body) == 1
    assert body[0]["type"] == "screenshot"
    assert body[0]["uri"].startswith(f"runs/{run_id}/")
    assert body[0]["metadata"]["size_bytes"] > 0


def test_download_artifact_content(client: TestClient, env: dict[str, Any]) -> None:
    run_id = _seed_run(env)
    body = client.get(f"/api/v1/runs/{run_id}", headers=_auth("alice")).json()
    artifact = body["artifacts"][0]
    r = client.get(artifact["download_url"], headers=_auth("alice"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content.startswith(b"\x89PNG")
    assert "failure.png" in r.headers.get("content-disposition", "")


def test_artifact_content_missing_file_404(client: TestClient, env: dict[str, Any]) -> None:
    """A row whose store file is gone (e.g. a ``file://`` seed) → 404, not 500."""
    run_id = _seed_run(env)
    body = client.get(f"/api/v1/runs/{run_id}", headers=_auth("alice")).json()
    artifact = body["artifacts"][0]
    (Path(env["store_root"]) / artifact["uri"]).unlink()
    r = client.get(artifact["download_url"], headers=_auth("alice"))
    assert r.status_code == 404


# --- RBAC + 404 matrix ---------------------------------------------------------


def test_cross_project_denied(client: TestClient, env: dict[str, Any]) -> None:
    """dave (owner of beta) must not read acme's run: 403 on every endpoint."""
    run_id = _seed_run(env, project_id=ACME_ID)
    assert client.get(f"/api/v1/runs/{run_id}", headers=_auth("dave")).status_code == 403
    url = f"/api/v1/runs/{run_id}/results"
    assert client.get(url, headers=_auth("dave")).status_code == 403
    url = f"/api/v1/runs/{run_id}/artifacts"
    assert client.get(url, headers=_auth("dave")).status_code == 403


def test_missing_run_404(client: TestClient, env: dict[str, Any]) -> None:
    missing = str(uuid4())
    assert client.get(f"/api/v1/runs/{missing}", headers=_auth("alice")).status_code == 404
    url = f"/api/v1/runs/{missing}/results"
    assert client.get(url, headers=_auth("alice")).status_code == 404
    url = f"/api/v1/runs/{missing}/artifacts"
    assert client.get(url, headers=_auth("alice")).status_code == 404


def test_malformed_run_id_404(client: TestClient, env: dict[str, Any]) -> None:
    assert client.get("/api/v1/runs/not-a-uuid", headers=_auth("alice")).status_code == 404


def test_missing_artifact_404(client: TestClient, env: dict[str, Any]) -> None:
    run_id = _seed_run(env)
    other_run = _seed_run(env)
    body = client.get(f"/api/v1/runs/{run_id}", headers=_auth("alice")).json()
    artifact_id = body["artifacts"][0]["id"]
    # an artifact from run_id requested under a different run → not found here
    url = f"/api/v1/runs/{other_run}/artifacts/{artifact_id}/content"
    assert client.get(url, headers=_auth("alice")).status_code == 404
    # a nonexistent artifact id → 404
    url = f"/api/v1/runs/{run_id}/artifacts/{str(uuid4())}/content"
    assert client.get(url, headers=_auth("alice")).status_code == 404
