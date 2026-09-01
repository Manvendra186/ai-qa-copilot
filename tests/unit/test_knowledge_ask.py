"""S5.5 project-knowledge Ask API tests (build bible §7, §14, §19 Phase 5).

Covers the ``POST /projects/{id}/knowledge/ask`` → **202 + job_id** contract
and the ``knowledge.answer`` SSE event that carries the grounded answer and its
citations:

- route:
  - 202 + ``job_id`` + ``Location`` (member+; 401 unauthenticated)
  - 403 viewer / non-member; 403 (not 404) for an unknown project — no
    existence leak (§31.3)
  - 422 for a blank / missing question
- ``knowledge.answer`` event (S5.4 contract, delivered over SSE):
  - refusal (no-LLM stub): ``in_scope=false``, no answer, no citations,
    ``confidence`` present — the API contract holds without a model
  - grounded: ``in_scope=true``, non-empty ``answer``, ≥1 citation carrying
    ``document_ref`` / ``source_type`` / ``title`` / ``score``, ``confidence``
- job agent:
  - retrieves S5.3 chunks, grounds via the S5.4 runner, emits
    ``stage.started`` / ``progress`` / ``knowledge.answer`` / ``stage.completed``
  - ``output_ref`` is a stable ``knowledge-ask://<project>`` reference (the
    full answer rides the SSE event, not the 1024-char ``jobs.output_ref``)
  - a runner that raises → job ``failed`` + error
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, uuid4, uuid5

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from qa_copilot_ai import InMemoryPromptStore, KnowledgeQAAgent, LLMGateway, PromptSpec
from qa_copilot_api import auth
from qa_copilot_api.config import Settings
from qa_copilot_api.jobs import (
    JobContext,
    KnowledgeAskJobAgent,
    KnowledgeQARunner,
)
from qa_copilot_api.knowledge_store import (
    build_project_knowledge,
    persist_project_knowledge,
    search_project_knowledge,
)
from qa_copilot_api.main import create_app
from qa_copilot_domain.enums import JobType, ProjectRole
from qa_copilot_repository import db, models
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"
# Scratch-DB prefix: a UNIQUE db name per test (pid + random) so concurrent
# pytest invocations never race on DROP/CREATE of one shared database.
TEST_DB_PREFIX = "qa_copilot_ask"

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

# Corpus content distinctive enough that a shared query hits both sources.
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
# A question whose terms overlap both corpus documents (lexical search).
QUESTION = "retry backoff payment gateway schedule"


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
            # Pin the no-LLM runner: hermetic. Init kwargs beat process env /
            # a leaked repo `.env`, so a real local model never sneaks in.
            llm_base_url=None,
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


@contextmanager
def _session(env: dict[str, Any]) -> Iterator[Session]:
    with db.make_session_factory(env["engine"])() as session:
        yield session


def _seed_requirement(env: dict[str, Any], project_id: str) -> str:
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
    root = base / "repo"
    (root / "src").mkdir(parents=True)
    (root / REPO_FILE_REL).write_text(REPO_FILE_CONTENT, encoding="utf-8")
    return root


def _index_corpus(env: dict[str, Any], repo: Path) -> int:
    with _session(env) as session:
        docs, _ = build_project_knowledge(session, ACME_ID, str(repo))
        n = persist_project_knowledge(session, ACME_ID, docs)
        session.commit()
        return n


def _first_document_ref(env: dict[str, Any]) -> str:
    """The real ``document_ref`` of a search hit (so a citation enriches)."""
    with _session(env) as session:
        result = search_project_knowledge(session, ACME_ID, QUESTION, top_k=5)
        assert result.hits, "expected at least one knowledge hit for the seeded corpus"
        return result.hits[0].chunk.document_ref


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


# --- fake S5.4 runner (real KnowledgeQAAgent over a stubbed gateway) ----------

_KNOWLEDGE_QA_PROMPT = PromptSpec(
    name="knowledge-qa",
    version=1,
    body=(
        "Answer using ONLY the retrieved context. If the context does not "
        "support the answer, refuse.\n"
        "Question: {{question}}\n\nContext:\n{{context}}\n\n"
        "Return JSON: {in_scope, answer, citations[{source_ref,title}], confidence}"
    ),
    model_class="coder",
    input_budget=8000,
    output_budget=4096,
    schema_ref="knowledge-qa/v1",
    temperature=0.2,
)


class _LlmTransport(httpx.AsyncBaseTransport):
    """Async-transport shim so ``AsyncClient`` accepts a sync fake handler."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _llm_assistant(payload: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 60},
    }


