"""Shared pydantic base for domain entities (build bible §10)."""

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Base class for all domain entities.

    - ``extra="forbid"``: unknown fields fail fast instead of silently
      persisting — important for schema-validated AI outputs (build bible §12).
    - ``from_attributes=True``: entities can later be built from ORM objects
      (S0.5) without extra conversion.
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)
