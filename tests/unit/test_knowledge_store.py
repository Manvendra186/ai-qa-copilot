"""S5.3 project knowledge API tests (build bible §7, §14, §19 Phase 5).

Covers the API composition layer that turns a project's QA data into a
searchable knowledge corpus persisted in the ``knowledge_documents`` table,
built on ``qa_copilot_knowledge`` (document / chunk / lexical-search core):

- ``knowledge_store``:
  - ``build_project_knowledge`` — repository files (only when a real dir is
    given) + persisted requirements; ``ValueError`` for a missing repo dir
  - ``persist_project_knowledge`` — idempotent delete+insert, stable ids
  - ``knowledge_status`` — per-source counts + last index time (empty → 0)
  - ``search_project_knowledge`` — ranked lexical hits, top-k capped at 5
  - ``list_project_knowledge_documents`` — newest first, limit/offset
- ``KnowledgeIndexJobAgent.run`` — job type / stage, success, project
  isolation, stable ``knowledge://<project>`` output ref
- routes:
  - POST /projects/{id}/knowledge/index → 202 + job_id (member+; 403 viewer /
    non-member / unknown project); a missing repo dir → job ``failed``
  - GET  /projects/{id}/knowledge/status (viewer+; 404 unknown; 403 non-member)
  - GET  /projects/{id}/knowledge?q=...&top_k=.. (viewer+; 422 top_k>5 / blank)
  - GET  /projects/{id}/knowledge/documents[?limit&offset] (viewer+)
  - GET  /projects/{id}/knowledge/documents/{id} (viewer+; 404 unknown)
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_api import auth
from qa_copilot_api.config import Settings
from qa_copilot_api.jobs import JobContext, KnowledgeIndexJobAgent
from qa_copilot_api.knowledge_store import (
    build_project_knowledge,
    knowledge_status,
    list_project_knowledge_documents,
    persist_project_knowledge,
    search_project_knowledge,
)
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import JobType, ProjectRole
from qa_copilot_knowledge import KnowledgeSourceType
from qa_copilot_repository import db, models

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: each test gets a UNIQUE db name (pid + random) so that
# concurrent pytest invocations can never race on DROP/CREATE of one shared
# database ("database is being accessed by other users").
TEST_DB_PREFIX = "qa_copilot_knowledge"

SECRET = "test-secret-0123456789abcdef"  # 16+ chars, test-only
PASSWORD = "correct-horse-battery-staple"

# ids are Postgres UUIDs — deterministic values, stable across runs.
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

# Distinctive corpus content: the requirement carries "payment gateway" and the
# repository file carries "backoff schedule", so a shared query hits both.
REQ_CONTENT = (
    "The payment gateway must retry failed charges with an exponential "
    "backoff before surfacing a permanent failure to the operator console."
)
REPO_FILE_REL = "src/retry_policy.py"
REPO_FILE_CONTENT = (
    "# retry_policy.py\n"
    "BACKOFF_SCHEDULE = [1, 2, 4]\n"
    "def schedule_retry(attempt):\n"
    "    return BACKOFF_SCHEDULE[min(attempt, 2)]\n"
)


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
            job_tick_delay_s=0.01,
            # Pin the stub agent so these tests stay hermetic (init kwargs beat
            # process env vars / a leaked repo `.env` — no real LLM in tests).
            llm_base_url=None,
            llm_model=None,
            _env_file=None,  # type: ignore[call-arg]  # pydantic private kwarg
        )
    )

    yield {"app": app, "engine": engine, "dbname": dbname}

    # Close pooled connections before DROP DATABASE, or Postgres refuses.
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


@contextmanager
def _session(env: dict[str, Any]) -> Iterator[Any]:
    with db.make_session_factory(env["engine"])() as session:
        yield session


def _seed_requirement(env: dict[str, Any], project_id: str) -> str:
    """Persist one requirement so the corpus carries QA data (not just files)."""
    with _session(env) as session:
        req = models.Requirement(
            project_id=project_id,
            title="Payment gateway retry",
            content=REQ_CONTENT,
            acceptance_criteria=["retry transient 5xx", "surface permanent failure"],
        )
        session.add(req)
        session.commit()
        return req.id


def _make_repo(base: Path) -> Path:
    """A minimal repository with one source file of distinctive content."""
    root = base / "repo"
    (root / "src").mkdir(parents=True)
    (root / REPO_FILE_REL).write_text(REPO_FILE_CONTENT, encoding="utf-8")
    return root


def _index_corpus(env: dict[str, Any], repo: Path | None) -> int:
    """Build + persist the ACME corpus directly; return the stored doc count."""
    with _session(env) as session:
        docs, _ = build_project_knowledge(session, ACME_ID, str(repo) if repo else None)
        n = persist_project_knowledge(session, ACME_ID, docs)
        session.commit()
        return n


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
    raise AssertionError(f"job {job_id} did not reach a terminal status: {last}")


class TestKnowledgeStore:
    """The ``knowledge_store`` composition layer over ``qa_copilot_knowledge``."""

    def test_build_requires_existing_repo_dir(self, env: dict[str, Any]) -> None:
        with _session(env) as session:
            with pytest.raises(ValueError, match="not an existing directory"):
                build_project_knowledge(session, ACME_ID, "/definitely/not/a/dir")

    def test_build_includes_repository_and_requirement(
        self, env: dict[str, Any], tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        req_id = _seed_requirement(env, ACME_ID)
        with _session(env) as session:
            docs, capped = build_project_knowledge(session, ACME_ID, str(repo))
            types = {d.source_type for d in docs}
            assert KnowledgeSourceType.REPOSITORY_FILE in types
            assert KnowledgeSourceType.REQUIREMENT in types
            assert capped is False
            req_doc = next(d for d in docs if d.id == req_id)
            assert req_doc.source_type is KnowledgeSourceType.REQUIREMENT
            assert "payment gateway" in req_doc.content

    def test_persist_is_idempotent_with_stable_ids(
        self, env: dict[str, Any], tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)
        with _session(env) as session:
            docs, _ = build_project_knowledge(session, ACME_ID, str(repo))
            n = persist_project_knowledge(session, ACME_ID, docs)
            session.commit()
            ids_first = {r.id for r in list_project_knowledge_documents(session, ACME_ID)}

            # Re-index the same inputs: stable ids → delete+insert is a clean
            # refresh (same count, same primary keys).
            docs2, _ = build_project_knowledge(session, ACME_ID, str(repo))
            n2 = persist_project_knowledge(session, ACME_ID, docs2)
            session.commit()
            ids_second = {r.id for r in list_project_knowledge_documents(session, ACME_ID)}
        assert n == len(docs) == n2
        assert ids_first == ids_second

    def test_status_aggregates_counts(self, env: dict[str, Any], tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)
        _index_corpus(env, repo)
        with _session(env) as session:
            status = knowledge_status(session, ACME_ID)
        assert status["document_count"] >= 2
        by_type = status["by_source_type"]
        assert by_type[KnowledgeSourceType.REQUIREMENT.value] >= 1
        assert by_type[KnowledgeSourceType.REPOSITORY_FILE.value] >= 1
        assert status["source_types"] == sorted(status["source_types"])
        assert status["last_indexed_at"] is not None

    def test_status_empty_project(self, env: dict[str, Any]) -> None:
        with _session(env) as session:
            status = knowledge_status(session, ACME_ID)
        assert status["document_count"] == 0
        assert status["by_source_type"] == {}
        assert status["source_types"] == []
        assert status["last_indexed_at"] is None

    def test_list_newest_first_and_pagination(self, env: dict[str, Any], tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)
        total = _index_corpus(env, repo)
        assert total >= 2
        with _session(env) as session:
            first = list_project_knowledge_documents(session, ACME_ID, limit=1, offset=0)
            assert len(first) == 1
            second = list_project_knowledge_documents(session, ACME_ID, limit=1, offset=1)
            assert len(second) == 1
            assert second[0].id != first[0].id  # distinct, ordered pages
            all_rows = list_project_knowledge_documents(session, ACME_ID, limit=500)
            assert len(all_rows) == total

    def test_search_returns_ranked_hits_capped_at_five(
        self, env: dict[str, Any], tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)
        _index_corpus(env, repo)
        with _session(env) as session:
            result = search_project_knowledge(
                session, ACME_ID, "payment gateway backoff retry", top_k=5
            )
            # top-k is hard-capped at 5 even when asked for more (§14).
            big = search_project_knowledge(session, ACME_ID, "retry backoff", top_k=50)
        assert result.query == "payment gateway backoff retry"
        assert 1 <= len(result.hits) <= 5
        hit = result.hits[0]
        assert hit.score > 0
        assert hit.matched_terms
        assert hit.chunk.content
        assert hit.chunk.source_type in {
            KnowledgeSourceType.REQUIREMENT,
            KnowledgeSourceType.REPOSITORY_FILE,
        }
        assert result.total_candidates >= len(result.hits)
        assert len(big.hits) <= 5


class TestKnowledgeIndexJobAgent:
    """``KnowledgeIndexJobAgent.run`` — job type, stage, isolation, output ref."""

    def test_run_success_stable_output_ref_and_project_isolation(
        self, env: dict[str, Any], tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)  # BETA intentionally gets nothing

        agent = KnowledgeIndexJobAgent(env["engine"])
        events: list[tuple[str, dict[str, Any]]] = []

        async def _emit(event: str, data: dict[str, Any]) -> None:
            events.append((event, data))

        ctx = JobContext(
            job_id=str(uuid4()),
            project_id=ACME_ID,
            job_type=JobType.KNOWLEDGE_INDEX,
            input={"repository_path": str(repo)},
            emit=_emit,
        )

        async def _drive() -> str | None:
            return await agent.run(ctx)

        output_ref = asyncio.run(_drive())

        # Stable, project-scoped output reference.
        assert output_ref == f"knowledge://{ACME_ID}"
        # Single-stage lifecycle reported over the event bus.
        names = [name for name, _ in events]
        assert "stage.started" in names
        assert "stage.completed" in names
        completed = next(data for name, data in events if name == "stage.completed")
        assert completed["stage"] == "knowledge_index"
        assert completed["documents"] >= 2
        # ACME got the corpus; BETA stayed empty (no cross-project leak).
        with _session(env) as session:
            assert knowledge_status(session, ACME_ID)["document_count"] >= 2
            assert knowledge_status(session, BETA_ID)["document_count"] == 0

    def test_run_rejects_missing_repository_dir(self, env: dict[str, Any]) -> None:
        agent = KnowledgeIndexJobAgent(env["engine"])

        async def _emit(_event: str, _data: dict[str, Any]) -> None:
            return None

        ctx = JobContext(
            job_id=str(uuid4()),
            project_id=ACME_ID,
            job_type=JobType.KNOWLEDGE_INDEX,
            input={"repository_path": "/definitely/not/a/dir"},
            emit=_emit,
        )

        async def _drive() -> str | None:
            return await agent.run(ctx)

        with pytest.raises(ValueError, match="not an existing directory"):
            asyncio.run(_drive())


class TestKnowledgeRoutes:
    """S5.3 knowledge endpoints: 202 + job_id, RBAC, search, listing, detail."""

    def test_index_requires_member(self, client: TestClient, env: dict[str, Any]) -> None:
        # owner (member+) → 202 + job_id + Location header
        r = client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/index", json={}, headers=_auth("alice")
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert "job_id" in body
        assert r.headers["location"] == f"/api/v1/jobs/{body['job_id']}"

        # viewer and non-member denied; unknown project → 403 (no existence leak)
        assert (
            client.post(
                f"/api/v1/projects/{ACME_ID}/knowledge/index", json={}, headers=_auth("carol")
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/projects/{ACME_ID}/knowledge/index", json={}, headers=_auth("dave")
            ).status_code
            == 403
        )
        unknown = str(uuid4())
        assert (
            client.post(
                f"/api/v1/projects/{unknown}/knowledge/index", json={}, headers=_auth("alice")
            ).status_code
            == 403
        )

    def test_index_end_to_end(
        self, client: TestClient, env: dict[str, Any], tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)

        r = client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/index",
            json={"repository_path": str(repo)},
            headers=_auth("alice"),
        )
        assert r.status_code == 202, r.text
        job = _wait_terminal(client, "alice", r.json()["job_id"])
        assert job["status"] == "completed"
        assert job["type"] == "knowledge_index"
        assert job["output_ref"] == f"knowledge://{ACME_ID}"

        # status reflects the persisted corpus
        st = client.get(
            f"/api/v1/projects/{ACME_ID}/knowledge/status", headers=_auth("alice")
        ).json()
        assert st["document_count"] >= 2
        assert st["by_source_type"][KnowledgeSourceType.REQUIREMENT.value] >= 1
        assert st["by_source_type"][KnowledgeSourceType.REPOSITORY_FILE.value] >= 1
        assert st["last_indexed_at"] is not None

        # search returns project-scoped hits
        sr = client.get(
            f"/api/v1/projects/{ACME_ID}/knowledge",
            params={"q": "payment gateway backoff retry", "top_k": 5},
            headers=_auth("alice"),
        )
        assert sr.status_code == 200, sr.text
        body = sr.json()
        assert 1 <= len(body["hits"]) <= 5
        hit = body["hits"][0]
        assert hit["score"] > 0
        assert hit["source_type"] in {
            KnowledgeSourceType.REQUIREMENT.value,
            KnowledgeSourceType.REPOSITORY_FILE.value,
        }
        assert hit["title"]
        assert hit["content"]

        # list documents
        dl = client.get(f"/api/v1/projects/{ACME_ID}/knowledge/documents", headers=_auth("alice"))
        assert dl.status_code == 200, dl.text
        docs = dl.json()
        assert len(docs) >= 2
        assert docs[0]["id"]
        assert docs[0]["title"]
        assert docs[0]["content"]

        # fetch one document by id
        one = client.get(
            f"/api/v1/projects/{ACME_ID}/knowledge/documents/{docs[0]['id']}",
            headers=_auth("alice"),
        )
        assert one.status_code == 200, one.text
        assert one.json()["id"] == docs[0]["id"]

    def test_index_with_missing_repo_dir_fails_job(
        self, client: TestClient, env: dict[str, Any]
    ) -> None:
        r = client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/index",
            json={"repository_path": "/definitely/not/a/dir"},
            headers=_auth("alice"),
        )
        assert r.status_code == 202, r.text
        job = _wait_terminal(client, "alice", r.json()["job_id"])
        assert job["status"] == "failed"
        assert job["error"]

    def test_status_and_documents_rbac_and_404(
        self, client: TestClient, env: dict[str, Any]
    ) -> None:
        base = f"/api/v1/projects/{ACME_ID}/knowledge"
        # viewer allowed
        assert client.get(f"{base}/status", headers=_auth("carol")).status_code == 200
        assert client.get(f"{base}/documents", headers=_auth("carol")).status_code == 200
        # non-member denied (403, not 404 — no existence leak)
        assert client.get(f"{base}/status", headers=_auth("dave")).status_code == 403
        assert client.get(f"{base}/documents", headers=_auth("dave")).status_code == 403
        # unknown project → 404
        unknown = str(uuid4())
        assert (
            client.get(
                f"/api/v1/projects/{unknown}/knowledge/status", headers=_auth("alice")
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/api/v1/projects/{unknown}/knowledge/documents", headers=_auth("alice")
            ).status_code
            == 404
        )

    def test_search_validation_and_rbac(
        self, client: TestClient, env: dict[str, Any], tmp_path: Path
    ) -> None:
        repo = _make_repo(tmp_path)
        _seed_requirement(env, ACME_ID)
        _index_corpus(env, repo)
        base = f"/api/v1/projects/{ACME_ID}/knowledge"
        # top_k above the hard cap → 422 (§14)
        assert (
            client.get(base, params={"q": "retry", "top_k": 6}, headers=_auth("alice")).status_code
            == 422
        )
        # blank query → 422
        assert client.get(base, params={"q": ""}, headers=_auth("alice")).status_code == 422
        # non-member → 403
        assert client.get(base, params={"q": "retry"}, headers=_auth("dave")).status_code == 403
        # valid query, viewer → 200
        assert client.get(base, params={"q": "retry"}, headers=_auth("carol")).status_code == 200

    def test_get_document_unknown_404(self, client: TestClient, env: dict[str, Any]) -> None:
        unknown_doc = str(uuid4())
        r = client.get(
            f"/api/v1/projects/{ACME_ID}/knowledge/documents/{unknown_doc}",
            headers=_auth("carol"),
        )
        assert r.status_code == 404
