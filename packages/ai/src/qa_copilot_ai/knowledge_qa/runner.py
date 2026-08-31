"""S5.4 eval runner — the golden Q&A set vs the Knowledge Q&A agent.

Scores each question against its deterministic oracle (build bible §19
S5.4): in-scope questions must be **grounded** — every expected fact phrase
appears verbatim (case-insensitive) in the answer and the citations cover
the expected corpus sources without invented refs; out-of-scope questions
must be **refused** (``in_scope=false``, no answer, no citations).

Pipeline per question:

``question`` → :class:`qa_copilot_knowledge.KnowledgeIndex` (S5.1 lexical
retrieval, deterministic) → :class:`KnowledgeQAAgent` (S5.4, AI) →
:class:`~qa_copilot_ai.agents.QAAnswer`.

Failure isolation (same contract as the S1.4/S2.3/S4.1 runners): a
contract-invalid output or an LLM error fails *its* question and the run
continues — the report is always produced.

``qa_copilot_knowledge`` is a **runtime-only dependency** (imported here,
not in ``pyproject.toml``) — the same pattern as the S4.1 investigator
runner with ``qa_copilot_execution``: the monorepo venv provides both.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, Field
from qa_copilot_knowledge import MAX_TOP_K, KnowledgeIndex, QAGoldenSet, QAQuestion

from ..agents import KNOWLEDGE_QA_NAME, KnowledgeContext, KnowledgeQAInput
from ..agents.knowledge_qa import KnowledgeQAAgentResult
from ..gateway import LLMError


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class QAAnsweringAgent(Protocol):
    """The agent seam: anything that answers a question per the §19 S5.4 contract."""

    async def run(self, qa_input: KnowledgeQAInput) -> KnowledgeQAAgentResult: ...


class QAQuestionResult(BaseModel):
    """One question's outcome (stable JSON contract).

    ``grounded`` = every expected fact phrase appears in the answer;
    ``citations_ok`` = the citations cover the expected sources and invent
    none; ``refused`` = the strict refusal contract (out-of-scope only);
    ``schema_valid`` is False when the output could not be parsed into a
    :class:`QAAnswer` (or the call failed).
    """

    id: str
    question: str
    expected_in_scope: bool
    answered_in_scope: bool | None
    grounded: bool
    citations_ok: bool
    refused: bool
    schema_valid: bool
    passed: bool
    error: str | None = None
    answer: str | None = None
    citations: list[str] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int | None = Field(default=None, ge=0)


class QATotals(BaseModel):
    """Aggregate counts per scope (the §19 S5.4 live gate has two parts)."""

    questions: int = Field(ge=1)
    in_scope_questions: int = Field(ge=1)
    in_scope_passed: int = Field(ge=0)
    in_scope_fraction: float = Field(ge=0.0, le=1.0)
    out_of_scope_questions: int = Field(ge=1)
    out_of_scope_refused: int = Field(ge=0)
    out_of_scope_fraction: float = Field(ge=0.0, le=1.0)
    schema_valid_fraction: float = Field(ge=0.0, le=1.0)


class QAReport(BaseModel):
    """The S5.4 JSON artifact (same shape family as the S1.4/S2.3/S4.1 reports)."""

    schema_version: int = 1
    agent: str = KNOWLEDGE_QA_NAME
    model: str
    prompt_ref: str
    golden_name: str
    golden_version: str
    golden_questions: int = Field(ge=1)
    targets: dict[str, float]
    totals: QATotals
    passed: bool
    generated_at: str
    questions: list[QAQuestionResult]


async def _judge_question(
    agent: QAAnsweringAgent,
    index: KnowledgeIndex,
    golden: QAGoldenSet,
    question: QAQuestion,
) -> QAQuestionResult:
    """Retrieve the top-k passages, ask the agent, and score against the oracle."""
    hits = index.search(question.question, top_k=MAX_TOP_K).hits
    contexts = tuple(
        KnowledgeContext(
            source_ref=hit.chunk.document_ref,
            title=hit.chunk.title,
            content=hit.chunk.content,
        )
        for hit in hits
    )
    outcome = await agent.run(KnowledgeQAInput(question=question.question, context=contexts))
    answer = outcome.answer
    call = outcome.call
    expect = question.expect
    cited = {citation.source_ref for citation in answer.citations}
    if expect.in_scope:
        answer_text = answer.answer or ""
        grounded = all(fact.lower() in answer_text.lower() for fact in expect.grounded_facts)
        corpus_refs = {doc.source_ref for doc in golden.corpus}
        citations_ok = bool(cited) and set(expect.cite_sources) <= cited and cited <= corpus_refs
        refused = False
        passed = answer.in_scope and grounded and citations_ok
    else:
        answer_text = answer.answer or ""
        grounded = False
        citations_ok = False
        refused = answer.in_scope is False and answer.answer is None and not answer.citations
        passed = refused
    return QAQuestionResult(
        id=question.id,
        question=question.question,
        expected_in_scope=expect.in_scope,
        answered_in_scope=answer.in_scope,
        grounded=grounded,
        citations_ok=citations_ok,
        refused=refused,
        schema_valid=True,
        passed=passed,
        answer=answer_text or None,
        citations=sorted(cited),
        tokens_in=call.usage.tokens_in,
        tokens_out=call.usage.tokens_out,
        latency_ms=call.latency_ms,
    )


async def run_qa_eval(
    golden: QAGoldenSet,
    *,
    agent: QAAnsweringAgent,
    model: str,
    prompt_ref: str,
) -> QAReport:
    """Run every golden question through retrieval → agent and score the gate.

    An **in-scope** question passes when the answer is grounded (all expected
    fact phrases present) and the citations cover the expected corpus sources
    without invented refs. An **out-of-scope** question passes when it is
    refused per the strict contract. The run **passes** when both fractions
    meet the golden set's gate (S5.4 live gate: ≥ 80% in-scope grounded,
    100% out-of-scope refused). Failures are isolated: a contract-invalid
    output or LLM error marks *its* question failed and the run continues —
    the report is always produced.
    """
    index = KnowledgeIndex(golden.corpus)
    results: list[QAQuestionResult] = []
    for question in golden.questions:
        try:
            results.append(await _judge_question(agent, index, golden, question))
        except (ValueError, LLMError) as exc:
            # Contract-invalid output / LLM error: fails this question only.
            results.append(
                QAQuestionResult(
                    id=question.id,
                    question=question.question,
                    expected_in_scope=question.expect.in_scope,
                    answered_in_scope=None,
                    grounded=False,
                    citations_ok=False,
                    refused=False,
                    schema_valid=False,
                    passed=False,
                    error=str(exc)[:500],
                )
            )

    total = len(results)
    in_scope = [result for result in results if result.expected_in_scope]
    out_of_scope = [result for result in results if not result.expected_in_scope]
    in_scope_passed = sum(1 for result in in_scope if result.passed)
    out_of_scope_refused = sum(1 for result in out_of_scope if result.passed)
    in_scope_fraction = in_scope_passed / len(in_scope)
    out_of_scope_fraction = out_of_scope_refused / len(out_of_scope)
    targets = {
        "in_scope_min": golden.gate.in_scope_min,
        "out_of_scope_refuse_min": golden.gate.out_of_scope_refuse_min,
    }
    return QAReport(
        agent=KNOWLEDGE_QA_NAME,
        model=model,
        prompt_ref=prompt_ref,
        golden_name=golden.name,
        golden_version=golden.version,
        golden_questions=total,
        targets=targets,
        totals=QATotals(
            questions=total,
            in_scope_questions=len(in_scope),
            in_scope_passed=in_scope_passed,
            in_scope_fraction=in_scope_fraction,
            out_of_scope_questions=len(out_of_scope),
            out_of_scope_refused=out_of_scope_refused,
            out_of_scope_fraction=out_of_scope_fraction,
            schema_valid_fraction=sum(1 for result in results if result.schema_valid) / total,
        ),
        passed=(
            in_scope_fraction >= targets["in_scope_min"]
            and out_of_scope_fraction >= targets["out_of_scope_refuse_min"]
        ),
        generated_at=_utcnow(),
        questions=results,
    )
