"""Vector retrieval + graceful lexical fallback (build bible §19 S5.2).

S5.1's lexical path (:mod:`~qa_copilot_knowledge.search`) is the
golden-gated baseline and stays **unchanged**. This module adds the upgrade
path: when an :class:`~qa_copilot_knowledge.embeddings.EmbeddingProvider`
is available and the corpus has stored vectors, :func:`hybrid_search` ranks
chunks by cosine similarity; when the endpoint is unavailable (501 — the
local completion-only LLM's exact behavior, §19 S5.0) it falls back to the
identical lexical result, gracefully.

Architecture mirrors S5.1: :func:`vector_search` is the uncapped ranking
primitive (like ``LexicalIndex.search``); :func:`hybrid_search` applies the
§14 top-k ≤ 5 cap (like ``KnowledgeIndex.search``) and reports which mode
produced the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from qa_copilot_domain import DomainModel

from .embeddings import EmbeddingProvider, EmbeddingUnavailable, cosine_similarity
from .models import KnowledgeChunk, SearchHit, SearchResult
from .search import MAX_TOP_K, KnowledgeIndex


class HybridSearchResult(DomainModel):
    """A search result plus the mode that produced it (S5.2).

    ``result`` is the usual capped :class:`SearchResult` (top-k ≤ 5, §14) —
    on the lexical fallback it is **bit-for-bit** the S5.1 result, so
    consumers can treat both modes identically.
    """

    mode: Literal["lexical", "vector"]
    #: The embedding model id (vector mode) — ``None`` in lexical mode.
    provider: str | None = None
    result: SearchResult


def vector_search(
    chunks: Sequence[KnowledgeChunk],
    vectors: Mapping[str, Sequence[float]],
    query_vector: Sequence[float],
) -> list[SearchHit]:
    """Rank *chunks* that have a stored vector by cosine to *query_vector*.

    Uncapped primitive (the S5.2 mirror of ``LexicalIndex.search``): every
    chunk with a vector scores; deterministic ordering (score desc → chunk
    id). Chunks without a vector are skipped. ``matched_terms`` is empty —
    vector ranking has no lexical terms to cite.

    Raises:
        ValueError: a stored vector's dimension differs from the query
            vector's (a model mismatch — fail loud, never a fallback).
    """
    scored: list[tuple[float, KnowledgeChunk]] = []
    for chunk in chunks:
        vector = vectors.get(chunk.id)
        if vector is None:
            continue
        scored.append((cosine_similarity(query_vector, vector), chunk))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [SearchHit(chunk=chunk, score=score, matched_terms=[]) for score, chunk in scored]


def hybrid_search(
    index: KnowledgeIndex,
    query: str,
    *,
    provider: EmbeddingProvider | None = None,
    vectors: Mapping[str, Sequence[float]] | None = None,
    top_k: int = MAX_TOP_K,
) -> HybridSearchResult:
    """Search with vector retrieval when possible, lexical otherwise (S5.2).

    - No provider (or no usable stored vectors) → the S5.1 lexical result,
      mode ``"lexical"`` — the path is unchanged and stays the baseline.
    - Provider available → the query is embedded and chunks are ranked by
      cosine, mode ``"vector"`` (top-k ≤ 5, §14).
    - :class:`~qa_copilot_knowledge.embeddings.EmbeddingUnavailable`
      (501/503/unreachable) → **graceful** fallback to the lexical result —
      swallowed by design (build bible §19 S5.2).
    - Any other :class:`~qa_copilot_knowledge.embeddings.EmbeddingError`
      (bad status, malformed payload) or a dimension mismatch propagates —
      a real fault must fail loud.

    Raises:
        ValueError: blank *query* or ``top_k < 1``.
    """
    stripped = query.strip()
    if not stripped:
        raise ValueError("query must be non-blank")
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    limit = min(top_k, MAX_TOP_K)
    if provider is not None and vectors:
        try:
            embedded = provider.embed([stripped])
        except EmbeddingUnavailable:
            # Graceful fallback (build bible §19 S5.2): the endpoint is 501/503
            # or unreachable — keep the unchanged S5.1 lexical path.
            return _lexical(index, stripped, limit)
        hits = vector_search(index.chunks, vectors, embedded[0].vector)
        if hits:
            return HybridSearchResult(
                mode="vector",
                provider=provider.model,
                result=SearchResult(
                    query=stripped,
                    hits=hits[:limit],
                    total_candidates=len(hits),
                    truncated=len(hits) > limit,
                ),
            )
    return _lexical(index, stripped, limit)


def _lexical(index: KnowledgeIndex, query: str, limit: int) -> HybridSearchResult:
    """The unchanged S5.1 lexical path, wrapped in a mode-tagged result."""
    return HybridSearchResult(
        mode="lexical",
        provider=None,
        result=index.search(query, top_k=limit),
    )


__all__ = ["HybridSearchResult", "hybrid_search", "vector_search"]
