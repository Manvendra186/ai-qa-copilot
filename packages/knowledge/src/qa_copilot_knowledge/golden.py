"""Retrieval golden set + gate (build bible §19 S5.1 exit: deterministic gate).

The golden set is the single source of truth for S5.1 retrieval quality: a
fixed corpus of project-shaped knowledge documents plus queries with the
expected top-1 document. Like the S3.3 failure golden, schema errors are
rejected loud (tamper detection).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field
from qa_copilot_domain import DomainModel

from .models import KnowledgeDocument
from .search import KnowledgeIndex

NonBlankStr = Annotated[str, Field(min_length=1)]


class KnowledgeGoldenSetError(RuntimeError):
    """Raised when a golden set file is missing, unparseable, or schema-invalid."""


class RetrievalQuery(DomainModel):
    """One retrieval question: which document must rank first (and which in top-k)."""

    id: NonBlankStr
    query: NonBlankStr
    expect_top1: NonBlankStr
    expect_top_k: list[str] = Field(default_factory=list)


class RetrievalGate(DomainModel):
    """Gate thresholds for a golden set (build bible §19 S5.1: >= 90% top-1)."""

    top1_min: float = Field(default=0.9, ge=0.0, le=1.0)


class RetrievalGoldenSet(DomainModel):
    """A fixed corpus + expected retrievals (schema-invalid input is rejected)."""

    name: NonBlankStr
    version: NonBlankStr
    gate: RetrievalGate = Field(default_factory=RetrievalGate)
    corpus: list[KnowledgeDocument] = Field(min_length=1)
    queries: list[RetrievalQuery] = Field(min_length=1)


class GoldenQueryResult(DomainModel):
    """Per-query outcome of the gate run."""

    id: NonBlankStr
    query: NonBlankStr
    expected_top1: NonBlankStr
    actual_top1: str | None
    top1_ok: bool
    topk_ok: bool


class GoldenReport(DomainModel):
    """Aggregate gate result (deterministic: same set + code, same report)."""

    name: NonBlankStr
    version: NonBlankStr
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float
    gate_top1_min: float
    gate_met: bool
    results: list[GoldenQueryResult] = Field(default_factory=list)


def default_golden_path() -> Path:
    """The canonical S5.1 golden set shipped with the package."""
    return Path(__file__).resolve().parents[2] / "golden" / "retrieval_v1.json"


def load_golden_set(path: Path) -> RetrievalGoldenSet:
    """Load + schema-validate a golden set; :class:`KnowledgeGoldenSetError` on any fault."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgeGoldenSetError(f"cannot read golden set at {path}: {exc}") from exc
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise KnowledgeGoldenSetError(f"golden set is not valid JSON: {exc}") from exc
    try:
        return RetrievalGoldenSet.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError and friends
        raise KnowledgeGoldenSetError(f"golden set fails schema validation: {exc}") from exc


def run_golden_set(path: Path) -> GoldenReport:
    """Build the index over the set's corpus, answer every query, judge the gate."""
    golden = load_golden_set(path)
    index = KnowledgeIndex(golden.corpus)
    results: list[GoldenQueryResult] = []
    for query in golden.queries:
        result = index.search(query.query)
        actual_top1 = result.hits[0].chunk.document_ref if result.hits else None
        top1_ok = actual_top1 == query.expect_top1
        topk_ok = True
        if query.expect_top_k:
            refs = {hit.chunk.document_ref for hit in result.hits}
            topk_ok = all(ref in refs for ref in query.expect_top_k)
        results.append(
            GoldenQueryResult(
                id=query.id,
                query=query.query,
                expected_top1=query.expect_top1,
                actual_top1=actual_top1,
                top1_ok=top1_ok,
                topk_ok=topk_ok,
            )
        )
    total = len(results)
    passed = sum(1 for r in results if r.top1_ok and r.topk_ok)
    rate = passed / total if total else 0.0
    return GoldenReport(
        name=golden.name,
        version=golden.version,
        total=total,
        passed=passed,
        failed=total - passed,
        pass_rate=rate,
        gate_top1_min=golden.gate.top1_min,
        gate_met=total > 0 and rate >= golden.gate.top1_min,
        results=results,
    )
