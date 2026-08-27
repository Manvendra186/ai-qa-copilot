"""The Requirement Agent — the first real LLM-backed agent (S1.1).

Build bible §19 Phase 1: "Requirement analysis: Parse requirement/acceptance
criteria into structured QA context." The agent:

1. loads its prompt from the registry (§31.6 — ``requirement-analyst@1``),
2. renders it with the requirement (title / content / acceptance criteria),
3. calls the model through the gateway (§31.1),
4. validates the JSON output against :class:`RequirementAnalysis` (§12 style).

The agent is **pure**: it takes a :class:`~qa_copilot_ai.prompts.PromptStore`
and an :class:`~qa_copilot_ai.gateway.LLMGateway`, and returns a
:class:`RequirementAgentResult` (the validated analysis + the audit payload).
It never touches the database — the caller (the API job agent) records the
``ai_actions`` row and persists the analysis.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..gateway import AICallResult, LLMGateway
from ..prompts import PromptStore, render_prompt

#: The agent's registry name (build bible §31.6).
AGENT_NAME = "requirement-analyst"

#: Fixed vocabulary for ``suggested_test_types`` (wire strings; aligned with
#: ``qa_copilot_domain.enums.TestType``). The agent may only suggest these.
SUGGESTED_TEST_TYPES: frozenset[str] = frozenset(
    {"functional", "negative", "boundary", "risk", "accessibility", "security"}
)


class RequirementAnalysis(BaseModel):
    """Schema-validated output of the Requirement Agent.

    The structured QA context a test designer uses to write test cases
    (build bible §3 V1 scope, §12 output style).
    """

    summary: str = Field(
        min_length=1,
        description="1-3 sentence restatement of what must be true",
    )
    actors: list[str] = Field(
        default_factory=list,
        description="Distinct actors/roles that interact (e.g. 'user', 'admin')",
    )
    testable_criteria: list[str] = Field(
        default_factory=list,
        description="Acceptance criteria restated as concrete, verifiable statements",
    )
    preconditions: list[str] = Field(
        default_factory=list,
        description="Setup/state required before the behavior can be tested",
    )
    suggested_test_types: list[str] = Field(
        default_factory=list,
        description="Subset of the fixed test-type vocabulary",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="Risk areas, edge cases, or failure modes to watch",
    )
    open_questions: list[str] = Field(
        default_factory=list,
        description="Ambiguities that need clarification (empty if none)",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Self-assessed confidence in this analysis (0.0-1.0)",
    )

    @field_validator("suggested_test_types")
    @classmethod
    def _check_test_types(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in SUGGESTED_TEST_TYPES]
        if unknown:
            raise ValueError(
                f"unknown suggested_test_types {unknown}; allowed: {sorted(SUGGESTED_TEST_TYPES)}"
            )
        return values


@dataclass(frozen=True, slots=True)
class RequirementInput:
    """The requirement the agent analyzes (from the analyze request)."""

    title: str
    content: str
    acceptance_criteria: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RequirementAgentResult:
    """Everything the caller needs: the validated analysis + the audit payload."""

    analysis: RequirementAnalysis
    call: AICallResult
    prompt_ref: str


def _parse_analysis(text: str) -> RequirementAnalysis:
    """Parse the model's JSON output into a validated :class:`RequirementAnalysis`.

    Tolerates a stray markdown fence or leading prose (local models sometimes
    wrap JSON); the first ``{`` … last ``}`` span is parsed. Invalid JSON or a
    schema violation raises ``ValueError`` (the job fails loud — §31.7
    schema-valid ≥ 99%).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"requirement analysis output has no JSON object: {text[:200]!r}")
    payload = text[start : end + 1]
    try:
        return RequirementAnalysis.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(f"requirement analysis output failed schema validation: {exc}") from exc


class RequirementAgent:
    """The first real LLM-backed agent (S1.1, build bible §19 Phase 1).

    Loads its prompt from the registry (§31.6), renders it with the
    requirement, calls the model through the gateway (§31.1), and returns a
    schema-validated :class:`RequirementAnalysis`.

    Pure: no DB, no I/O beyond the gateway — the caller records the audit row
    and persists the result.
    """

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = AGENT_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, requirement: RequirementInput) -> dict[str, str]:
        criteria = requirement.acceptance_criteria
        if criteria:
            criteria_text = "\n".join(f"- {c}" for c in criteria)
        else:
            criteria_text = "(none stated — derive testable criteria from the description)"
        return {
            "title": requirement.title,
            "content": requirement.content,
            "acceptance_criteria": criteria_text,
        }

    async def run(self, requirement: RequirementInput) -> RequirementAgentResult:
        """Analyze *requirement*; return the validated analysis + audit payload.

        Raises ``ValueError`` when the model output is not schema-valid JSON,
        or ``PromptNotFound`` when the registry has no such prompt.
        """
        spec = self._store.get(self._prompt_name, self._prompt_version)
        body = render_prompt(spec, **self._variables(requirement))
        messages = [{"role": "user", "content": body}]
        result = await self._gateway.chat(
            messages,
            agent=AGENT_NAME,
            temperature=spec.temperature if spec.temperature is not None else 0.2,
            max_tokens=spec.output_budget if spec.output_budget is not None else 2048,
        )
        analysis = _parse_analysis(result.text)
        return RequirementAgentResult(analysis=analysis, call=result, prompt_ref=spec.ref)
