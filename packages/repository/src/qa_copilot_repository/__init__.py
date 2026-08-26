"""Data access + Git, AST, and file indexing (build bible §7).

S0.5: SQLAlchemy 2.0 ORM models for the §10 core data model
(:mod:`qa_copilot_repository.models`) and engine/session factories
(:mod:`qa_copilot_repository.db`). Alembic migrations live in
``infra/migrations`` (repo root).

S0.6: prompt-registry loader (:mod:`qa_copilot_repository.prompts`, §31.6)
and ``ai_actions`` audit recorder (:mod:`qa_copilot_repository.audit`, §31.1).
"""

from . import audit, db, models, prompts

__version__ = "0.1.0"

__all__ = ["__version__", "audit", "db", "models", "prompts"]
