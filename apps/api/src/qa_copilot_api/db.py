"""Database engine + session dependency for the FastAPI app (S0.8).

URL resolution lives in ``qa_copilot_repository.db`` (env → ``.env`` → dev
default); this module only adapts it to FastAPI dependency injection: one
engine per app (stored on ``app.state.engine``) and one session per request.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from qa_copilot_repository import db as repo_db
from sqlalchemy import Engine
from sqlalchemy.orm import Session

__all__ = ["get_db", "make_app_engine"]


def make_app_engine(database_url: str | None = None) -> Engine:
    """One engine per app; *database_url* overrides env/``.env`` resolution."""
    return repo_db.make_engine(database_url)


def get_db(request: Request) -> Iterator[Session]:
    """Yield a session bound to the app's engine (closed after the request).

    Callers commit explicitly; the session is closed in ``finally`` no
    matter how the request ends.
    """
    engine: Engine = request.app.state.engine
    session = repo_db.make_session_factory(engine)()
    try:
        yield session
    finally:
        session.close()
