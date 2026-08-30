"""Knowledge models (build bible §14 knowledge documents, §19 Phase 5).

Pydantic v2 models on the shared `DomainModel` base (frozen, strict,
`extra="forbid"`) so wire shapes round-trip unchanged between the CLI, the
API layer (S5.3), and the golden set.

The plain wire records (:class:`RunRecord`, :class:`TestOutcomeRecord`) are
DB-agnostic: the API layer (S5.3) fills them from persisted
`test_runs`/`test_results` rows, the CLI (S5.1) from ad-hoc fixtures.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field
from qa_copilot_domain import DomainModel

NonBlankStr = Annotated[str, Field(min_length=1)]


class KnowledgeSourceType(StrEnum):
    """Knowledge source vocabulary (build bible §10 `knowledge_documents.source_type`).

    Wire strings are lowercase so they round-trip unchanged through JSON
    payloads and the `knowledge_documents` VARCHAR column.
    """

    REQUIREMENT = "requirement"
    TEST_CASE = "test_case"
    STANDARD = "standard"
    RUN_HISTORY = "run_history"
    FAILURE = "failure"
    REPOSITORY_FILE = "repository_file"
    DOCUMENT = "document"


class KnowledgeDocument(DomainModel):
    """One unit of project knowledge, ready to be chunked + indexed (build bible §14).

    ``source_ref`` identifies the origin (requirement id, test-case id,
    standard name, run id, relative file path, …) so answers can cite it.
    ``id`` is server-assigned and stays ``None`` until persisted.
    """

    id: str | None = None
    source_type: KnowledgeSourceType
    source_ref: NonBlankStr
    title: NonBlankStr
    content: NonBlankStr
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class KnowledgeChunk(DomainModel):
    """A size-capped slice of one knowledge document (build bible §13: ≤ 600 tokens)."""

    __test__ = False  # noqa: RUF012 - not a pytest class; avoid collection

    id: NonBlankStr
    document_ref: NonBlankStr
    source_type: KnowledgeSourceType
    title: NonBlankStr
    content: NonBlankStr
    chunk_index: int = Field(ge=0)
    char_count: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(DomainModel):
    """One ranked chunk for a query, with its score and matched query terms."""

    chunk: KnowledgeChunk
    score: float
    matched_terms: list[str] = Field(default_factory=list)


class SearchResult(DomainModel):
    """A ranked, hard-truncated answer to a query (build bible §14: top-k ≤ 5)."""

    query: NonBlankStr
    hits: list[SearchHit] = Field(default_factory=list)
    total_candidates: int = Field(ge=0)
    truncated: bool = False


class IndexReport(DomainModel):
    """What an index build produced (counts by source type + chunk-size proof)."""

    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    source_breakdown: dict[str, int] = Field(default_factory=dict)
    max_chunk_chars: int = Field(ge=0)
    capped: bool = False


class TestOutcomeRecord(DomainModel):
    """One test outcome in a run (plain wire shape of `test_results`).

    ``status`` uses the §23 test-result wire strings (passed/failed/flaky/skipped).
    """

    __test__ = False  # noqa: RUF012 - not a pytest class; avoid collection

    test: NonBlankStr
    status: NonBlankStr
    failure_category: str | None = None
    failure_root_cause: str | None = None
    failure_evidence: list[str] = Field(default_factory=list)


class RunRecord(DomainModel):
    """One test-run summary (plain wire shape of `test_runs` + its results)."""

    __test__ = False  # noqa: RUF012 - not a pytest class; avoid collection

    run_id: NonBlankStr
    status: NonBlankStr
    commit_sha: str | None = None
    started_at: datetime | None = None
    results: list[TestOutcomeRecord] = Field(default_factory=list)
