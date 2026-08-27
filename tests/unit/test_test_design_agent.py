"""S1.2 Test Design Agent — unit tests against a fake OpenAI-compatible server.

Exit criterion (build bible §19 S1.2): "Step coverage ≥ 85% vs oracle
(golden set)."

How it is measured:
- the golden fixture requirements (golden_v1.json — the same set S1.1
  was judged on, now the shared S1.2/S1.4 dataset) run through
  the agent against an ``httpx`` async mock transport (no network, no model).
  The fake "model" reads the title out of the rendered prompt and designs
  test cases the way a competent model would (``MODEL_OUTPUTS``);
- an independent hand-written **oracle** (``ORACLE_STEPS``) states, per
  requirement, the steps a complete suite must exercise — ground truth in
  QA-lead phrasing;
- :func:`step_coverage` scores the generated steps against the oracle:
  stopwords stripped, inflection-tolerant token matching (prefix), and an
  oracle step counts as *covered* when ≥ 60% of its meaningful tokens appear
  in the generated step pool.

Gate: all golden outputs are schema-valid (``TestSuite`` / §12) and every
requirement's step coverage is ≥ 85%.

S1.4: FIXTURES / MODEL_OUTPUTS / ORACLE_STEPS are loaded from the golden
set (packages/ai/golden/golden_v1.json) — the same file the
qa_copilot_ai.eval runner scores a live local LLM against (§22).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from qa_copilot_ai import (
    TEST_CASE_TYPES,
    InMemoryPromptStore,
    LLMGateway,
    PromptSpec,
    RequirementAnalysis,
    RequirementInput,
    TestDesignAgent,
    TestDesignInput,
    TestSuite,
)
from qa_copilot_ai.eval import default_golden_path, load_golden_set, step_coverage
from qa_copilot_ai.prompts import PromptNotFound

PROMPT_SPEC = PromptSpec(
    name="test-designer",
    version=1,
    body=(
        "Design test cases for the requirement: {{title}} | {{content}} | "
        "criteria: {{acceptance_criteria}} | analysis: {{analysis}}"
    ),
    model_class="coder",
    input_budget=8000,
    output_budget=4096,
    schema_ref="test-suite/v1",
    temperature=0.3,
)


def _design_input(fixture: RequirementInput) -> TestDesignInput:
    """The agent's input for a fixture (analysis left unset)."""
    return TestDesignInput(
        title=fixture.title,
        content=fixture.content,
        acceptance_criteria=fixture.acceptance_criteria,
    )


def _assistant(payload: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 210},
    }


Handler = Callable[[httpx.Request], httpx.Response]


class _AsyncMockTransport(httpx.AsyncBaseTransport):
    """Async-transport shim so ``AsyncClient`` accepts a sync fake handler."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _gateway(handler: Handler) -> LLMGateway:
    return LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=0,
        transport=_AsyncMockTransport(handler),
    )


# --- Golden set (S1.4) — fixtures, fake "model" outputs, oracle steps --------
#
# Single source of truth: packages/ai/golden/golden_v1.json (build bible
# §19 S1.4, §22). The qa_copilot_ai.eval runner scores the same data
# against a live local LLM — the S1.2 tests below reuse it as their fakes.
# step_coverage is the shared §31.7 metric (qa_copilot_ai.eval.golden).

_GOLDEN = load_golden_set(default_golden_path())

FIXTURES: tuple[RequirementInput, ...] = tuple(
    RequirementInput(
        title=fixture.title,
        content=fixture.content,
        acceptance_criteria=tuple(fixture.acceptance_criteria),
    )
    for fixture in _GOLDEN.fixtures
)

MODEL_OUTPUTS: dict[str, list[dict[str, object]]] = {
    fixture.title: [case.model_dump() for case in fixture.suite.test_cases]
    for fixture in _GOLDEN.fixtures
}

ORACLE_STEPS: dict[str, list[str]] = {
    fixture.title: list(fixture.oracle_steps) for fixture in _GOLDEN.fixtures
}


def _case(
    requirement_title: str,
    case_id: str,
    title: str,
    case_type: str,
    steps: list[str],
    expected_results: list[str],
    *,
    priority: str = "medium",
    risk: str = "medium",
    preconditions: list[str] | None = None,
) -> dict[str, object]:
    """One §12 test-case dict — the fake model's output vocabulary."""
    return {
        "id": case_id,
        "title": title,
        "type": case_type,
        "priority": priority,
        "preconditions": preconditions if preconditions is not None else [],
        "steps": steps,
        "expected_results": expected_results,
        "risk": risk,
        "requirement_refs": [requirement_title],
    }


# --- Prompt + parsing behavior ------------------------------------------------


