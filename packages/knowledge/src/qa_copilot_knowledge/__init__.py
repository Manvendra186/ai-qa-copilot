"""Retrieval, embeddings, and project memory (build bible §7, §14).

S5.1: the LLM-free knowledge core — documents, size-capped chunking (§13),
deterministic BM25 lexical retrieval (top-k ≤ 5, §14), source adapters
(requirements, test cases, standards/conventions, run+failure history,
repository files), the golden retrieval gate, and the CLI.

S5.2 (this step): the embedding seam — :class:`EmbeddingProvider` protocol
+ :class:`OpenAICompatibleEmbeddingProvider` (OpenAI-compatible
``/embeddings`` endpoint), vector retrieval with graceful lexical fallback
when the endpoint is unavailable (501 — the local completion-only LLM), and
persistence to the ``embeddings`` table (pgvector, ``VECTOR_DIM``).
"""

from .chunking import DEFAULT_MAX_CHARS, DEFAULT_MAX_TOKENS, chunk_document, chunk_text
from .embeddings import (
    DEFAULT_EMBED_CONNECT_TIMEOUT_S,
    DEFAULT_EMBED_TIMEOUT_S,
    DEFAULT_MAX_RETRIES,
    UNAVAILABLE_STATUSES,
    EmbeddingError,
    EmbeddingProvider,
    EmbeddingUnavailable,
    EmbeddingVector,
    OpenAICompatibleEmbeddingProvider,
    cosine_similarity,
    parse_embedding_response,
)
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
from .hybrid import HybridSearchResult, hybrid_search, vector_search
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
from .persist import embed_and_store, load_document_embeddings, store_document_embedding
from .qa_golden import (
    QAExpectations,
    QAGate,
    QAGoldenSet,
    QAGoldenSetError,
    QAQuestion,
    default_qa_golden_path,
    load_qa_golden_set,
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
    "DEFAULT_EMBED_CONNECT_TIMEOUT_S",
    "DEFAULT_EMBED_TIMEOUT_S",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MAX_TOKENS",
    "EmbeddingError",
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "EmbeddingVector",
    "GoldenQueryResult",
    "GoldenReport",
    "HybridSearchResult",
    "IndexReport",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "KnowledgeGoldenSetError",
    "KnowledgeIndex",
    "KnowledgeSourceType",
    "LexicalIndex",
    "MAX_TOP_K",
    "OpenAICompatibleEmbeddingProvider",
    "QAGate",
    "QAExpectations",
    "QAQuestion",
    "QAGoldenSet",
    "QAGoldenSetError",
    "RetrievalGate",
    "RetrievalGoldenSet",
    "RetrievalQuery",
    "RunRecord",
    "SearchHit",
    "SearchResult",
    "TestOutcomeRecord",
    "UNAVAILABLE_STATUSES",
    "chunk_document",
    "chunk_text",
    "cosine_similarity",
    "default_golden_path",
    "default_qa_golden_path",
    "embed_and_store",
    "history_documents",
    "hybrid_search",
    "load_document_embeddings",
    "load_golden_set",
    "load_qa_golden_set",
    "parse_embedding_response",
    "requirement_documents",
    "repository_file_documents",
    "run_golden_set",
    "standard_documents",
    "store_document_embedding",
    "tokenize",
    "vector_search",
]
