"""The Test Design Agent — the second real LLM-backed agent (S1.2).

Build bible §19 Phase 1: "Test Design Agent (functional/negative/boundary/
risk/a11y/security)" — §3 V1 scope: "Generate functional, negative, boundary,
risk, accessibility, and basic security scenarios"; §8: the Test Design agent
"Create[s] scenarios and structured test cases" from requirements and the
risk context. The agent:

1. loads its prompt from the registry (§31.6 — ``test-designer@1``),
2. renders it with the requirement plus the (optional) S1.1
   :class:`RequirementAnalysis` (§4 flow: Requirement → QA Test Designer),
3. calls the model through the gateway (§31.1),
4. validates the JSON output against :class:`TestSuite` / :class:`TestCase`
   (the build bible §12 test-case schema).

Like the Requirement Agent, it is **pure**: no DB, no I/O beyond the
gateway — the caller (the S1.3 job agent / route) persists the suite, links
it to the requirement (``requirement_test_cases``), and records the
``ai_actions`` row.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ..gateway import AICallResult, LLMGateway
from ..prompts import PromptStore, render_prompt
from .requirement import RequirementAnalysis

#: The agent's registry name (build bible §31.6).
TEST_DESIGNER_NAME = "test-designer"

#: Fixed vocabulary for a test case's ``type`` (wire strings; aligned with
#: ``qa_copilot_domain.enums.TestType`` and the §3 V1 scope).
TEST_CASE_TYPES: frozenset[str] = frozenset(
    {"functional", "negative", "boundary", "risk", "accessibility", "security"}
)
#: Fixed vocabulary for ``priority`` (``qa_copilot_domain.enums.Priority``).
PRIORITIES: frozenset[str] = frozenset({"high", "medium", "low"})
#: Fixed vocabulary for ``risk`` (``qa_copilot_domain.enums.RiskLevel``).
RISK_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})


class TestCase(BaseModel):
    """One structured test case (build bible §12 test-case output schema).

    ``id`` follows the §12 ``TC-###`` numbering. ``steps`` and
    ``expected_results`` are both required to be non-empty — a case without
    steps cannot be executed, and one without an expectation cannot be
    judged (§9: every agent has explicit input/output schemas).
    """

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    id: str = Field(pattern=r"^TC-\d{3,}$", description="Suite-local id, e.g. TC-001")
    title: str = Field(min_length=1)
    type: str = Field(description="One of the six fixed test-case types")
    priority: str = Field(default="medium", description="high | medium | low")
    preconditions: list[str] = Field(
        default_factory=list,
        description="Setup/state required before the first step",
    )
    steps: list[str] = Field(min_length=1, description="Ordered, concrete, executable actions")
    expected_results: list[str] = Field(min_length=1, description="Observable, verifiable outcomes")
    risk: str = Field(default="medium", description="low | medium | high")
    requirement_refs: list[str] = Field(
        default_factory=list, description="Requirement ids/titles this case covers"
    )

    @field_validator("type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if value not in TEST_CASE_TYPES:
            raise ValueError(
                f"unknown test case type {value!r}; allowed: {sorted(TEST_CASE_TYPES)}"
            )
        return value

    @field_validator("priority")
    @classmethod
    def _check_priority(cls, value: str) -> str:
        if value not in PRIORITIES:
            raise ValueError(f"unknown priority {value!r}; allowed: {sorted(PRIORITIES)}")
        return value

    @field_validator("risk")
    @classmethod
    def _check_risk(cls, value: str) -> str:
        if value not in RISK_LEVELS:
            raise ValueError(f"unknown risk {value!r}; allowed: {sorted(RISK_LEVELS)}")
        return value


class TestSuite(BaseModel):
    """The Test Design Agent's output: the cases for one requirement (§12)."""

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    test_cases: list[TestCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> TestSuite:
        ids = [case.id for case in self.test_cases]
        duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate test case ids: {duplicates}")
        return self


@dataclass(frozen=True, slots=True)
class TestDesignInput:
    """The requirement the agent designs tests for.

    ``analysis`` is the optional S1.1 :class:`RequirementAnalysis` — when the
    pipeline runs the two agents in sequence (§4), the analysis is the
    structured QA context the designer builds on. Standalone use (tests,
    direct calls) leaves it ``None``.
    """

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    title: str
    content: str
    acceptance_criteria: tuple[str, ...] = ()
    analysis: RequirementAnalysis | None = None


@dataclass(frozen=True, slots=True)
class TestDesignAgentResult:
    """Everything the caller needs: the validated suite + the audit payload."""

    suite: TestSuite
    call: AICallResult
    prompt_ref: str


def _parse_suite(text: str) -> TestSuite:
    """Parse the model's JSON output into a validated :class:`TestSuite`.

    Tolerates a stray markdown fence or leading prose (local models sometimes
    wrap JSON); the first ``{`` … last ``}`` span is parsed. Invalid JSON or a
    schema violation raises ``ValueError`` (the job fails loud — §31.7
    schema-valid ≥ 99%).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"test design output has no JSON object: {text[:200]!r}")
    payload = text[start : end + 1]
    try:
        return TestSuite.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(f"test design output failed schema validation: {exc}") from exc


class TestDesignAgent:
    """The Test Design Agent (S1.2, build bible §19 Phase 1).

    Loads its prompt from the registry (§31.6, ``test-designer@1``), renders
    it with the requirement and the optional S1.1 analysis, calls the model
    through the gateway (§31.1), and returns a schema-valid
    :class:`TestSuite` covering the six test-case types (§3).

    Pure: no DB, no I/O beyond the gateway — the caller persists the suite
    and records the audit row.
    """

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = TEST_DESIGNER_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, requirement: TestDesignInput) -> dict[str, str]:
        criteria = requirement.acceptance_criteria
        if criteria:
            criteria_text = "\n".join(f"- {c}" for c in criteria)
        else:
            criteria_text = "(none stated — derive testable behavior from the description)"
        analysis = requirement.analysis
        if analysis is not None:
            analysis_text = analysis.model_dump_json(indent=2)
        else:
            analysis_text = "(none provided — derive the QA context from the requirement itself)"
        return {
            "title": requirement.title,
            "content": requirement.content,
            "acceptance_criteria": criteria_text,
            "analysis": analysis_text,
        }

    async def run(self, requirement: TestDesignInput) -> TestDesignAgentResult:
        """Design test cases for *requirement*; return the suite + audit payload.

        Raises ``ValueError`` when the model output is not schema-valid JSON,
        or ``PromptNotFound`` when the registry has no such prompt.
        """
        spec = self._store.get(self._prompt_name, self._prompt_version)
        body = render_prompt(spec, **self._variables(requirement))
        messages = [{"role": "user", "content": body}]
        result = await self._gateway.chat(
            messages,
            agent=TEST_DESIGNER_NAME,
            temperature=spec.temperature if spec.temperature is not None else 0.3,
            max_tokens=spec.output_budget if spec.output_budget is not None else 4096,
        )
        suite = _parse_suite(result.text)
        return TestDesignAgentResult(suite=suite, call=result, prompt_ref=spec.ref)
