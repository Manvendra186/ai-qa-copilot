"""S2.4 generated-test review tests (build bible §19 S2.4, §11, §31.1, §31.3).

Covers the full human-in-the-loop contract for S2.3 output:

- ``POST /api/v1/automation/generate`` → 202 + job + a **pending**
  ``generated_tests`` row whose id is the job's ``output_ref`` (member+;
  401 unauthenticated; 403 for viewers/non-members; 403 — not 404 — for
  unknown projects: no existence leak; 404 for unknown/cross-project test
  cases; agent failure → job failed + no row)
- ``GET /api/v1/projects/{id}/generated-tests`` → the review queue (viewer+;
  non-member 403; unknown project 404)
- ``GET /api/v1/generated-tests/{id}`` → the review row (viewer+; 403/404
  matrix; malformed id 404 without a 500)
- approve / reject / apply: the domain state machine is enforced (invalid or
  no-op transitions → 409), the reviewer trail is written, and every action
  is audited (``ai_sessions`` anchor + ``ai_actions`` row, §31.1)
- ``apply``: writes the file under the repository root only — existing
  target → 409 (V1: no silent overwrite), missing repository / missing
  ``repository_path`` / path escape → 409, and the row is rolled back
  unchanged on every failure path
"""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth
from qa_copilot_api.config import Settings
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import ProjectRole
from qa_copilot_repository import db, models
from qa_copilot_repository import generated_tests as repo_generated_tests
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: each test gets a UNIQUE db name (pid + random) so that
# concurrent pytest invocations can never race on DROP/CREATE of one shared
# database (same pattern as tests/unit/test_jobs.py).
TEST_DB_PREFIX = "qa_copilot_gen"

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

CASE_TITLE = "valid login succeeds"
STUB_FILE_PATH = "tests/valid-login-succeeds.spec.ts"  # AutomationStub slug


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
            # S2.4: pin the deterministic AutomationStub — hermetic, no live
            # model. Init kwargs beat process env vars (pydantic-settings),
            # so a real LLM in the environment (or a repo `.env` leaked by
            # alembic's `_load_dotenv`) can never pull in the S2.3 agent.
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


def _persist_test_case(env: dict[str, Any], project_id: str = ACME_ID) -> str:
    """One requirement + one designed case (the S1.2 shape the S2.4 job reads).

    Writes through the same entry point ``TestDesignJobAgent`` uses
    (``persist_requirement_with_suite``), so the automation job is tested
    against realistic rows. Returns the DB uuid of the single case.
    """
    from qa_copilot_ai import TestCase as AITestCase
    from qa_copilot_ai import TestSuite
    from qa_copilot_repository import requirements as repo_requirements

    suite = TestSuite(
        test_cases=[
            AITestCase(
                id="TC-001",
                title=CASE_TITLE,
                type="functional",
                priority="high",
                preconditions=["a registered user exists"],
                steps=["enter email + password", "submit"],
                expected_results=["redirected to /home"],
            )
        ]
    )
    with db.make_session_factory(env["engine"])() as session:
        persisted = repo_requirements.persist_requirement_with_suite(
            session,
            project_id=project_id,
            title="Login flow",
            content="Users can log in with email + password and are redirected to /home.",
            acceptance_criteria=["valid credentials -> 200"],
            suite=suite,
        )
        session.commit()
        case_id = session.scalar(
            select(models.TestCase.id)
            .join(
                models.RequirementTestCase,
                models.RequirementTestCase.test_case_id == models.TestCase.id,
            )
            .where(
                models.RequirementTestCase.requirement_id == persisted.requirement_id,
                models.TestCase.title == CASE_TITLE,
            )
        )
        assert case_id is not None
    return case_id