def _ok_handler(cases: list[dict[str, object]]) -> Handler:
    """A fake model that always answers with *cases*."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant({"test_cases": cases}))

    return handler


def test_agent_uses_prompt_from_registry() -> None:
    """The agent loads its prompt from the store (prompt selection)."""
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.extend(body["messages"])
        return httpx.Response(
            200, json=_assistant({"test_cases": MODEL_OUTPUTS[FIXTURES[0].title]})
        )

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert len(captured) == 1
    prompt = captured[0]["content"]
    # The rendered prompt carries the requirement, its criteria and content.
    assert FIXTURES[0].title in prompt
    assert FIXTURES[0].content in prompt
    assert FIXTURES[0].acceptance_criteria[0] in prompt


def test_agent_passes_analysis_when_provided() -> None:
    """The optional S1.1 analysis is rendered into the prompt (§4 flow)."""
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.extend(body["messages"])
        return httpx.Response(
            200, json=_assistant({"test_cases": MODEL_OUTPUTS[FIXTURES[0].title]})
        )

    analysis = RequirementAnalysis(
        summary="Login must verify credentials securely.",
        actors=["user"],
        testable_criteria=["valid credentials return a session"],
        preconditions=["a registered account exists"],
        suggested_test_types=["functional", "security"],
        risks=["credential leakage"],
        open_questions=[],
        confidence=0.9,
    )
    requirement = TestDesignInput(
        title=FIXTURES[0].title,
        content=FIXTURES[0].content,
        acceptance_criteria=FIXTURES[0].acceptance_criteria,
        analysis=analysis,
    )
    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(requirement)
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert len(captured) == 1
    assert analysis.summary in captured[0]["content"]


def test_agent_raises_when_prompt_missing() -> None:
    """A registry without the prompt fails loud (prompt selection, §31.6)."""
    store = InMemoryPromptStore([])

    async def run() -> None:
        gateway = _gateway(_ok_handler([MODEL_OUTPUTS[FIXTURES[0].title][0]]))
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    with pytest.raises(PromptNotFound):
        asyncio.run(run())


def test_agent_rejects_invalid_json() -> None:
    """Model output that is not JSON fails loud (schema-valid gate)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "not json"}}]},
        )

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="no JSON object"):
        asyncio.run(run())


def test_agent_rejects_missing_test_cases() -> None:
    """Model output that is JSON but not a suite fails loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": '{"summary": "no cases"}'}}
                ]
            },
        )

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="schema validation"):
        asyncio.run(run())


def test_agent_rejects_empty_steps() -> None:
    """A case without steps cannot be executed (§9) — fails loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        case = _case(
            FIXTURES[0].title, "TC-001", "No steps", "functional", [], ["Something happens"]
        )
        return httpx.Response(200, json=_assistant({"test_cases": [case]}))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="schema validation"):
        asyncio.run(run())


def test_agent_rejects_unknown_type() -> None:
    """Model output with an unknown test case type fails loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        case = _case(
            FIXTURES[0].title, "TC-001", "Chaos testing", "chaos", ["Do something"], ["It works"]
        )
        return httpx.Response(200, json=_assistant({"test_cases": [case]}))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="unknown test case type"):
        asyncio.run(run())


def test_agent_rejects_duplicate_ids() -> None:
    """Two cases with the same id break §12 numbering — fails loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        first = _case(
            FIXTURES[0].title, "TC-001", "First", "functional", ["Step one"], ["Outcome one"]
        )
        second = _case(
            FIXTURES[0].title, "TC-001", "Second", "negative", ["Step two"], ["Outcome two"]
        )
        return httpx.Response(200, json=_assistant({"test_cases": [first, second]}))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            await agent.run(_design_input(FIXTURES[0]))
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="duplicate test case ids"):
        asyncio.run(run())


def test_agent_tolerates_markdown_fence() -> None:
    """Model output wrapped in a markdown fence is still parsed."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {"test_cases": MODEL_OUTPUTS[FIXTURES[0].title]}
        fenced = "```json\n" + json.dumps(payload) + "\n```"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": fenced}}]},
        )

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> TestSuite:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            return (await agent.run(_design_input(FIXTURES[0]))).suite
        finally:
            await gateway.aclose()

    suite = asyncio.run(run())
    assert len(suite.test_cases) == 3
    assert [case.id for case in suite.test_cases] == ["TC-001", "TC-002", "TC-003"]


# --- S1.2 exit criterion: step coverage ≥ 85% vs oracle (golden set) ------


def _generated_steps(suite: TestSuite) -> list[str]:
    return [step for case in suite.test_cases for step in case.steps]


def test_golden_set_step_coverage_ge_85_percent() -> None:
    """Exit criterion (build bible §19 S1.2): step coverage ≥ 85% vs oracle.

    All golden outputs must be schema-valid (``TestSuite`` / §12) and every
    requirement's generated steps must cover ≥ 85% of its oracle steps.
    """
    assert len(FIXTURES) == 12  # golden_v1

    def handler(request: httpx.Request) -> httpx.Response:
        # The fake model reads its title out of the rendered prompt — a wrong
        # prompt/render surfaces here as a KeyError.
        body = json.loads(request.content)
        content = body["messages"][0]["content"]
        title = (
            content.split("|", 1)[0].removeprefix("Design test cases for the requirement: ").strip()
        )
        return httpx.Response(200, json=_assistant({"test_cases": MODEL_OUTPUTS[title]}))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> list[TestSuite]:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            return [(await agent.run(_design_input(fixture))).suite for fixture in FIXTURES]
        finally:
            await gateway.aclose()

    suites = asyncio.run(run())
    assert len(suites) == len(FIXTURES)
    for suite in suites:
        assert isinstance(suite, TestSuite)
        assert len(suite.test_cases) >= 1
        for case in suite.test_cases:
            assert case.type in TEST_CASE_TYPES
            assert case.steps and case.expected_results

    failures: list[tuple[str, float]] = []
    for fixture, suite in zip(FIXTURES, suites, strict=True):
        coverage = step_coverage(_generated_steps(suite), ORACLE_STEPS[fixture.title])
        if coverage < 0.85:
            failures.append((fixture.title, coverage))
    assert not failures, f"step coverage below 85%: {failures}"


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f.title for f in FIXTURES])
def test_fixture_step_coverage_ge_85_percent(fixture: RequirementInput) -> None:
    """Per-requirement coverage — the report view of the S1.2 exit gate."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant({"test_cases": MODEL_OUTPUTS[fixture.title]}))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> TestSuite:
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            return (await agent.run(_design_input(fixture))).suite
        finally:
            await gateway.aclose()

    suite = asyncio.run(run())
    coverage = step_coverage(_generated_steps(suite), ORACLE_STEPS[fixture.title])
    assert coverage >= 0.85, f"step coverage {coverage:.0%} < 85%"
