"""S5.1 lexical search tests: BM25 ranking, top-k cap (§14), determinism.

All tests are hermetic: the retrieval path contains no LLM and no clock
(build bible §19 S5.1), so results must be a pure function of corpus + query.
"""

from __future__ import annotations

import pytest
from qa_copilot_knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSourceType
from qa_copilot_knowledge.search import MAX_TOP_K, KnowledgeIndex, LexicalIndex, tokenize


def _document(ref: str, title: str, content: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        source_type=KnowledgeSourceType.DOCUMENT,
        source_ref=ref,
        title=title,
        content=content,
    )


CORPUS = [
    _document("A", "Alpha", "The alpha service renders the dashboard widgets."),
    _document("B", "Beta", "The beta service renders the billing invoices."),
    _document("C", "Gamma", "Gamma handles the alpha service retries and backoff."),
]


class TestTokenize:
    def test_lowercase_alnum_tokens(self) -> None:
        assert tokenize("GetByTestId, order-2026! CSV") == ["getbytestid", "order", "2026", "csv"]

    def test_empty_and_punctuation_only(self) -> None:
        assert tokenize("") == []
        assert tokenize("!!! ???") == []


class TestLexicalIndex:
    def test_relevant_document_ranks_first(self) -> None:
        index = LexicalIndex()
        index.add_many([c for doc in CORPUS for c in _chunks_of(doc)])
        hits = index.search("dashboard widgets")
        assert [h.chunk.document_ref for h in hits][0] == "A"

    def test_unmatched_query_has_no_hits(self) -> None:
        index = LexicalIndex()
        index.add_many(_chunks_of(CORPUS[0]))
        assert index.search("quantum flux capacitor") == []

    def test_empty_index_has_no_hits(self) -> None:
        assert LexicalIndex().search("anything") == []

    def test_empty_query_has_no_hits(self) -> None:
        index = LexicalIndex()
        index.add_many(_chunks_of(CORPUS[0]))
        assert index.search("   ") == []

    def test_repeated_term_beats_single_mention(self) -> None:
        index = LexicalIndex()
        index.add(_chunk("RARE", "checkout gateway timeout retry"))
        index.add(_chunk("HEAVY", "gateway timeout retry gateway timeout retry gateway"))
        hits = index.search("gateway timeout")
        assert [h.chunk.document_ref for h in hits][0] == "HEAVY"

    def test_matched_terms_are_reported(self) -> None:
        index = LexicalIndex()
        index.add(_chunk("X", "billing invoices for enterprise"))
        hits = index.search("enterprise billing")
        assert set(hits[0].matched_terms) == {"enterprise", "billing"}


class TestKnowledgeIndex:
    def test_search_caps_at_max_top_k(self) -> None:
        docs = [
            _document(f"D{i:02d}", f"Doc {i}", f"shared term number {i} shared") for i in range(12)
        ]
        index = KnowledgeIndex(docs)
        result = index.search("shared term", top_k=50)
        assert len(result.hits) <= MAX_TOP_K == 5
        assert result.truncated

    def test_requested_smaller_top_k_is_honored(self) -> None:
        index = KnowledgeIndex(CORPUS)
        assert len(index.search("renders", top_k=2).hits) == 2

    def test_invalid_top_k_raises(self) -> None:
        index = KnowledgeIndex(CORPUS)
        with pytest.raises(ValueError, match="top_k"):
            index.search("renders", top_k=0)

    def test_blank_query_raises(self) -> None:
        index = KnowledgeIndex(CORPUS)
        with pytest.raises(ValueError, match="non-blank"):
            index.search("   ")

    def test_blank_query_is_stripped_in_result(self) -> None:
        index = KnowledgeIndex(CORPUS)
        result = index.search("  dashboard  ")
        assert result.query == "dashboard"

    def test_search_is_deterministic(self) -> None:
        index = KnowledgeIndex(CORPUS)
        a = index.search("renders the alpha service")
        b = index.search("renders the alpha service")
        assert a.model_dump_json() == b.model_dump_json()

    def test_ties_break_by_chunk_id(self) -> None:
        docs = [
            _document("B", "B", "identical content here"),
            _document("A", "A", "identical content here"),
        ]
        index = KnowledgeIndex(docs)
        result = index.search("identical content")
        assert {h.chunk.document_ref for h in result.hits} == {"A", "B"}
        # equal scores must order deterministically by chunk id
        assert [h.chunk.id for h in result.hits] == sorted(h.chunk.id for h in result.hits)

    def test_report_counts(self) -> None:
        index = KnowledgeIndex(CORPUS)
        report = index.report
        assert report.document_count == 3
        assert report.chunk_count == len(index.chunks)
        assert report.source_breakdown == {"document": 3}
        assert report.max_chunk_chars == max(c.char_count for c in index.chunks)
        assert report.capped is False

    def test_report_reflects_capped_flag(self) -> None:
        index = KnowledgeIndex(CORPUS, capped=True)
        assert index.report.capped is True


def _chunk(ref: str, content: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        id=f"chunk-{ref}",
        document_ref=ref,
        source_type=KnowledgeSourceType.DOCUMENT,
        title=ref,
        content=content,
        chunk_index=0,
        char_count=len(content),
    )


def _chunks_of(document: KnowledgeDocument) -> list[KnowledgeChunk]:
    from qa_copilot_knowledge.chunking import chunk_document

    return chunk_document(document)
