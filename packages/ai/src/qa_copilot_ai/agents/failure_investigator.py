"""Failure Investigator agent (S4.1, build bible §19 Phase 4).

The S3.3 normalizer turns a raw failure into a structured
:class:`~qa_copilot_domain.entities.NormalizedFailure` (signals + evidence +
context); this agent reasons over that normalized shape and produces the
**failure-analysis output contract (build bible §12)**:

``category`` (§16 taxonomy) + ``root_cause`` + ``confidence`` +
``evidence`` + ``suggested_fix`` + ``needs_human_approval``.

v1 scope (§19 S4.1): **classify** — top-1 category accuracy ≥ 80% on the
30-broken-test set (``packages/execution/golden/failure_v1.json``).
Suggested fix is advisory (text, human-approved — §26 never auto-heals);
root-cause isolation and the approve→re-run loop are S4.2/S4.3.

All model calls go through the LLM gateway (§31.1); the output is
schema-validated (:class:`Diagnosis`) — invalid output fails loud
(``ValueError``), the job never half-succeeds (§31.7).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from qa_copilot_domain import FailureCategory, NormalizedFailure

from ..config import load_model_settings
from ..gateway import AICallResult, LLMGateway
from ..prompts import PromptStore, render_prompt

INVESTIGATOR_NAME = "failure-investigator"


@dataclass(frozen=True, slots=True)
class InvestigatorInput:
    """One failed execution, already normalized (S3.3 text-first shape).

    The agent reasons over the normalized structure, not raw logs: the
    signals/evidence lines are the text-first material (§16 v1.1) and the
    normalizer's ``category`` is its best-guess prior for the model.
    """

    normalized: NormalizedFailure


class Diagnosis(BaseModel):
    """The §12 failure-analysis output contract (schema: ``failure-analysis/v1``).

    ``category`` is the top-1 classification against the §16 taxonomy;
    ``evidence`` must cite the normalized failure's captured lines (no
    invented quotes — the prompt enforces this, the field shape keeps the
    report auditable); ``needs_human_approval`` is always ``True`` in v1
    (no auto-heal, §26).
    """

    category: FailureCategory
    root_cause: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(min_length=1)
    suggested_fix: str = Field(min_length=1)
    needs_human_approval: bool = True


def parse_diagnosis(text: str) -> Diagnosis:
    """Parse the model's JSON output into a validated :class:`Diagnosis`.

    Tolerates a stray markdown fence or leading prose (local models sometimes
    wrap JSON); the first ``{`` … last ``}`` span is parsed. Invalid JSON or
    a schema violation raises ``ValueError`` (the job fails loud).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"failure analysis output has no JSON object: {text[:200]!r}")
    payload = text[start : end + 1]
    try:
        return Diagnosis.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(f"failure analysis output failed schema validation: {exc}") from exc


@dataclass(frozen=True, slots=True)
class FailureInvestigatorAgentResult:
    """Everything the caller needs: the validated diagnosis + the audit payload."""

    diagnosis: Diagnosis
    call: AICallResult
    prompt_ref: str


class FailureInvestigatorAgent:
    """The Failure Investigator (S4.1, build bible §19 Phase 4).

    Loads its prompt from the registry (§31.6, ``failure-investigator@1``),
    renders it with the S3.3 normalized failure, calls the model through the
    gateway (§31.1), and returns a schema-valid :class:`Diagnosis` (§12).

    Pure: no DB, no I/O beyond the gateway — the caller persists the
    diagnosis and records the audit row.
    """

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = INVESTIGATOR_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, investigation: InvestigatorInput) -> dict[str, str]:
        normalized = investigation.normalized
        if normalized.category_signals:
            signals = ", ".join(normalized.category_signals)
        else:
            signals = "(none detected)"
        if normalized.evidence:
            evidence_text = "\n".join(f"- {line}" for line in normalized.evidence)
        else:
            evidence_text = "(no lines captured)"
        return {
            "category": normalized.category.value,
            "signals": signals,
            "evidence": evidence_text,
            "http_status": str(normalized.http_status) if normalized.http_status else "n/a",
            "selector": normalized.selector or "n/a",
            "endpoint": normalized.endpoint or "n/a",
        }

    async def run(self, investigation: InvestigatorInput) -> FailureInvestigatorAgentResult:
        """Investigate one normalized failure; return the diagnosis + audit payload.

        Raises ``ValueError`` when the model output is not schema-valid JSON,
        or ``PromptNotFound`` when the registry has no such prompt.
        """
        spec = self._store.get(self._prompt_name, self._prompt_version)
        body = render_prompt(spec, **self._variables(investigation))
        messages = [{"role": "user", "content": body}]
        # §9 budgets: the prompt's own values win; the AI_* environment
        # defaults (qa_copilot_ai.config) are the fallback.
        settings = load_model_settings()
        result = await self._gateway.chat(
            messages,
            agent=INVESTIGATOR_NAME,
            temperature=spec.temperature if spec.temperature is not None else settings.temperature,
            max_tokens=(
                spec.output_budget if spec.output_budget is not None else settings.max_output_tokens
            ),
            max_input_tokens=(
                spec.input_budget if spec.input_budget is not None else settings.max_input_tokens
            ),
        )
        diagnosis = parse_diagnosis(result.text)
        return FailureInvestigatorAgentResult(diagnosis=diagnosis, call=result, prompt_ref=spec.ref)
