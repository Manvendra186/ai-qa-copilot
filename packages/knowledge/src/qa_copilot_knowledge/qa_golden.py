"""S5.4 Q&A golden set (build bible §19 Phase 5: RAG Q&A agent live gate).

The ``knowledge-qa@1`` agent (S5.4) answers questions strictly from the
project knowledge base. Its live gate (§19 S5.4: ≥ 80% of in-scope questions
grounded on project-specific facts, 100% of out-of-scope questions refused)
is scored deterministically against a golden Q&A set:

- **in-scope** questions carry the deterministic oracle: every
  ``grounded_facts`` phrase must appear verbatim (case-insensitive) in the
  answer, the answer must cite at least the expected ``cite_sources`` (real
  corpus documents), and no hallucinated source refs;
- **out-of-scope** questions must be refused — the answer contract is
  ``in_scope=false`` with no answer and no citations.

The corpus reuses the S5.1 demo-shop knowledge documents so the retrieval
gate and the Q&A gate stay coherent. The set is schema-validated and frozen
(§12: schema-validated AI outputs). The S5.5 Ask API + web Q&A view reuse
the same agent and contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, model_validator
from qa_copilot_domain import DomainModel

from .models import KnowledgeDocument

NonBlankStr = Annotated[str, Field(min_length=1)]


class QAGoldenSetError(RuntimeError):
    """Raised when a Q&A golden set file is missing, unparseable, or schema-invalid."""


class QAExpectations(DomainModel):
    """Deterministic oracle for one question (build bible §19 S5.4).

    In-scope questions expect a **grounded** answer (facts + citations);
    out-of-scope questions expect a strict refusal (no facts, no citations).
    """

    in_scope: bool
    grounded_facts: list[NonBlankStr] = Field(default_factory=list)
    cite_sources: list[NonBlankStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_scope_contract(self) -> QAExpectations:
        if self.in_scope:
            if not self.grounded_facts:
                raise ValueError("an in-scope question needs grounded_facts")
            if not self.cite_sources:
                raise ValueError("an in-scope question needs cite_sources")
        elif self.grounded_facts or self.cite_sources:
            raise ValueError("an out-of-scope question expects a refusal (no facts or citations)")
        return self


class QAQuestion(DomainModel):
    """One golden question with its deterministic expectations."""

    id: str = Field(pattern=r"^QA-\d{3}$")
    question: NonBlankStr
    expect: QAExpectations


class QAGate(DomainModel):
    """S5.4 live gate (§19): ≥ 80% in-scope grounded · 100% out-of-scope refused."""

    in_scope_min: float = Field(default=0.8, ge=0.0, le=1.0)
    out_of_scope_refuse_min: float = Field(default=1.0, ge=0.0, le=1.0)


class QAGoldenSet(DomainModel):
    """Versioned golden Q&A set: the demo-shop corpus + the question set."""

    name: NonBlankStr
    version: NonBlankStr
    description: str = ""
    gate: QAGate = Field(default_factory=QAGate)
    corpus: list[KnowledgeDocument] = Field(min_length=1)
    questions: list[QAQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_questions(self) -> QAGoldenSet:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique")
        if not any(question.expect.in_scope for question in self.questions):
            raise ValueError("a Q&A golden set needs at least one in-scope question")
        if not any(not question.expect.in_scope for question in self.questions):
            raise ValueError("a Q&A golden set needs at least one out-of-scope question")
        return self


def default_qa_golden_path() -> Path:
    """Default golden Q&A set path: ``packages/knowledge/golden/qa_v1.json``."""
    return Path(__file__).resolve().parents[2] / "golden" / "qa_v1.json"


def load_qa_golden_set(path: Path) -> QAGoldenSet:
    """Load + validate a golden Q&A set (schema violations are rejected loudly)."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QAGoldenSetError(f"golden Q&A set not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise QAGoldenSetError(f"invalid golden Q&A JSON at {path}: {exc}") from exc
    try:
        return QAGoldenSet.model_validate(raw)
    except ValueError as exc:
        raise QAGoldenSetError(f"invalid golden Q&A set at {path}: {exc}") from exc