def _seed_generated_test(
    env: dict[str, Any],
    *,
    project_id: str = ACME_ID,
    file_path: str = "tests/seeded.spec.ts",
    repository_path: str | None = None,
) -> str:
    """One pending ``generated_tests`` row via the S2.4 persistence entry point.

    The review-flow tests (approve / reject / apply + RBAC + audit) only need
    a row to act on — driving the full job for each one would re-test S2.3
    for no reason.
    """
    with db.make_session_factory(env["engine"])() as session:
        row = repo_generated_tests.persist_generated_test(
            session,
            project_id=project_id,
            job_id=None,
            test_case_id=None,
            file_path=file_path,
            file_path_pattern=None,
            language="typescript",
            framework="playwright",
            content="// seeded S2.4 review row\n",
            notes=["seeded directly for the review-flow tests"],
            repository_path=repository_path,
        )
        row_id = row.id
        session.commit()
    return row_id


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


# --- 202 job → generated_tests row (S2.4, §11) ---------------------------------


def test_generate_returns_202_and_persists_pending_row(
    client: TestClient, env: dict[str, Any], tmp_path: Path
) -> None:
    """Job completes; ``output_ref`` is the durable pending review row."""
    case_id = _persist_test_case(env)
    body = {"project_id": ACME_ID, "test_case_id": case_id, "repository_path": str(tmp_path)}
    r = client.post("/api/v1/automation/generate", json=body, headers=_auth("alice"))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert job_id
    assert r.json()["status"] == "pending"
    assert r.headers["location"] == f"/api/v1/jobs/{job_id}"  # §11

    job = _wait_terminal(client, "alice", job_id)
    assert job["status"] == "completed", job
    assert job["type"] == "automation_generation"
    assert job["project_id"] == ACME_ID
    assert job["progress"] == 1.0
    assert job["error"] is None
    gt_id = job["output_ref"]
    UUID(gt_id)  # the job points at the review row, not raw generated content

    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["id"] == gt_id
    assert row["project_id"] == ACME_ID
    assert row["job_id"] == job_id
    assert row["test_case_id"] == case_id
    assert row["file_path"] == STUB_FILE_PATH
    assert row["language"] == "typescript"
    assert row["framework"] == "playwright"
    assert row["content"]
    assert row["notes"]
    assert row["repository_path"] == str(tmp_path)
    assert row["status"] == "pending"  # human review is mandatory before ship
    assert row["reviewed_by"] is None
    assert row["review_note"] is None
    assert row["created_at"] and row["updated_at"]

    # the project's review queue lists the row
    listed = client.get(
        f"/api/v1/projects/{ACME_ID}/generated-tests", headers=_auth("alice")
    ).json()
    assert [item["id"] for item in listed] == [gt_id]

    # audit: the job's ai_actions row links to the durable review row (§31.1)
    with db.make_session_factory(env["engine"])() as session:
        anchor = session.scalar(
            select(models.AISession).where(models.AISession.task_type == "automation_generation")
        )
        assert anchor is not None
        assert anchor.status == "completed"
        action = session.scalar(
            select(models.AIAction).where(models.AIAction.session_id == anchor.id)
        )
        assert action is not None
        assert action.agent == "test-automator"
        assert action.model == "stub"
        assert action.output_ref == gt_id


