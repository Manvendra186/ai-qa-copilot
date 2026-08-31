"""Knowledge Q&A agent (S5.4, build bible §19 Phase 5).

RAG over the project knowledge base (S5.1/S5.2 retrieval): the caller
retrieves the top-k passages for a question (``qa_copilot_knowledge``) and
hands them in as :class:`KnowledgeContext` items; this agent renders the
``knowledge-qa@1`` prompt, calls the model through the gateway (§31.1), and
parses the **strict grounded-answer contract** (build bible §19 S5.4):

- **in-scope** — a non-empty answer grounded in the passages (concrete
  values quoted verbatim) + at least one citation (source ref + title);
- **refusal** — ``in_scope=false`` with no answer and no citations, for
  questions the retrieved context does not directly answer (out-of-scope).

v1 scope (§19 S5.4): agent + parser + runner/CLI over the golden Q&A set
(``qa_copilot_knowledge.qa_golden``) with the **live gate**: ≥ 80% of
in-scope questions grounded on project-specific facts, 100% of out-of-scope
questions refused. The S5.5 Ask API + web Q&A view wire this agent into the
jobs API with the same contract.

All model calls go through the LLM gateway (§31.1); the output is
schema-validated (:class:`QAAnswer`) — invalid output fails loud
(``ValueError``), the job never half-succeeds (§31.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..config import load_model_settings
from ..gateway import AICallResult, LLMGateway
from ..prompts import PromptStore, render_prompt

KNOWLEDGE_QA_NAME = "knowledge-qa"

NO_CONTEXT = "(no relevant project knowledge was retrieved for this question)"


@dataclass(frozen=True, slots=True)
class KnowledgeContext:
    """One retrieved knowledge passage (wire shape of a search hit)."""

    source_ref: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class KnowledgeQAInput:
    """One question + the retrieved passages the answer must be grounded in."""

    question: str
    context: tuple[KnowledgeContext, ...] = ()


class QACitation(BaseModel):
    """A citation back to one retrieved passage (its exact source ref + title)."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str = Field(min_length=1)
    title: str = Field(min_length=1)


class QAAnswer(BaseModel):
    """The strict grounded-answer contract (schema: ``knowledge-qa/v1``).

    In-scope: ``answer`` is non-empty and ``citations`` is non-empty.
    Refusal: ``in_scope=false`` with ``answer=null`` and ``citations=[]``.
    Anything else is a contract violation (schema rejection).
    """

    model_config = ConfigDict(extra="forbid")

    in_scope: bool
    answer: str | None = Field(default=None, min_length=1)
    citations: list[QACitation] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _enforce_contract(self) -> QAAnswer:
        if self.in_scope:
            if self.answer is None or not self.answer.strip():
                raise ValueError("an in-scope answer must be a non-empty string")
            if not self.citations:
                raise ValueError("an in-scope answer must cite at least one source")
        elif self.answer is not None or self.citations:
            raise ValueError("a refusal must carry no answer and no citations")
        return self


def parse_qa_answer(text: str) -> QAAnswer:
    """Parse the model's JSON output into a validated :class:`QAAnswer`.

    Tolerates a stray markdown fence or leading prose (local models sometimes
    wrap JSON); the first ``{`` … last ``}`` span is parsed. Invalid JSON or
    a contract violation raises ``ValueError`` (the job fails loud, §31.7).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"knowledge-qa output has no JSON object: {text[:200]!r}")
    payload = text[start : end + 1]
    try:
        return QAAnswer.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(f"knowledge-qa output failed schema validation: {exc}") from exc


def render_context(context: tuple[KnowledgeContext, ...]) -> str:
    """Render the retrieved passages as the prompt's ``{{context}}`` block."""
    if not context:
        return NO_CONTEXT
    passages: list[str] = []
    for number, item in enumerate(context, start=1):
        passages.append(f"{number}. [{item.source_ref}] {item.title}\n{item.content}")
    return "\n\n".join(passages)


@dataclass(frozen=True, slots=True)
class KnowledgeQAAgentResult:
    """Everything the caller needs: the validated answer + the audit payload."""

    answer: QAAnswer
    call: AICallResult
    prompt_ref: str


class KnowledgeQAAgent:
    """The Knowledge Q&A agent (S5.4, build bible §19 Phase 5).

    Loads its prompt from the registry (§31.6, ``knowledge-qa@1``), renders
    it with the question + retrieved passages, calls the model through the
    gateway (§31.1), and returns a contract-valid :class:`QAAnswer`.

    Pure: no DB, no I/O beyond the gateway — the caller (S5.5 Ask API)
    persists the answer and records the audit row.
    """

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = KNOWLEDGE_QA_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, qa_input: KnowledgeQAInput) -> dict[str, str]:
        return {
            "question": qa_input.question,
            "context": render_context(qa_input.context),
        }

    async def run(self, qa_input: KnowledgeQAInput) -> KnowledgeQAAgentResult:
        """Answer one question from the retrieved context; return answer + audit.

        Raises ``ValueError`` when the model output is not contract-valid
        JSON, or ``PromptNotFound`` when the registry has no such prompt.
        """
        spec = self._store.get(self._prompt_name, self._prompt_version)
        body = render_prompt(spec, **self._variables(qa_input))
        messages: list[dict[str, Any]] = [{"role": "user", "content": body}]
        # §9 budgets: the prompt's own values win; the AI_* environment
        # defaults (qa_copilot_ai.config) are the fallback.
        settings = load_model_settings()
        result = await self._gateway.chat(
            messages,
            agent=KNOWLEDGE_QA_NAME,
            temperature=spec.temperature if spec.temperature is not None else settings.temperature,
            max_tokens=(
                spec.output_budget if spec.output_budget is not None else settings.max_output_tokens
            ),
            max_input_tokens=(
                spec.input_budget if spec.input_budget is not None else settings.max_input_tokens
            ),
        )
        answer = parse_qa_answer(result.text)
        return KnowledgeQAAgentResult(answer=answer, call=result, prompt_ref=spec.ref)
