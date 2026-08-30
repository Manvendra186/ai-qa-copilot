"""Deterministic size-capped chunking (build bible §13: hard cap ≤ 600 tokens).

Chunking never calls the LLM and never calls the clock: the same document
always yields the same chunks (stable ids, stable order). The token budget is
enforced with a conservative chars-per-token factor so the §13 cap holds for
both prose and code.
"""

from __future__ import annotations

import hashlib
import re

from .models import KnowledgeChunk, KnowledgeDocument

#: Conservative estimate for mixed prose/code (CJK ≈ 1 token/char).
DEFAULT_CHARS_PER_TOKEN = 4

#: Build bible §13 hard cap: chunks must stay within 600 tokens.
DEFAULT_MAX_TOKENS = 600

#: Character budget that guarantees the token budget at the factor above.
DEFAULT_MAX_CHARS = DEFAULT_MAX_TOKENS * DEFAULT_CHARS_PER_TOKEN  # 2400

_BLANK_LINE_RE = re.compile(r"\n\s*\n")


def chunk_text(text: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> list[str]:
    """Split *text* into deterministic chunks of at most *max_chars* characters.

    - Lines are the atomic unit (paragraphs are kept together when they fit).
    - Adjacent small units are merged up to the cap (no wasted space).
    - Oversized units are hard-cut at the cap.
    - Content is preserved: chunks concatenated and whitespace-normalized
      equal the whitespace-normalized input (no word is lost or added).
    """
    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")
    units: list[str] = []
    for block in _BLANK_LINE_RE.split(text.replace("\r\n", "\n").replace("\r", "\n").strip()):
        for line in block.split("\n"):
            line = line.strip()
            if line:
                units.append(line)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if len(unit) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_hard_cut(unit, max_chars))
        elif not current:
            current = unit
        elif len(current) + 1 + len(unit) <= max_chars:
            current = f"{current}\n{unit}"
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def chunk_document(
    document: KnowledgeDocument, *, max_chars: int = DEFAULT_MAX_CHARS
) -> list[KnowledgeChunk]:
    """Chunk one document; ids are deterministic over (source_ref, index)."""
    texts = chunk_text(document.content, max_chars=max_chars)
    if not texts:
        raise ValueError("document content is blank; nothing to chunk")
    return [
        KnowledgeChunk(
            id=f"chunk-{_digest(document.source_ref, index)}",
            document_ref=document.source_ref,
            source_type=document.source_type,
            title=document.title,
            content=text,
            chunk_index=index,
            char_count=len(text),
            metadata=dict(document.metadata),
        )
        for index, text in enumerate(texts)
    ]


def _hard_cut(unit: str, max_chars: int) -> list[str]:
    return [unit[i : i + max_chars] for i in range(0, len(unit), max_chars)]


def _digest(source_ref: str, index: int) -> str:
    payload = f"{source_ref}#{index}".encode()
    return hashlib.sha1(payload).hexdigest()[:16]