def test_generate_rbac_and_validation(
    client: TestClient, env: dict[str, Any], tmp_path: Path
) -> None:
    """member+ only; unknown project 403 (no leak); case lookups 404 (§31.3)."""
    case_id = _persist_test_case(env)
    body = {"project_id": ACME_ID, "test_case_id": case_id, "repository_path": str(tmp_path)}

    # unauthenticated
    assert client.post("/api/v1/automation/generate", json=body).status_code == 401
    # viewer may not start work; non-member of the project is blocked
    r = client.post("/api/v1/automation/generate", json=body, headers=_auth("carol"))
    assert r.status_code == 403
    r = client.post("/api/v1/automation/generate", json=body, headers=_auth("dave"))
    assert r.status_code == 403
    # unknown project: 403, not 404 — no existence leak (§31.3)
    ghost = dict(body, project_id=str(uuid5(NS, "ghost-project")))
    r = client.post("/api/v1/automation/generate", json=ghost, headers=_auth("alice"))
    assert r.status_code == 403

    # malformed / unknown / cross-project test case: 404 ("not found")
    r = client.post(
        "/api/v1/automation/generate",
        json=dict(body, test_case_id="not-a-uuid"),
        headers=_auth("alice"),
    )
    assert r.status_code == 404
    r = client.post(
        "/api/v1/automation/generate",
        json=dict(body, test_case_id=str(uuid5(NS, "ghost-case"))),
        headers=_auth("alice"),
    )
    assert r.status_code == 404
    other_project_case = _persist_test_case(env, project_id=BETA_ID)
    r = client.post(
        "/api/v1/automation/generate",
        json=dict(body, test_case_id=other_project_case),
        headers=_auth("alice"),
    )
    assert r.status_code == 404

    # schema floor: test_case_id is required
    r = client.post(
        "/api/v1/automation/generate", json={"project_id": ACME_ID}, headers=_auth("alice")
    )
    assert r.status_code == 422

    # nothing was created by any of the attempts above
    r = client.get(f"/api/v1/projects/{ACME_ID}/generated-tests", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json() == []


def test_generate_failed_job_when_agent_cannot_run(client: TestClient, env: dict[str, Any]) -> None:
    """Agent failure → job failed + error; no review row; anchor marked failed."""
    case_id = _persist_test_case(env)
    body = {"project_id": ACME_ID, "test_case_id": case_id, "repository_path": ""}
    r = client.post("/api/v1/automation/generate", json=body, headers=_auth("alice"))
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, "alice", r.json()["job_id"])
    assert job["status"] == "failed"
    assert "repository_path" in (job["error"] or "")
    assert job["output_ref"] is None

    with db.make_session_factory(env["engine"])() as session:
        anchor = session.scalar(
            select(models.AISession).where(models.AISession.task_type == "automation_generation")
        )
        assert anchor is not None
        assert anchor.status == "failed"

    # no review row was created
    r = client.get(f"/api/v1/projects/{ACME_ID}/generated-tests", headers=_auth("alice"))
    assert r.json() == []


# --- list + detail RBAC (§31.3) -------------------------------------------------


def test_list_and_detail_rbac(client: TestClient, env: dict[str, Any]) -> None:
    """viewer+ reads the queue; non-members 403; unknown ids/projects 404."""
    gt_id = _seed_generated_test(env)

    # list: owner + viewer ok, non-member 403, unknown project 404
    r = client.get(f"/api/v1/projects/{ACME_ID}/generated-tests", headers=_auth("alice"))
    assert r.status_code == 200
    assert [item["id"] for item in r.json()] == [gt_id]
    r = client.get(f"/api/v1/projects/{ACME_ID}/generated-tests", headers=_auth("carol"))
    assert r.status_code == 200
    r = client.get(f"/api/v1/projects/{ACME_ID}/generated-tests", headers=_auth("dave"))
    assert r.status_code == 403
    ghost_project = str(uuid5(NS, "ghost-project"))
    r = client.get(f"/api/v1/projects/{ghost_project}/generated-tests", headers=_auth("alice"))
    assert r.status_code == 404
    # unauthenticated
    r = client.get(f"/api/v1/projects/{ACME_ID}/generated-tests")
    assert r.status_code == 401

    # detail: viewer+ ok; non-member 403 (not 404 — no existence leak)
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("carol"))
    assert r.status_code == 200
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("dave"))
    assert r.status_code == 403
    ghost = str(uuid5(NS, "ghost-generated-test"))
    r = client.get(f"/api/v1/generated-tests/{ghost}", headers=_auth("alice"))
    assert r.status_code == 404
    r = client.get("/api/v1/generated-tests/not-a-uuid", headers=_auth("alice"))
    assert r.status_code == 404
    # unauthenticated
    r = client.get(f"/api/v1/generated-tests/{gt_id}")
    assert r.status_code == 401


