"""Repository package tests (S0.5): URL resolution + ORM model registration.

The database smoke test at the bottom connects to the dev database (per
``DATABASE_URL`` / ``.env``) and is *skipped* when no database is
reachable, so ``pytest`` stays green without docker.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from qa_copilot_ai import AICallResult, PromptNotFound, TokenUsage
from qa_copilot_knowledge.persist import load_document_embeddings, store_document_embedding
from qa_copilot_repository import audit, db, models, prompts

# --- URL resolution ---------------------------------------------------------


def test_get_database_url_env_var_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example:1234/qa")
    assert db.get_database_url() == "postgresql+psycopg://u:p@db.example:1234/qa"


def test_get_database_url_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "_find_dotenv", lambda: None)
    assert db.get_database_url() == db.DEFAULT_DATABASE_URL


def test_make_engine_uses_resolved_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db.example:1234/qa")
    engine = db.make_engine()
    assert engine.url.database == "qa"
    engine.dispose()


# --- Model registration ------------------------------------------------------


EXPECTED_TABLES = {
    "organizations",
    "users",
    "repositories",
    "projects",
    "project_members",
    "files",
    "requirements",
    "test_cases",
    "requirement_test_cases",
    "test_runs",
    "test_results",
    "failures",
    "artifacts",
    "knowledge_documents",
    "embeddings",
    "ai_sessions",
    "ai_actions",
    "jobs",
    "prompt_versions",
    "generated_tests",
    "integration_configs",
}


def test_all_core_tables_registered() -> None:
    assert set(models.Base.metadata.tables) == EXPECTED_TABLES


def test_mapper_configuration_succeeds() -> None:
    # Forces resolution of every relationship/secondary — catches broken
    # back_populates / secondary wiring without touching a database.
    models.Base.registry.configure()


def test_metadata_column_named_metadata_attribute_metadata_underscore() -> None:
    for table in (models.Artifact.__table__, models.KnowledgeDocument.__table__):
        assert "metadata" in table.columns
        assert "metadata_" not in table.columns


def test_enum_columns_are_plain_varchar() -> None:
    """Enum values live in qa_copilot_domain; the DB stores plain VARCHAR."""
    col = models.Requirement.__table__.c["risk"]
    assert isinstance(col.type, sa.String)


def test_project_members_role_is_plain_varchar() -> None:
    """S0.8: project-scoped role (§31.3) also stores the domain wire string."""
    col = models.ProjectMember.__table__.c["role"]
    assert isinstance(col.type, sa.String)
    pk = [c.name for c in models.ProjectMember.__table__.primary_key]
    assert pk == ["project_id", "user_id"]


def test_embedding_column_is_pgvector() -> None:
    import pgvector.sqlalchemy

    col = models.Embedding.__table__.c["vector"]
    assert isinstance(col.type, pgvector.sqlalchemy.Vector)


# --- Optional database smoke test -------------------------------------------


def _engine_or_skip() -> sa.Engine | None:
    engine = sa.create_engine(
        db.get_database_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    try:
        with engine.connect():
            pass
    except sa.exc.OperationalError:
        engine.dispose()
        return None
    return engine


def test_db_smoke_seed_rows_present() -> None:
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    try:
        with engine.connect() as conn:
            orgs = conn.execute(sa.text("SELECT count(*) FROM organizations")).scalar_one()
            tables = sa.inspect(engine).get_table_names()
        assert orgs >= 1
        assert EXPECTED_TABLES <= set(tables)
    finally:
        engine.dispose()


def test_db_smoke_vector_roundtrip() -> None:
    """pgvector round-trip: store a VECTOR_DIM vector, read it back.

    Self-contained (S5.2 guarded-persistence pattern): one temporary
    org/project/document/embedding chain inside a transaction that is
    rolled back, so the smoke test does not depend on seeded data.
    """
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    session = db.make_session_factory(engine)()
    try:
        org_id, project_id, doc_id = (str(uuid.uuid4()) for _ in range(3))
        conn = session.connection()
        conn.execute(
            sa.insert(models.Organization),
            {"id": org_id, "name": "s05-vector-smoke", "plan": "dev"},
        )
        conn.execute(
            sa.insert(models.Project),
            {
                "id": project_id,
                "organization_id": org_id,
                "name": "s05-vector-smoke",
                "settings": {},
            },
        )
        conn.execute(
            sa.insert(models.KnowledgeDocument),
            {
                "id": doc_id,
                "project_id": project_id,
                "source_type": "standard",
                "source_ref": "s05-vector-smoke",
                "content": "S0.5 vector smoke test document",
                "metadata": {},
            },
        )
        document = session.get(models.KnowledgeDocument, doc_id)
        assert document is not None
        vector = [0.01 * (i + 1) for i in range(models.VECTOR_DIM)]
        store_document_embedding(session, document, vector)
        dims = session.execute(
            sa.text("SELECT vector_dims(vector) FROM embeddings WHERE knowledge_document_id = :id"),
            {"id": doc_id},
        ).scalar_one()
        assert dims == models.VECTOR_DIM
        # pgvector stores float4, so compare with a small tolerance.
        loaded = load_document_embeddings(session, [doc_id])[doc_id]
        assert len(loaded) == models.VECTOR_DIM
        assert all(abs(a - b) < 1e-5 for a, b in zip(loaded, vector, strict=True))
    finally:
        session.rollback()
        session.close()
        engine.dispose()


# --- S0.6: prompt registry + ai_actions (build bible §31.1, §31.6) ------------


def test_load_prompt_seeded_requirement_analyst() -> None:
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    try:
        with db.session_scope(engine) as session:
            spec = prompts.load_prompt(session, "requirement-analyst")
        assert spec.version >= 1
        assert spec.body
        assert spec.ref.startswith("requirement-analyst@")
    finally:
        engine.dispose()


def test_load_prompt_missing_raises_prompt_not_found() -> None:
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    try:
        with db.session_scope(engine) as session:
            with pytest.raises(PromptNotFound):
                prompts.load_prompt(session, "no-such-prompt")
    finally:
        engine.dispose()


def test_record_ai_call_writes_action_row() -> None:
    """Token accounting → ``ai_actions`` (build bible §31.1)."""
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    try:
        with db.session_scope(engine) as session:
            project = session.scalars(
                sa.select(models.Project).where(models.Project.name == "Demo App")
            ).first()
            assert project is not None
            session_row = models.AISession(project_id=project.id, task_type="requirement_analysis")
            session.add(session_row)
            session.flush()
            result = AICallResult(
                agent="unit-test",
                model="fake-model",
                text="ok",
                usage=TokenUsage(tokens_in=10, tokens_out=5),
                latency_ms=42,
                redactions=0,
                retries=0,
                input_hash="0" * 64,
            )
            action = audit.record_ai_call(session, session_id=session_row.id, result=result)
        with db.session_scope(engine) as session:
            row = session.scalars(
                sa.select(models.AIAction).where(models.AIAction.id == action.id)
            ).first()
            assert row is not None
            assert row.tokens_in == 10
            assert row.tokens_out == 5
            assert row.latency_ms == 42
            assert row.agent == "unit-test"
            assert row.model == "fake-model"
    finally:
        engine.dispose()


# --- S1.3: suite persistence (build bible §10, §12; §19 S1.3) -----------------


def test_persist_requirement_with_suite_writes_rows_and_join() -> None:
    """S1.3: persist the pure ``TestSuite`` → requirement + test-cases + §10 M:N join.

    Drives :func:`qa_copilot_repository.requirements.persist_requirement_with_suite`
    against the dev database: one ``requirements`` row, one ``test_cases`` row per
    suite case, and the ``requirement_test_cases`` join rows linking them.
    """
    from qa_copilot_ai import TestCase as AITestCase
    from qa_copilot_ai import TestSuite
    from qa_copilot_domain.enums import Priority, RiskLevel, TestType
    from qa_copilot_repository import requirements as repo_requirements

    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    suite = TestSuite(
        test_cases=[
            AITestCase(
                id="TC-001",
                title="valid login succeeds",
                type="functional",
                priority="high",
                preconditions=["a registered user exists"],
                steps=["enter email + password", "submit"],
                expected_results=["redirected to /home"],
                risk="high",
            ),
            AITestCase(
                id="TC-002",
                title="empty password rejected",
                type="negative",
                priority="medium",
                steps=["submit an empty password"],
                expected_results=["a validation error is shown"],
                risk="medium",
            ),
            AITestCase(
                id="TC-003",
                title="password length boundary",
                type="boundary",
                priority="low",
                steps=["submit a 72-character password"],
                expected_results=["accepted or rejected at the documented limit"],
                risk="low",
            ),
        ]
    )
    try:
        with db.session_scope(engine) as session:
            org = models.Organization(name="S1.3 persistence test org")
            session.add(org)
            session.flush()
            project = models.Project(name="S1.3 persistence test project", organization_id=org.id)
            session.add(project)
            session.flush()
            persisted = repo_requirements.persist_requirement_with_suite(
                session,
                project_id=project.id,
                title="Login flow",
                content="Users can log in with email + password and are redirected to /home.",
                acceptance_criteria=["valid credentials succeed", "invalid credentials fail"],
                suite=suite,
            )

        # A *fresh* scope proves the rows were committed, not merely flushed.
        with db.session_scope(engine) as session:
            requirement = session.get(models.Requirement, persisted.requirement_id)
            assert requirement is not None
            assert requirement.project_id == project.id
            assert requirement.title == "Login flow"
            assert requirement.acceptance_criteria == [
                "valid credentials succeed",
                "invalid credentials fail",
            ]

            assert len(persisted.test_case_ids) == 3
            cases = {
                case.title: case
                for case in session.scalars(
                    sa.select(models.TestCase).where(
                        models.TestCase.id.in_(persisted.test_case_ids)
                    )
                ).all()
            }
            assert set(cases) == {
                "valid login succeeds",
                "empty password rejected",
                "password length boundary",
            }
            # Enum round-trip: the stored wire strings come back as domain enums.
            assert cases["valid login succeeds"].type is TestType.FUNCTIONAL
            assert cases["valid login succeeds"].priority is Priority.HIGH
            assert cases["valid login succeeds"].risk is RiskLevel.HIGH
            assert cases["valid login succeeds"].steps == ["enter email + password", "submit"]
            assert cases["valid login succeeds"].preconditions == ["a registered user exists"]
            assert cases["empty password rejected"].type is TestType.NEGATIVE
            assert cases["password length boundary"].type is TestType.BOUNDARY

            # The §10 M:N join: all three cases are linked to exactly this requirement.
            join_count = session.execute(
                sa.text("SELECT count(*) FROM requirement_test_cases WHERE requirement_id = :rid"),
                {"rid": persisted.requirement_id},
            ).scalar_one()
            assert join_count == 3
    finally:
        engine.dispose()


def test_persist_run_writes_run_result_and_artifact_rows() -> None:
    """S3.1: the execution worker's RunReport lands as §10 test_runs/test_results/artifacts."""
    from qa_copilot_domain.enums import ArtifactType, RunStatus, TestResultStatus
    from qa_copilot_execution.report import (
        ArtifactReport,
        RunReport,
        RunTotals,
        TestResultReport,
    )
    from qa_copilot_repository import runs as repo_runs

    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    slug = "demo-login-products-signs-in-and-sees-the-product-catalog"
    report = RunReport(
        status=RunStatus.COMPLETED,
        target_dir="/tmp/demo",
        started_at="2026-08-28T00:00:00.000+00:00",
        completed_at="2026-08-28T00:00:08.500+00:00",
        duration_ms=8500,
        totals=RunTotals(total=1, passed=1),
        results=[
            TestResultReport(
                title="signs in and sees the product catalog",
                file="demo.spec.js",
                status=TestResultStatus.PASSED,
                duration_ms=450,
                slug=slug,
                artifacts=[
                    ArtifactReport(
                        type=ArtifactType.VIDEO,
                        uri=f"runs/s31-test/{slug}/video",
                        metadata={"size_bytes": 67633},
                    ),
                    ArtifactReport(
                        type=ArtifactType.SCREENSHOT,
                        uri=f"runs/s31-test/{slug}/screenshot",
                    ),
                ],
            )
        ],
    )
    try:
        with db.session_scope(engine) as session:
            org = models.Organization(name="S3.1 persistence test org")
            session.add(org)
            session.flush()
            project = models.Project(name="S3.1 persistence test project", organization_id=org.id)
            session.add(project)
            session.flush()
            persisted = repo_runs.persist_run(session, project_id=project.id, report=report)

        with db.session_scope(engine) as session:
            run = repo_runs.get_run(session, persisted.id)
            assert run is not None
            assert run.project_id == project.id
            assert run.status == RunStatus.COMPLETED
            assert run.started_at is not None

            results = list(repo_runs.list_results(session, persisted.id))
            assert len(results) == 1
            assert results[0].status == TestResultStatus.PASSED
            assert results[0].duration == pytest.approx(0.45)

            artifacts = list(repo_runs.list_artifacts(session, persisted.id))
            assert {a.type for a in artifacts} == {ArtifactType.VIDEO, ArtifactType.SCREENSHOT}
            video = next(a for a in artifacts if a.type is ArtifactType.VIDEO)
            assert video.uri == f"runs/s31-test/{slug}/video"
            assert video.metadata_ == {"size_bytes": 67633}
    finally:
        engine.dispose()
