"""S5.1 chunking tests: size cap (§13), determinism, content preservation.

All tests are hermetic (no LLM, no network, no clock): chunking must be a
pure function of its input (build bible §19 S5.1).
"""

from __future__ import annotations

import pytest
from qa_copilot_knowledge.chunking import (
    DEFAULT_CHARS_PER_TOKEN,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_TOKENS,
    chunk_document,
    chunk_text,
)
from qa_copilot_knowledge.models import KnowledgeDocument, KnowledgeSourceType


def _document(content: str, ref: str = "DOC-1") -> KnowledgeDocument:
    return KnowledgeDocument(
        source_type=KnowledgeSourceType.DOCUMENT,
        source_ref=ref,
        title="Doc",
        content=content,
    )


def _normalized(text: str) -> str:
    return " ".join(text.split())


class TestChunkText:
    def test_short_text_is_a_single_chunk(self) -> None:
        assert chunk_text("hello world", max_chars=2400) == ["hello world"]

    def test_every_chunk_respects_the_char_cap(self) -> None:
        text = "\n".join(f"line {i} with some words to chunk" for i in range(200))
        chunks = chunk_text(text, max_chars=80)
        assert chunks
        assert all(len(chunk) <= 80 for chunk in chunks)

    def test_content_is_preserved(self) -> None:
        text = "alpha\n\nbeta gamma delta\nepsilon"
        chunks = chunk_text(text, max_chars=16)
        assert chunks == ["alpha", "beta gamma delta", "epsilon"]
        joined = "\n".join(chunks)
        assert _normalized(joined) == _normalized(text)

    def test_deterministic_across_calls(self) -> None:
        text = "\n\n".join(f"block {i}: " + "word " * 20 for i in range(10))
        assert chunk_text(text) == chunk_text(text)

    def test_empty_and_whitespace_only_input(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\n  \t ") == []

    def test_long_single_line_is_hard_cut(self) -> None:
        text = "x" * 50
        chunks = chunk_text(text, max_chars=20)
        assert chunks == ["x" * 20, "x" * 20, "x" * 10]
        assert all(len(chunk) <= 20 for chunk in chunks)

    def test_small_lines_are_merged_up_to_the_cap(self) -> None:
        chunks = chunk_text("aa\nbb\ncc", max_chars=7)
        assert chunks == ["aa\nbb", "cc"]  # merged when they fit, else flushed

    def test_invalid_max_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            chunk_text("text", max_chars=0)

    def test_default_cap_encodes_the_600_token_budget(self) -> None:
        assert DEFAULT_MAX_TOKENS == 600  # build bible §13 hard cap
        assert DEFAULT_MAX_CHARS == 600 * DEFAULT_CHARS_PER_TOKEN


class TestChunkDocument:
    def test_ids_are_stable_and_unique(self) -> None:
        doc = _document("first block\n\n" + "filler words " * 300)
        ids = [chunk.id for chunk in chunk_document(doc)]
        assert len(ids) == len(set(ids))
        repeat_ids = [chunk.id for chunk in chunk_document(doc)]
        assert ids == repeat_ids

    def test_ids_differ_per_document(self) -> None:
        a = chunk_document(_document("same content", ref="A"))
        b = chunk_document(_document("same content", ref="B"))
        assert a[0].id != b[0].id

    def test_chunks_carry_source_metadata(self) -> None:
        doc = _document("one\n\ntwo three", ref="REQ-9")
        chunks = chunk_document(doc)
        for index, chunk in enumerate(chunks):
            assert chunk.document_ref == "REQ-9"
            assert chunk.source_type is KnowledgeSourceType.DOCUMENT
            assert chunk.title == "Doc"
            assert chunk.chunk_index == index
            assert chunk.char_count == len(chunk.content)

    def test_long_content_yields_multiple_capped_chunks(self) -> None:
        content = "word " * 2000
        chunks = chunk_document(_document(content))
        assert len(chunks) > 1
        assert all(chunk.char_count <= DEFAULT_MAX_CHARS for chunk in chunks)
        assert _normalized("\n".join(c.content for c in chunks)) == _normalized(content)

    def test_blank_content_document_raises(self) -> None:
        doc = _document("   ")
        with pytest.raises(ValueError, match="blank"):
            chunk_document(doc)
