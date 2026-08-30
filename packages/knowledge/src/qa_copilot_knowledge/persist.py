"""Embedding persistence — the ``embeddings`` table (build bible §19 S5.2).

The S0.5 schema already has ``knowledge_documents`` + ``embeddings``
(pgvector ``VECTOR(VECTOR_DIM)``, one row per document). This adapter owns
the knowledge package's write/read side of that table:

- :func:`store_document_embedding` — idempotent upsert with fail-loud
  validation (dimension must equal ``VECTOR_DIM``; values must be finite —
  pgvector rejects NaN/inf);
- :func:`load_document_embeddings` — stored vectors back, keyed by document
  id (input to :func:`~qa_copilot_knowledge.hybrid_search`'s vector path
  once S5.3 wires the API);
- :func:`embed_and_store` — provider → table in one call.

The caller owns the session/transaction: commit to persist, or roll back to
keep the dev database clean (the unit tests use the latter).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import sqlalchemy as sa
from qa_copilot_repository import models
from sqlalchemy.orm import Session

from .embeddings import EmbeddingError, EmbeddingProvider


def store_document_embedding(
    session: Session,
    knowledge_document: models.KnowledgeDocument,
    vector: Sequence[float],
) -> models.Embedding:
    """Insert (or update) *knowledge_document*'s embedding row.

    Idempotent: one row per document; re-storing replaces the vector and
    returns the same row.

    Raises:
        EmbeddingError: wrong dimension (not ``VECTOR_DIM``), a non-numeric
            value, or a non-finite value — fail loud, never store a broken
            vector (§9).
    """
    _validate_vector(vector)
    existing: models.Embedding | None = session.scalar(
        sa.select(models.Embedding).where(
            models.Embedding.knowledge_document_id == knowledge_document.id
        )
    )
    if existing is not None:
        existing.vector = list(vector)
        return existing
    row = models.Embedding(knowledge_document_id=knowledge_document.id, vector=list(vector))
    session.add(row)
    session.flush()
    return row


def load_document_embeddings(
    session: Session, document_ids: Sequence[str]
) -> dict[str, list[float]]:
    """Stored vectors for *document_ids*; documents without a row are omitted."""
    if not document_ids:
        return {}
    rows = session.scalars(
        sa.select(models.Embedding).where(
            models.Embedding.knowledge_document_id.in_(list(document_ids))
        )
    )
    return {row.knowledge_document_id: list(row.vector) for row in rows}


def embed_and_store(
    session: Session,
    provider: EmbeddingProvider,
    knowledge_document: models.KnowledgeDocument,
    text: str,
) -> models.Embedding:
    """Embed *text* with *provider* and store it (one convenience call)."""
    embedded = provider.embed([text])
    return store_document_embedding(session, knowledge_document, embedded[0].vector)


def _validate_vector(vector: Sequence[float]) -> None:
    if len(vector) != models.VECTOR_DIM:
        raise EmbeddingError(
            f"vector has {len(vector)} dimension(s); the embeddings table "
            f"expects {models.VECTOR_DIM} (VECTOR_DIM) — use a matching model"
        )
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingError(f"vector contains a non-numeric value ({value!r})")
        if not math.isfinite(value):
            raise EmbeddingError("vector contains a non-finite value (NaN/inf)")


__all__ = [
    "embed_and_store",
    "load_document_embeddings",
    "store_document_embedding",
]
