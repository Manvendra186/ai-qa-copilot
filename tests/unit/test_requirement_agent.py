"""S1.1 Requirement Agent — unit tests against a fake OpenAI-compatible server.

Exit criterion (build bible §19 S1.1): "10 fixture requirements → 10/10
schema-valid." The agent contract is verified against ``httpx`` async mock
transport (no network, no model): prompt selection, structured output
parsing, and schema validation.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

import httpx
import pytest
from qa_copilot_ai import (
    SUGGESTED_TEST_TYPES,
    InMemoryPromptStore,
    LLMGateway,
    PromptSpec,
    RequirementAgent,
    RequirementAnalysis,
    RequirementInput,
)

PROMPT_SPEC = PromptSpec(
    name="requirement-analyst",
    version=1,
    body="Analyze: {{title}} / {{content}} / {{acceptance_criteria}}",
    model_class="coder",
    input_budget=2048,
    output_budget=1024,
    schema_ref="requirement-analysis/v1",
    temperature=0.2,
)


def _valid_analysis(title: str) -> dict[str, object]:
    """A schema-valid RequirementAnalysis payload for a fixture."""
    return {
        "summary": f"{title}: the system must behave as specified.",
        "actors": ["user"],
        "testable_criteria": [f"{title} works as described"],
        "preconditions": ["user is logged in"],
        "suggested_test_types": ["functional", "negative"],
        "risks": ["edge case: empty input"],
        "open_questions": [],
        "confidence": 0.8,
    }


def _assistant(payload: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
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


# --- 10 fixture requirements → 10/10 schema-valid (exit criterion) -----------


FIXTURES = [
    RequirementInput(
        title="Login with email and password",
        content="Users can log in with their email and password.",
        acceptance_criteria=(
            "Valid credentials return a session",
            "Invalid credentials show an error",
        ),
    ),
    RequirementInput(
        title="Password reset via email",
        content="Users can reset their password via a link emailed to them.",
        acceptance_criteria=(
            "Link expires after 30 minutes",
            "New password must be at least 8 characters",
        ),
    ),
    RequirementInput(
        title="Search products by name",
        content="Users can search products by name.",
        acceptance_criteria=("Results are sorted by relevance", "Empty search shows a message"),
    ),
    RequirementInput(
        title="Add item to cart",
        content="Users can add items to their cart.",
        acceptance_criteria=("Cart count updates", "Item quantity can be changed"),
    ),
    RequirementInput(
        title="Checkout with saved card",
        content="Users can check out with a saved payment card.",
        acceptance_criteria=("Card details are not re-entered", "Payment is processed"),
    ),
    RequirementInput(
        title="Order history",
        content="Users can view their order history.",
        acceptance_criteria=("Orders are listed newest first", "Each order shows status"),
    ),
    RequirementInput(
        title="Cancel an order",
        content="Users can cancel an order before it ships.",
        acceptance_criteria=("Cancellation is confirmed", "Refund is initiated"),
    ),
    RequirementInput(
        title="Apply discount code",
        content="Users can apply a discount code at checkout.",
        acceptance_criteria=("Valid code reduces the total", "Invalid code shows an error"),
    ),
    RequirementInput(
        title="Email receipt",
        content="Users receive an email receipt after purchase.",
        acceptance_criteria=("Receipt includes order details", "Receipt is sent within 1 minute"),
    ),
    RequirementInput(
        title="Admin dashboard",
        content="Admins can view a dashboard with key metrics.",
        acceptance_criteria=("Metrics are up to date", "Dashboard is accessible only to admins"),
    ),
]


def test_ten_fixtures_all_schema_valid() -> None:
    """Exit criterion: 10 fixture requirements → 10/10 schema-valid."""
    assert len(FIXTURES) == 10

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = body["messages"]
        content = messages[0]["content"]
        title = "requirement"
        for line in content.splitlines():
            if line.startswith("Title:"):
                title = line.split(":", 1)[1].strip()
                break
        return httpx.Response(200, json=_assistant(_valid_analysis(title)))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> list[RequirementAnalysis]:
        gateway = _gateway(handler)
        agent = RequirementAgent(store, gateway)
        try:
            return [(await agent.run(fixture)).analysis for fixture in FIXTURES]
        finally:
            await gateway.aclose()

    analyses = asyncio.run(run())
    assert len(analyses) == 10
    for analysis in analyses:
        assert isinstance(analysis, RequirementAnalysis)
        assert analysis.summary
        assert 0.0 <= analysis.confidence <= 1.0
        for test_type in analysis.suggested_test_types:
            assert test_type in SUGGESTED_TEST_TYPES


def test_agent_uses_prompt_from_registry() -> None:
    """The agent loads its prompt from the store (prompt selection)."""
    captured: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.extend(body["messages"])
        return httpx.Response(200, json=_assistant(_valid_analysis("x")))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = RequirementAgent(store, gateway)
        try:
            await agent.run(FIXTURES[0])
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert len(captured) == 1
    # The rendered prompt contains the requirement's title.
    assert FIXTURES[0].title in captured[0]["content"]


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
        agent = RequirementAgent(store, gateway)
        try:
            await agent.run(FIXTURES[0])
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="no JSON object"):
        asyncio.run(run())


def test_agent_rejects_schema_violation() -> None:
    """Model output that is JSON but not schema-valid fails loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Missing required `summary` field.
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": '{"actors": ["user"]}'}}]
            },
        )

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = RequirementAgent(store, gateway)
        try:
            await agent.run(FIXTURES[0])
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="schema validation"):
        asyncio.run(run())


def test_agent_rejects_unknown_test_type() -> None:
    """Model output with an unknown suggested_test_type fails loud."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _valid_analysis("x")
        payload["suggested_test_types"] = ["functional", "made-up-type"]
        return httpx.Response(200, json=_assistant(payload))

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> None:
        gateway = _gateway(handler)
        agent = RequirementAgent(store, gateway)
        try:
            await agent.run(FIXTURES[0])
        finally:
            await gateway.aclose()

    with pytest.raises(ValueError, match="unknown suggested_test_types"):
        asyncio.run(run())


def test_agent_tolerates_markdown_fence() -> None:
    """Model output wrapped in a markdown fence is still parsed."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = _valid_analysis("x")
        fenced = "```json\n" + json.dumps(payload) + "\n```"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": fenced}}]},
        )

    store = InMemoryPromptStore([PROMPT_SPEC])

    async def run() -> RequirementAnalysis:
        gateway = _gateway(handler)
        agent = RequirementAgent(store, gateway)
        try:
            result = await agent.run(FIXTURES[0])
            return result.analysis
        finally:
            await gateway.aclose()

    analysis = asyncio.run(run())
    assert analysis.summary