# --- review transitions (domain state machine, §19 S2.4) ------------------------


def test_approve_then_apply(client: TestClient, env: dict[str, Any], tmp_path: Path) -> None:
    """pending → approved → applied; applied is terminal; the file is written."""
    gt_id = _seed_generated_test(env, file_path=STUB_FILE_PATH, repository_path=str(tmp_path))

    r = client.post(
        f"/api/v1/generated-tests/{gt_id}/approve",
        json={"note": "looks good"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["status"] == "approved"
    assert row["reviewed_by"] == ALICE_ID
    assert row["review_note"] == "looks good"
    assert row["reviewed_at"]

    # no-op transition → 409
    r = client.post(f"/api/v1/generated-tests/{gt_id}/approve", headers=_auth("alice"))
    assert r.status_code == 409

    target = tmp_path / STUB_FILE_PATH
    assert not target.exists()
    r = client.post(f"/api/v1/generated-tests/{gt_id}/apply", headers=_auth("alice"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    assert target.is_file()
    assert target.read_text(encoding="utf-8")  # the row's content was written

    # applied is terminal: every review action is now 409 (no-op or illegal)
    for action in ("approve", "apply", "reject"):
        r = client.post(f"/api/v1/generated-tests/{gt_id}/{action}", headers=_auth("alice"))
        assert r.status_code == 409
        if action == "apply":  # no-op gets its own message
            assert r.json()["detail"] == "generated test is already applied"
        else:  # illegal transition out of a terminal state
            assert "allowed from applied: none" in r.json()["detail"]


def test_apply_directly_from_pending(
    client: TestClient, env: dict[str, Any], tmp_path: Path
) -> None:
    """``pending → applied`` is legal — apply implies approval (§19 S2.4)."""
    gt_id = _seed_generated_test(env, repository_path=str(tmp_path))
    r = client.post(
        f"/api/v1/generated-tests/{gt_id}/apply", json={"note": "ship it"}, headers=_auth("alice")
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"
    target = tmp_path / "tests" / "seeded.spec.ts"
    assert target.is_file()


def test_reject_is_terminal(client: TestClient, env: dict[str, Any]) -> None:
    """pending → rejected is terminal: re-generating creates a NEW row (V1)."""
    gt_id = _seed_generated_test(env)
    r = client.post(
        f"/api/v1/generated-tests/{gt_id}/reject",
        json={"note": "wrong framework"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["status"] == "rejected"
    assert row["reviewed_by"] == ALICE_ID
    assert row["review_note"] == "wrong framework"

    for action in ("approve", "apply", "reject"):
        r = client.post(f"/api/v1/generated-tests/{gt_id}/{action}", headers=_auth("alice"))
        assert r.status_code == 409
        if action == "reject":  # no-op gets its own message
            assert r.json()["detail"] == "generated test is already rejected"
        else:  # illegal transition out of a terminal state
            assert "allowed from rejected: none" in r.json()["detail"]


def test_review_rbac(client: TestClient, env: dict[str, Any]) -> None:
    """Reviewing needs member+; blocked attempts change nothing (§31.3)."""
    gt_id = _seed_generated_test(env)

    # unauthenticated: 401 on every review action
    for action in ("approve", "apply", "reject"):
        r = client.post(f"/api/v1/generated-tests/{gt_id}/{action}")
        assert r.status_code == 401
    # viewer may read but not review
    for action in ("approve", "apply", "reject"):
        r = client.post(f"/api/v1/generated-tests/{gt_id}/{action}", headers=_auth("carol"))
        assert r.status_code == 403
    # non-member blocked
    r = client.post(f"/api/v1/generated-tests/{gt_id}/approve", headers=_auth("dave"))
    assert r.status_code == 403

    # blocked attempts change nothing
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("alice"))
    row = r.json()
    assert row["status"] == "pending"
    assert row["reviewed_by"] is None
    assert row["review_note"] is None


def test_review_audit_trail(client: TestClient, env: dict[str, Any]) -> None:
    """Every review action is audited: session anchor + human action (§31.1)."""
    gt_id = _seed_generated_test(env)
    r = client.post(
        f"/api/v1/generated-tests/{gt_id}/approve", json={"note": "ok"}, headers=_auth("alice")
    )
    assert r.status_code == 200, r.text
    r = client.post(f"/api/v1/generated-tests/{gt_id}/reject", headers=_auth("alice"))
    assert r.status_code == 200, r.text

    with db.make_session_factory(env["engine"])() as session:
        anchors = session.scalars(
            select(models.AISession).where(
                models.AISession.project_id == ACME_ID,
                models.AISession.task_type == "generated_test_review",
            )
        ).all()
        assert len(anchors) == 2
        assert all(anchor.status == "completed" for anchor in anchors)
        assert all(anchor.user_id == ALICE_ID for anchor in anchors)

        actions = session.scalars(
            select(models.AIAction).where(models.AIAction.output_ref == gt_id)
        ).all()
        assert len(actions) == 2
        assert sorted(a.approval_status or "" for a in actions) == ["approved", "rejected"]
        for action in actions:
            assert action.agent == "human-review"
            assert action.model == "human"


# --- apply: file-write guards ---------------------------------------------------


def test_apply_refuses_existing_file(
    client: TestClient, env: dict[str, Any], tmp_path: Path
) -> None:
    """V1 policy: no silent overwrite — existing target → 409, row unchanged."""
    gt_id = _seed_generated_test(env, repository_path=str(tmp_path))
    target = tmp_path / "tests" / "seeded.spec.ts"
    target.parent.mkdir(parents=True)
    sentinel = "human-written — must not be clobbered\n"
    target.write_text(sentinel, encoding="utf-8")

    r = client.post(f"/api/v1/generated-tests/{gt_id}/apply", headers=_auth("alice"))
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]

    assert target.read_text(encoding="utf-8") == sentinel  # untouched
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("alice"))
    row = r.json()
    assert row["status"] == "pending"  # rolled back, review still possible
    assert row["reviewed_by"] is None

    # and the row can still be reviewed normally afterwards
    r = client.post(f"/api/v1/generated-tests/{gt_id}/approve", headers=_auth("alice"))
    assert r.status_code == 200


def test_apply_requires_repository_path(client: TestClient, env: dict[str, Any]) -> None:
    gt_id = _seed_generated_test(env, repository_path=None)
    r = client.post(f"/api/v1/generated-tests/{gt_id}/apply", headers=_auth("alice"))
    assert r.status_code == 409
    assert "repository_path" in r.json()["detail"]
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("alice"))
    assert r.json()["status"] == "pending"  # rolled back


def test_apply_refuses_path_escape(client: TestClient, env: dict[str, Any], tmp_path: Path) -> None:
    """A row whose file_path climbs out of the repository root is never written."""
    gt_id = _seed_generated_test(env, file_path="../escape.spec.ts", repository_path=str(tmp_path))
    r = client.post(f"/api/v1/generated-tests/{gt_id}/apply", headers=_auth("alice"))
    assert r.status_code == 409
    assert "escapes the repository root" in r.json()["detail"]
    assert not (tmp_path.parent / "escape.spec.ts").exists()
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("alice"))
    assert r.json()["status"] == "pending"


def test_apply_refuses_missing_repository(
    client: TestClient, env: dict[str, Any], tmp_path: Path
) -> None:
    gt_id = _seed_generated_test(env, repository_path=str(tmp_path / "no-such-dir"))
    r = client.post(f"/api/v1/generated-tests/{gt_id}/apply", headers=_auth("alice"))
    assert r.status_code == 409
    assert "not found" in r.json()["detail"]
    r = client.get(f"/api/v1/generated-tests/{gt_id}", headers=_auth("alice"))
    assert r.json()["status"] == "pending"