def _fake_qa_gateway(payload: dict[str, object]) -> LLMGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_llm_assistant(payload))

    return LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=0,
        transport=_LlmTransport(handler),
    )


def _grounded_agent(citation_ref: str, citation_title: str) -> KnowledgeQAAgent:
    """A real :class:`KnowledgeQAAgent` whose model returns a grounded answer."""
    payload = {
        "in_scope": True,
        "answer": (
            "Retries use an exponential backoff schedule before surfacing a permanent failure."
        ),
        "citations": [{"source_ref": citation_ref, "title": citation_title}],
        "confidence": 0.9,
    }
    store = InMemoryPromptStore([_KNOWLEDGE_QA_PROMPT])
    return KnowledgeQAAgent(store, _fake_qa_gateway(payload))


class _FailingRunner(KnowledgeQARunner):
    """A S5.4 runner whose model call blows up (drives the job failure path)."""

    async def run(self, qa_input: Any) -> Any:
        raise RuntimeError("knowledge-qa model unavailable")


# --- route: 202 + RBAC + validation -------------------------------------------


def test_ask_returns_202_job_and_location(client: TestClient) -> None:
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/knowledge/ask",
        json={"question": QUESTION},
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
    assert job["type"] == "knowledge_ask"
    assert job["project_id"] == ACME_ID
    assert job["progress"] == 1.0
    # output_ref is the stable reference; the answer rides the SSE event.
    assert job["output_ref"] == f"knowledge-ask://{ACME_ID}"
    assert job["error"] is None


def test_ask_requires_auth(client: TestClient) -> None:
    assert (
        client.post(f"/api/v1/projects/{ACME_ID}/knowledge/ask", json={"question": "x"}).status_code
        == 401
    )


def test_ask_requires_member_or_above(client: TestClient) -> None:
    # viewer may not start work (§31.3)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/ask",
            json={"question": QUESTION},
            headers=_auth("carol"),
        ).status_code
        == 403
    )
    # non-member of the project
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/ask",
            json={"question": QUESTION},
            headers=_auth("dave"),
        ).status_code
        == 403
    )
    # unknown project: 403, not 404 — no existence leak (§31.3)
    ghost = str(uuid5(NS, "ghost-project"))
    assert (
        client.post(
            f"/api/v1/projects/{ghost}/knowledge/ask",
            json={"question": QUESTION},
            headers=_auth("alice"),
        ).status_code
        == 403
    )


def test_ask_validation(client: TestClient) -> None:
    # blank question → 422 (min_length=1)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/ask",
            json={"question": ""},
            headers=_auth("alice"),
        ).status_code
        == 422
    )
    # missing question → 422 (required)
    assert (
        client.post(
            f"/api/v1/projects/{ACME_ID}/knowledge/ask",
            json={},
            headers=_auth("alice"),
        ).status_code
        == 422
    )


# --- knowledge.answer event over SSE ------------------------------------------


def test_ask_refusal_answer_event_over_sse(client: TestClient) -> None:
    """No-LLM app: the refusal stub still honours the SSE answer contract."""
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/knowledge/ask",
        json={"question": QUESTION},
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    events = _stream_events(client, "alice", f"/api/v1/events?job_id={job_id}")
    names = [name for name, _ in events]
    assert "stage.started" in names
    assert "knowledge.answer" in names
    assert "stage.completed" in names
    assert "job.completed" in names

    answer = next(d for n, d in events if n == "knowledge.answer")
    assert answer["in_scope"] is False
    assert not answer["answer"]
    assert answer["citations"] == []
    assert answer["confidence"] == 0.0


# --- job agent: grounded answer (real S5.4 runner) + failure path ------------


