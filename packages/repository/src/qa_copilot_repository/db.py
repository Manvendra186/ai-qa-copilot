"""Database access for the AI QA Copilot (build bible §10, §19 S0.5).

This module owns database-URL resolution and the SQLAlchemy engine/session
factories. ORM table models live in :mod:`qa_copilot_repository.models`;
Alembic migrations live in ``infra/migrations`` (build bible §7).

URL resolution order (first hit wins):

1. ``DATABASE_URL`` environment variable
2. ``DATABASE_URL`` in a ``.env`` file (current dir, then the repo root)
3. Dev default (matches ``.env.example``)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

#: Dev default — compose db per `.env.example` (qa/qa @ qa_copilot:5432).
DEFAULT_DATABASE_URL = "postgresql+psycopg://qa:qa@localhost:5432/qa_copilot"


def _find_dotenv() -> Path | None:
    """Locate a ``.env`` file: current working dir first, then the repo root."""
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        return cwd_env
    # db.py → qa_copilot_repository → src → repository → packages → repo root
    root_env = Path(__file__).resolve().parents[4] / ".env"
    return root_env if root_env.is_file() else None


def _load_dotenv(path: Path | None) -> None:
    """Minimal stdlib ``.env`` reader.

    Only sets keys that are *not* already in the environment, so real
    environment variables always win. Comments and blank lines are ignored.
    """
    if path is None:
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_database_url() -> str:
    """Resolve the SQLAlchemy database URL (see module docstring for order)."""
    _load_dotenv(_find_dotenv())
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(database_url: str | None = None, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine for the given (or resolved) database URL."""
    url = database_url or get_database_url()
    return create_engine(url, echo=echo, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to *engine*.

    ``expire_on_commit=False`` keeps attributes accessible after commit —
    handy for the 202 + job pattern (S0.9) where rows are returned right
    after being saved.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = make_session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
