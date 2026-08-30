"""Deterministic lexical retrieval (BM25) over knowledge chunks (build bible §14).

The local LLM is completion-only (no embeddings endpoint — see the §19 S5.0
note), so Phase 5 retrieval is lexical and **deterministic**: the same corpus
plus the same query always yields the same ranked hits. When an embedding
provider is wired in (S5.2), lexical search remains the fallback path.

Public contract: ``KnowledgeIndex.search`` returns at most ``MAX_TOP_K``
hits (build bible §14: "top-k ≤ 5 chunks, hard-truncated to the agent
budget"). ``LexicalIndex.search`` is the uncapped primitive underneath.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from .chunking import DEFAULT_MAX_CHARS, chunk_document
from .models import (
    IndexReport,
    KnowledgeChunk,
    KnowledgeDocument,
    SearchHit,
    SearchResult,
)

#: Build bible §14: top-k ≤ 5 chunks, hard-truncated to agent budget.
MAX_TOP_K = 5

_TOKEN_RE = re.compile(r"[a-z0-9]+")

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens (deterministic, stemmer-free)."""
    return _TOKEN_RE.findall(text.lower())


class LexicalIndex:
    """Incremental BM25 index over :class:`KnowledgeChunk` objects."""

    def __init__(self, *, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        self._k1 = k1
        self._b = b
        self._chunks: list[KnowledgeChunk] = []
        self._tf: list[dict[str, int]] = []
        self._doc_len: list[int] = []
        self._df: dict[str, int] = {}

    def add(self, chunk: KnowledgeChunk) -> None:
        tokens = tokenize(chunk.content)
        tf: dict[str, int] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0) + 1
        self._chunks.append(chunk)
        self._tf.append(tf)
        self._doc_len.append(len(tokens))
        for term in tf:
            self._df[term] = self._df.get(term, 0) + 1

    def add_many(self, chunks: Sequence[KnowledgeChunk]) -> None:
        for chunk in chunks:
            self.add(chunk)

    def __len__(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> list[KnowledgeChunk]:
        """Chunks in insertion order (deterministic)."""
        return list(self._chunks)

    def search(self, query: str) -> list[SearchHit]:
        """All positive-scoring hits, ranked, deterministic (no top-k cap here).

        Ties break on chunk id so the ordering is stable across processes.
        """
        query_terms = list(dict.fromkeys(tokenize(query)))
        n = len(self._chunks)
        if n == 0 or not query_terms:
            return []
        avgdl = sum(self._doc_len) / n if sum(self._doc_len) else 1.0
        scored: list[tuple[float, KnowledgeChunk, list[str]]] = []
        for chunk, tf, doc_len in zip(self._chunks, self._tf, self._doc_len, strict=True):
            matched: list[str] = []
            score = 0.0
            for term in query_terms:
                count = tf.get(term)
                if count is None:
                    continue
                matched.append(term)
                df = self._df[term]
                idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
                score += (
                    idf
                    * (count * (self._k1 + 1.0))
                    / (count + self._k1 * (1.0 - self._b + self._b * doc_len / avgdl))
                )
            if score > 0.0:
                scored.append((score, chunk, matched))
        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [
            SearchHit(chunk=chunk, score=score, matched_terms=matched)
            for score, chunk, matched in scored
        ]


class KnowledgeIndex:
    """A corpus of knowledge documents: chunked, indexed, searchable, reportable.

    This is the S5.1 retrieval core (no LLM, no DB): the CLI uses it over a
    local repo, the golden gate over the fixed corpus, and S5.3's API layer
    over the persisted ``knowledge_documents`` rows.
    """

    def __init__(
        self,
        documents: Sequence[KnowledgeDocument],
        *,
        max_chars: int = DEFAULT_MAX_CHARS,
        capped: bool = False,
    ) -> None:
        self.documents: list[KnowledgeDocument] = list(documents)
        self.chunks: list[KnowledgeChunk] = [
            chunk
            for document in self.documents
            for chunk in chunk_document(document, max_chars=max_chars)
        ]
        self.lexical = LexicalIndex()
        self.lexical.add_many(self.chunks)
        breakdown: dict[str, int] = {}
        for document in self.documents:
            breakdown[document.source_type.value] = breakdown.get(document.source_type.value, 0) + 1
        self.report = IndexReport(
            document_count=len(self.documents),
            chunk_count=len(self.chunks),
            source_breakdown=breakdown,
            max_chunk_chars=max((chunk.char_count for chunk in self.chunks), default=0),
            capped=capped,
        )

    def search(self, query: str, *, top_k: int = MAX_TOP_K) -> SearchResult:
        """Rank chunks for *query*; at most ``min(top_k, MAX_TOP_K)`` hits (build bible §14)."""
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        stripped = query.strip()
        if not stripped:
            raise ValueError("query must be non-blank")
        scored = self.lexical.search(stripped)
        limit = min(top_k, MAX_TOP_K)
        return SearchResult(
            query=stripped,
            hits=scored[:limit],
            total_candidates=len(scored),
            truncated=len(scored) > limit,
        )