def _drive_agent(
    agent: KnowledgeQARunner,
    engine: Any,
    question: str,
    ai_session_id: str | None = None,
) -> tuple[str | None, list[tuple[str, dict[str, Any]]]]:
    """Run a :class:`KnowledgeAskJobAgent` and capture its emitted events."""
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _emit(event: str, data: dict[str, Any]) -> None:
        captured.append((event, data))

    ctx = JobContext(
        job_id=str(uuid4()),
        project_id=ACME_ID,
        job_type=JobType.KNOWLEDGE_ASK,
        input={"question": question},
        emit=_emit,
        ai_session_id=ai_session_id,
    )

    async def _go() -> str | None:
        return await KnowledgeAskJobAgent(agent, engine).run(ctx)

    output_ref = asyncio.run(_go())
    return output_ref, captured


def test_ask_agent_grounds_answer_with_citations(env: dict[str, Any], tmp_path: Path) -> None:
    """S5.5: retrieve S5.3 chunks, ground via S5.4, emit a rich
    ``knowledge.answer`` with citations carrying ``document_ref`` /
    ``source_type`` / ``title`` / ``score``."""
    repo = _make_repo(tmp_path)
    _seed_requirement(env, ACME_ID)
    _index_corpus(env, repo)

    # Ground the fake model's citation in a *real* search hit so the job agent
    # enriches it with the hit's ``source_type`` and ``score``.
    with _session(env) as session:
        result = search_project_knowledge(session, ACME_ID, QUESTION, top_k=5)
        hit = result.hits[0]
    doc_ref = hit.chunk.document_ref
    title = hit.chunk.title
    source_type = hit.chunk.source_type.value
    score = hit.score

    output_ref, events = _drive_agent(_grounded_agent(doc_ref, title), env["engine"], QUESTION)

    names = [name for name, _ in events]
    assert names == [
        "stage.started",
        "progress",
        "progress",
        "knowledge.answer",
        "progress",
        "stage.completed",
    ]

    answer = next(d for n, d in events if n == "knowledge.answer")
    assert answer["in_scope"] is True
    assert answer["answer"]
    assert answer["confidence"] == 0.9
    assert len(answer["citations"]) == 1
    cite = answer["citations"][0]
    assert cite["document_ref"] == doc_ref
    assert cite["source_type"] == source_type
    assert cite["title"] == title
    assert cite["score"] == score
    assert cite["score"] > 0

    # Stable reference — the full answer rides the SSE event, not this.
    assert output_ref == f"knowledge-ask://{ACME_ID}"


def test_ask_agent_refusal_payload(env: dict[str, Any], tmp_path: Path) -> None:
    """Even with an empty corpus the contract holds: a valid refusal payload."""
    # No corpus indexed for this project → no hits → the runner refuses.
    _grounded_payload: dict[str, object] = {
        "in_scope": False,
        "answer": None,
        "citations": [],
        "confidence": 0.0,
    }
    store = InMemoryPromptStore([_KNOWLEDGE_QA_PROMPT])
    agent = KnowledgeQAAgent(store, _fake_qa_gateway(_grounded_payload))

    output_ref, events = _drive_agent(agent, env["engine"], QUESTION)
    answer = next(d for n, d in events if n == "knowledge.answer")
    assert answer["in_scope"] is False
    assert not answer["answer"]
    assert answer["citations"] == []
    assert answer["confidence"] == 0.0
    assert output_ref == f"knowledge-ask://{ACME_ID}"


def test_ask_runner_failure_fails_job(client: TestClient, env: dict[str, Any]) -> None:
    """A runner that raises → the job lands in ``failed`` with an error (§31.7)."""
    # Swap in a failing runner (the app was built with the no-LLM refusal stub).
    env["app"].state.jobs_knowledge_ask_agent = KnowledgeAskJobAgent(
        _FailingRunner(), env["engine"]
    )
    r = client.post(
        f"/api/v1/projects/{ACME_ID}/knowledge/ask",
        json={"question": QUESTION},
        headers=_auth("alice"),
    )
    assert r.status_code == 202, r.text
    job = _wait_terminal(client, "alice", r.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error"]
    assert "model unavailable" in job["error"]
