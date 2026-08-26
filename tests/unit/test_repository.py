"""Repository package tests (S0.5): URL resolution + ORM model registration.

The database smoke test at the bottom connects to the dev database (per
``DATABASE_URL`` / ``.env``) and is *skipped* when no database is
reachable, so ``pytest`` stays green without docker.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from qa_copilot_repository import db, models

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
    engine = _engine_or_skip()
    if engine is None:
        pytest.skip("dev database not reachable (docker compose up -d?)")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT vector_dims(vector) FROM embeddings LIMIT 1")
            ).scalar_one_or_none()
        assert row == models.VECTOR_DIM
    finally:
        engine.dispose()
