"""Past-requirements history list tests (S1.3 read-back extension, §31.3).

Covers ``GET /api/v1/projects/{id}/requirements`` (the web shell's
"past requirements" list, ``viewer`` or above):

- summary rows (id / title / risk / created_at / test-case count), newest
  first, scoped to the project (no cross-project leak)
- the same RBAC shape as the S2.4/S3.2 project list endpoints: 403 for
  non-members, 404 for unknown projects
- an empty list for a project with no requirements yet
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
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
from qa_copilot_domain.enums import ProjectRole
from qa_copilot_repository import db, models
from qa_copilot_repository import requirements as repo_requirements

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: each test gets a UNIQUE db name (pid + random) so that
# concurrent pytest invocations can never race on DROP/CREATE of one shared
# database (same pattern as tests/unit/test_generated_tests.py).
TEST_DB_PREFIX = "qa_copilot_reqhist"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only

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
def env() -> Iterator[dict[str, Any]]:
    """Scratch Postgres DB + migrated schema + users/projects/roles + app."""
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
                    password_hash=auth.hash_password("unused-in-these-tests"),
                )
            )
        session.add(models.Organization(id=ORG_ID, name="Acme Inc"))
        session.add(models.Project(id=ACME_ID, organization_id=ORG_ID, name="Acme Store"))
        session.add(models.Project(id=BETA_ID, organization_id=ORG_ID, name="Beta App"))
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
            job_tick_delay_s=0.01,
            # Pin the deterministic stubs — hermetic, no live model. Init
            # kwargs beat process env vars (pydantic-settings), so a real LLM
            # in the environment can never leak into these tests.
            llm_base_url=None,
            llm_model=None,
            _env_file=None,  # type: ignore[call-arg]  # pydantic private kwarg
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


def _seed_requirement(
    env: dict[str, Any],
    project_id: str,
    *,
    title: str,
    n_cases: int,
    created_at: datetime,
) -> str:
    """One requirement + its designed cases, with a controlled ``created_at``.

    Writes through the same entry point ``TestDesignJobAgent`` uses
    (``persist_requirement_with_suite``) — realistic rows — then pins
    ``created_at`` explicitly so the ordering assertions are deterministic
    (the server default ``now()`` could collide on a fast machine).
    """
    from qa_copilot_ai import TestCase as AITestCase
    from qa_copilot_ai import TestSuite

    suite = TestSuite(
        test_cases=[
            AITestCase(
                id=f"TC-{i:03d}",
                title=f"{title} — case {i}",
                type="functional",
                priority="medium",
                preconditions=["a user is signed in"],
                steps=[f"perform step {i}"],
                expected_results=[f"expected result {i}"],
            )
            for i in range(1, n_cases + 1)
        ]
    )
    with db.make_session_factory(env["engine"])() as session:
        persisted = repo_requirements.persist_requirement_with_suite(
            session,
            project_id=project_id,
            title=title,
            content=f"Content of {title}.",
            acceptance_criteria=["the thing works"],
            suite=suite,
        )
        requirement = session.get(models.Requirement, persisted.requirement_id)
        assert requirement is not None
        requirement.created_at = created_at
        session.commit()
    return persisted.requirement_id


# --- GET /api/v1/projects/{id}/requirements -----------------------------------


def test_list_requirements_returns_summary_rows_newest_first(client, env) -> None:
    old_id = _seed_requirement(
        env,
        ACME_ID,
        title="Login flow",
        n_cases=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    new_id = _seed_requirement(
        env,
        ACME_ID,
        title="Checkout totals",
        n_cases=3,
        created_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    res = client.get(f"/api/v1/projects/{ACME_ID}/requirements", headers=_auth("alice"))
    assert res.status_code == 200
    rows = res.json()
    assert [row["id"] for row in rows] == [new_id, old_id]  # newest first
    assert rows[0]["title"] == "Checkout totals"
    assert rows[0]["test_case_count"] == 3
    assert rows[1]["title"] == "Login flow"
    assert rows[1]["test_case_count"] == 1
    for row in rows:
        assert set(row) == {"id", "title", "risk", "created_at", "test_case_count"}
        assert row["risk"] in {"low", "medium", "high"}
        assert row["created_at"] is not None


def test_list_requirements_is_project_scoped(client, env) -> None:
    acme_id = _seed_requirement(
        env,
        ACME_ID,
        title="Acme requirement",
        n_cases=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    beta_id = _seed_requirement(
        env,
        BETA_ID,
        title="Beta requirement",
        n_cases=2,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    acme_res = client.get(f"/api/v1/projects/{ACME_ID}/requirements", headers=_auth("alice"))
    assert acme_res.status_code == 200
    assert [row["id"] for row in acme_res.json()] == [acme_id]  # no beta leak

    beta_res = client.get(f"/api/v1/projects/{BETA_ID}/requirements", headers=_auth("dave"))
    assert beta_res.status_code == 200
    beta_rows = beta_res.json()
    assert [row["id"] for row in beta_rows] == [beta_id]
    assert beta_rows[0]["test_case_count"] == 2


def test_list_requirements_viewer_can_read(client, env) -> None:
    req_id = _seed_requirement(
        env, ACME_ID, title="Login flow", n_cases=2, created_at=datetime(2026, 3, 1, tzinfo=UTC)
    )
    res = client.get(f"/api/v1/projects/{ACME_ID}/requirements", headers=_auth("carol"))
    assert res.status_code == 200
    rows = res.json()
    assert [row["id"] for row in rows] == [req_id]


def test_list_requirements_non_member_gets_403(client, env) -> None:
    _seed_requirement(
        env,
        ACME_ID,
        title="Acme requirement",
        n_cases=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    res = client.get(f"/api/v1/projects/{ACME_ID}/requirements", headers=_auth("dave"))
    assert res.status_code == 403


def test_list_requirements_unknown_project_gets_404(client, env) -> None:
    res = client.get(f"/api/v1/projects/{uuid4()}/requirements", headers=_auth("alice"))
    assert res.status_code == 404


def test_list_requirements_unauthenticated_gets_401(client, env) -> None:
    res = client.get(f"/api/v1/projects/{ACME_ID}/requirements")
    assert res.status_code == 401


def test_list_requirements_empty_project_returns_empty_list(client, env) -> None:
    # BETA has no requirements in this test's fresh scratch DB.
    res = client.get(f"/api/v1/projects/{BETA_ID}/requirements", headers=_auth("dave"))
    assert res.status_code == 200
    assert res.json() == []
