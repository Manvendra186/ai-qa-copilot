"""Retrieval, embeddings, and project memory (build bible §7, §14).

S5.1 (this step): the LLM-free knowledge core — documents, size-capped
chunking (§13), deterministic BM25 lexical retrieval (top-k ≤ 5, §14),
source adapters (requirements, test cases, standards/conventions,
run+failure history, repository files), the golden retrieval gate, and the
CLI. No LLM in the retrieval path (the local endpoint is completion-only);
the embedding seam lands in S5.2.
"""

from .chunking import DEFAULT_MAX_CHARS, DEFAULT_MAX_TOKENS, chunk_document, chunk_text
from .golden import (
    GoldenQueryResult,
    GoldenReport,
    KnowledgeGoldenSetError,
    RetrievalGate,
    RetrievalGoldenSet,
    RetrievalQuery,
    default_golden_path,
    load_golden_set,
    run_golden_set,
)
from .models import (
    IndexReport,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSourceType,
    RunRecord,
    SearchHit,
    SearchResult,
    TestOutcomeRecord,
)
from .search import MAX_TOP_K, KnowledgeIndex, LexicalIndex, tokenize
from .sources import (
    history_documents,
    repository_file_documents,
    requirement_documents,
    standard_documents,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_TOKENS",
    "GoldenQueryResult",
    "GoldenReport",
    "IndexReport",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeGoldenSetError",
    "KnowledgeIndex",
    "KnowledgeSourceType",
    "LexicalIndex",
    "MAX_TOP_K",
    "RetrievalGate",
    "RetrievalGoldenSet",
    "RetrievalQuery",
    "RunRecord",
    "SearchHit",
    "SearchResult",
    "TestOutcomeRecord",
    "chunk_document",
    "chunk_text",
    "default_golden_path",
    "history_documents",
    "load_golden_set",
    "requirement_documents",
    "repository_file_documents",
    "run_golden_set",
    "standard_documents",
    "tokenize",
]
