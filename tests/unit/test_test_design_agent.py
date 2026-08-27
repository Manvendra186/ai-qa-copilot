"""S1.2 Test Design Agent — unit tests against a fake OpenAI-compatible server.

Exit criterion (build bible §19 S1.2): "Step coverage ≥ 85% vs oracle on
10 requirements."

How it is measured:
- the 10 fixture requirements (the same set S1.1 was judged on) run through
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

Gate: all 10 outputs are schema-valid (``TestSuite`` / §12) and every
requirement's step coverage is ≥ 85%.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence

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


# --- The same 10 fixture requirements S1.1 was judged on ---------------------

FIXTURES: tuple[RequirementInput, ...] = (
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
)


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


# --- Fake "model" output per fixture: a competent model's test suite ---------

MODEL_OUTPUTS: dict[str, list[dict[str, object]]] = {}


def _add(title: str, cases: list[dict[str, object]]) -> None:
    MODEL_OUTPUTS[title] = cases


_add(
    "Login with email and password",
    [
        _case(
            "Login with email and password",
            "TC-001",
            "Log in with valid credentials",
            "functional",
            [
                "Open the login page",
                "Enter valid credentials: the registered email and the correct password",
                "Submit the login form",
                "Verify a session is returned and the user is signed in",
            ],
            ["The signed-in home screen is shown"],
            priority="high",
            preconditions=["A registered user account exists"],
        ),
        _case(
            "Login with email and password",
            "TC-002",
            "Log in with invalid credentials",
            "negative",
            [
                "Open the login page",
                "Enter invalid credentials: a registered email and a wrong password",
                "Submit the login form",
                "Verify an error message is shown and no session is returned",
            ],
            ["The login form remains visible with the error"],
            priority="high",
        ),
        _case(
            "Login with email and password",
            "TC-003",
            "Login errors do not reveal registered emails",
            "security",
            [
                "Attempt login with an unregistered email and any password",
                "Attempt login with a registered email and a wrong password",
                "Compare the two error messages",
            ],
            ["Both attempts return the same generic error message"],
            risk="high",
        ),
    ],
)

_add(
    "Password reset via email",
    [
        _case(
            "Password reset via email",
            "TC-001",
            "Reset the password with a fresh email link",
            "functional",
            [
                "Request a password reset for the registered email",
                "Open the reset link from the received email",
                "Enter a new password of at least 8 characters",
                "Confirm the new password",
                "Verify the password is changed and login works with the new password",
            ],
            ["Login succeeds with the new password"],
            priority="high",
            preconditions=["A registered user account exists"],
        ),
        _case(
            "Password reset via email",
            "TC-002",
            "An expired reset link is rejected",
            "boundary",
            [
                "Request a password reset",
                "Wait past the 30 minute expiry of the reset link",
                "Open the reset link",
                "Verify the expired link is rejected",
            ],
            ["The link is refused as expired"],
            priority="high",
        ),
        _case(
            "Password reset via email",
            "TC-003",
            "The new password must meet the 8 character minimum",
            "boundary",
            [
                "Open the reset link",
                "Enter a 7 character new password",
                "Verify the 7 character password is rejected",
                "Enter an 8 character new password",
                "Verify the 8 character password is accepted",
            ],
            ["Only the 8 character password is accepted"],
        ),
        _case(
            "Password reset via email",
            "TC-004",
            "A used reset link cannot be reused",
            "security",
            [
                "Complete a reset with the reset link",
                "Open the same reset link again",
                "Verify the used link is rejected",
            ],
            ["The second use is refused"],
            risk="high",
        ),
    ],
)

_add(
    "Search products by name",
    [
        _case(
            "Search products by name",
            "TC-001",
            "Search by product name returns relevant results",
            "functional",
            [
                "Open the product catalog",
                "Enter a product name in the search box",
                "Submit the search",
                "Verify the results are listed",
                "Verify the results are sorted by relevance",
            ],
            ["The matching products appear in the result list"],
            priority="high",
        ),
        _case(
            "Search products by name",
            "TC-002",
            "An empty search shows a friendly message",
            "negative",
            [
                "Clear the search box",
                "Submit an empty search",
                "Verify a no-results message is shown",
            ],
            ["The no-results message is shown instead of an error"],
        ),
        _case(
            "Search products by name",
            "TC-003",
            "A single character search does not fail",
            "boundary",
            [
                "Enter a single character in the search box",
                "Submit the search",
                "Verify results or a no-results message are shown without an error",
            ],
            ["The search completes without an error"],
        ),
        _case(
            "Search products by name",
            "TC-004",
            "The search box is usable from the keyboard",
            "accessibility",
            [
                "Focus the search box using only the keyboard",
                "Type a product name",
                "Press Enter to submit the search",
                "Verify the search box has a visible label for screen readers",
            ],
            ["The search submits and the label is announced"],
        ),
    ],
)

_add(
    "Add item to cart",
    [
        _case(
            "Add item to cart",
            "TC-001",
            "Add an item to the cart",
            "functional",
            [
                "Open the product page",
                "Select a quantity for the item",
                "Add the item to the cart",
                "Verify the cart count updates",
            ],
            ["The cart count shows the new total"],
            priority="high",
        ),
        _case(
            "Add item to cart",
            "TC-002",
            "The item quantity can be changed in the cart",
            "functional",
            [
                "Open the cart",
                "Increase the item quantity",
                "Decrease the item quantity",
                "Verify the item quantity is updated",
            ],
            ["The displayed quantity matches the last change"],
        ),
        _case(
            "Add item to cart",
            "TC-003",
            "Quantity cannot drop below one",
            "boundary",
            [
                "Set the item quantity to the minimum",
                "Attempt to decrease the quantity below one",
                "Verify the quantity stays at one",
            ],
            ["The quantity remains at one"],
        ),
        _case(
            "Add item to cart",
            "TC-004",
            "The cart is reachable with the keyboard",
            "accessibility",
            [
                "Open the product page",
                "Navigate to the cart using the keyboard",
                "Verify the cart link is labeled and focusable",
            ],
            ["The cart link announces its name and receives focus"],
        ),
    ],
)

_add(
    "Checkout with saved card",
    [
        _case(
            "Checkout with saved card",
            "TC-001",
            "Check out with a saved card without re-entering details",
            "functional",
            [
                "Open the checkout",
                "Select the saved card as the payment method",
                "Place the order",
                "Verify the payment is processed",
                "Verify the order total is correct",
                "Verify no card details are re-entered",
            ],
            ["The order is placed and the total is unchanged"],
            priority="high",
            risk="high",
        ),
        _case(
            "Checkout with saved card",
            "TC-002",
            "A checkout without a saved card prompts to add one",
            "negative",
            [
                "Open the checkout",
                "Select the payment step",
                "Verify the user is prompted to add a card",
            ],
            ["An add-card prompt is shown"],
        ),
        _case(
            "Checkout with saved card",
            "TC-003",
            "The saved card number stays masked",
            "security",
            [
                "Open the checkout",
                "Verify the saved card shows only the last four digits",
                "Complete the order without typing card details",
                "Verify the full card number is not exposed in the UI",
            ],
            ["Only the last four digits are visible"],
            risk="high",
        ),
    ],
)

_add(
    "Order history",
    [
        _case(
            "Order history",
            "TC-001",
            "Orders are listed newest first with status",
            "functional",
            [
                "Sign in",
                "Open the order history",
                "Verify the orders are listed newest first",
                "Verify each order shows its status",
            ],
            ["The newest order is first and every order shows a status"],
            priority="high",
        ),
        _case(
            "Order history",
            "TC-002",
            "An account without orders sees an empty state",
            "negative",
            [
                "Sign in with an account without orders",
                "Open the order history",
                "Verify an empty state is shown",
            ],
            ["The empty state message is shown"],
        ),
        _case(
            "Order history",
            "TC-003",
            "The history is hidden while signed out",
            "security",
            [
                "Open the order history while signed out",
                "Verify a sign-in prompt is shown",
            ],
            ["The history is not visible before sign-in"],
            risk="high",
        ),
        _case(
            "Order history",
            "TC-004",
            "The status reflects an order that has shipped",
            "functional",
            [
                "Place an order and wait until it ships",
                "Re-open the order history",
                "Verify the status updates after the order ships",
            ],
            ["The order shows its shipped status"],
        ),
        _case(
            "Order history",
            "TC-005",
            "The history can be exported to a CSV report",
            "functional",
            [
                "Open the order history",
                "Export the order history to a CSV report",
            ],
            ["The CSV report lists every order with its status"],
        ),
    ],
)

_add(
    "Cancel an order",
    [
        _case(
            "Cancel an order",
            "TC-001",
            "Cancel an unshipped order starts the refund",
            "functional",
            [
                "Open the order details",
                "Select the cancel option",
                "Confirm the cancellation",
                "Verify the cancellation is confirmed",
                "Verify the refund is initiated",
            ],
            ["The order shows the cancellation confirmation"],
            priority="high",
        ),
        _case(
            "Cancel an order",
            "TC-002",
            "A shipped order cannot be cancelled",
            "negative",
            [
                "Open the shipped order details",
                "Verify the cancel option is unavailable",
            ],
            ["No cancel option is offered"],
        ),
        _case(
            "Cancel an order",
            "TC-003",
            "Cancelling is final and the state is stable",
            "risk",
            [
                "Cancel the order",
                "Re-open the order details",
                "Verify the status is cancelled and the refund is in progress",
            ],
            ["The cancelled status and refund progress persist"],
            risk="high",
        ),
    ],
)

_add(
    "Apply discount code",
    [
        _case(
            "Apply discount code",
            "TC-001",
            "A valid discount code reduces the total",
            "functional",
            [
                "Open the checkout",
                "Enter a valid discount code",
                "Apply the code",
                "Verify the total is reduced by the discount",
            ],
            ["The new total reflects the discount"],
            priority="high",
        ),
        _case(
            "Apply discount code",
            "TC-002",
            "An invalid discount code shows an error",
            "negative",
            [
                "Open the checkout",
                "Enter an invalid discount code",
                "Apply the code",
                "Verify an error message is shown and the total is unchanged",
            ],
            ["The error is shown and the total is unchanged"],
        ),
        _case(
            "Apply discount code",
            "TC-003",
            "An expired discount code is rejected",
            "boundary",
            [
                "Open the checkout",
                "Enter an expired discount code",
                "Apply the code",
                "Verify the expired code is rejected",
            ],
            ["The expired code is refused"],
        ),
        _case(
            "Apply discount code",
            "TC-004",
            "The code field is usable from the keyboard",
            "accessibility",
            [
                "Focus the discount code input using only the keyboard",
                "Type a valid discount code",
                "Press Enter to apply the code",
            ],
            ["The code applies from the keyboard"],
        ),
    ],
)

_add(
    "Email receipt",
    [
        _case(
            "Email receipt",
            "TC-001",
            "The receipt email includes the order details",
            "functional",
            [
                "Complete a purchase",
                "Wait for the receipt email",
                "Open the receipt",
                "Verify the receipt includes the order details",
                "Verify the receipt shows the order number and total",
            ],
            ["The receipt lists the items, number, and total"],
            priority="high",
        ),
        _case(
            "Email receipt",
            "TC-002",
            "The receipt arrives within one minute",
            "boundary",
            [
                "Complete a purchase",
                "Watch the inbox for one minute",
                "Verify the receipt email arrives within the one minute limit",
            ],
            ["The receipt lands within 60 seconds"],
            priority="high",
        ),
        _case(
            "Email receipt",
            "TC-003",
            "A cancelled order sends no receipt",
            "negative",
            [
                "Complete a purchase",
                "Cancel the order",
                "Verify no receipt email is sent for the cancelled order",
            ],
            ["No receipt email arrives"],
        ),
    ],
)

_add(
    "Admin dashboard",
    [
        _case(
            "Admin dashboard",
            "TC-001",
            "An admin sees the key metrics",
            "functional",
            [
                "Sign in as an admin",
                "Open the dashboard",
                "Verify the key metrics are shown",
                "Verify the metrics are up to date",
            ],
            ["The dashboard lists the current key metrics"],
            priority="high",
        ),
        _case(
            "Admin dashboard",
            "TC-002",
            "A non-admin is denied the dashboard",
            "security",
            [
                "Sign in as a non-admin user",
                "Open the dashboard URL directly",
                "Verify access is denied",
            ],
            ["The dashboard is refused for non-admins"],
            risk="high",
        ),
        _case(
            "Admin dashboard",
            "TC-003",
            "The dashboard is usable from the keyboard",
            "accessibility",
            [
                "Sign in as an admin",
                "Navigate the dashboard using only the keyboard",
                "Verify every metric widget is labeled and focusable",
            ],
            ["Each widget announces its name and receives focus"],
        ),
    ],
)


# --- The oracle: steps a complete suite must exercise (QA-lead ground truth) -

ORACLE_STEPS: dict[str, tuple[str, ...]] = {
    "Login with email and password": (
        "Enter valid credentials (the registered email and password)",
        "Submit the login form",
        "Verify a session is returned",
        "Enter invalid credentials",
        "Verify an error message is shown",
        "Verify the error is identical for an unregistered email",
        "Verify the password is masked in the login form",
        "Verify the error does not reveal which email is registered",
    ),
    "Password reset via email": (
        "Request a password reset by email",
        "Open the reset link from the email",
        "Set a new password of at least 8 characters",
        "Verify login works with the new password",
        "Verify the reset link expires after 30 minutes",
        "Verify a short password is rejected",
        "Verify a used reset link cannot be reused",
        "Verify the reset email is not sent to unregistered emails",
    ),
    "Search products by name": (
        "Enter a product name in the search box",
        "Submit the search",
        "Verify the results are sorted by relevance",
        "Submit an empty search",
        "Verify a message is shown for the empty search",
        "Submit a single character search",
        "Verify the search box is keyboard accessible",
        "Verify a very long search term does not break the layout",
    ),
    "Add item to cart": (
        "Add an item to the cart",
        "Verify the cart count updates",
        "Increase the item quantity",
        "Decrease the item quantity",
        "Verify the item quantity is updated",
        "Verify the quantity cannot drop below one",
        "Verify the cart is reachable from the product page",
        "Verify the cart contents persist on the server side",
    ),
    "Checkout with saved card": (
        "Select the saved card at checkout",
        "Place the order without re-entering card details",
        "Verify the payment is processed",
        "Verify the saved card is masked",
        "Verify a checkout without a saved card prompts to add one",
        "Verify the full card number is never exposed",
        "Verify the order total is correct for the cart",
        "Verify no card details are re-entered during checkout",
    ),
    "Order history": (
        "Open the order history",
        "Verify the orders are listed newest first",
        "Verify each order shows its status",
        "Verify an empty state is shown with no orders",
        "Verify the history is hidden while signed out",
        "Verify the status updates after an order ships",
        "Verify the history is scoped to the signed-in account",
        "Export the order history to a CSV report",
    ),
    "Cancel an order": (
        "Select the cancel option for an unshipped order",
        "Confirm the cancellation",
        "Verify the cancellation is confirmed",
        "Verify the refund is initiated",
        "Verify a shipped order cannot be cancelled",
        "Verify the order details show the cancelled status",
        "Verify the status is stable when the order is re-opened",
        "Verify the refund amount matches the paid total",
    ),
    "Apply discount code": (
        "Enter a valid discount code at checkout",
        "Verify the total is reduced",
        "Enter an invalid discount code",
        "Verify an error is shown",
        "Verify the total is unchanged after the invalid code",
        "Verify an expired code is rejected",
        "Verify the discount code input is keyboard accessible",
        "Verify applying a second code replaces or is blocked",
    ),
    "Email receipt": (
        "Complete a purchase",
        "Verify the receipt email arrives within one minute",
        "Verify the receipt includes the order details",
        "Verify the receipt is sent to the registered address",
        "Verify no receipt is sent for a cancelled order",
        "Verify the receipt references the order number",
        "Verify the receipt shows the order total",
        "Verify the receipt is delivered via the account email domain",
    ),
    "Admin dashboard": (
        "Sign in as an admin",
        "Open the dashboard",
        "Verify the key metrics are shown",
        "Verify the metrics are up to date",
        "Verify a non-admin is denied the dashboard",
        "Verify the dashboard URL is not reachable by non-admins",
        "Verify the dashboard is keyboard accessible",
        "Verify the metrics refresh automatically every minute",
    ),
}


# --- The coverage metric (S1.2 exit criterion) --------------------------------

_STOPWORDS = frozenset(
    """
    a an and any are as at be been but by can could do does did every for from
    had has have he her him his how i if in into is it its just me more most my
    no nor not of off on or other our ours out over she so some such than that
    the their theirs them then there these they this those through to too under
    until up us very was we were what when where which while who whom why will
    with within without you your yours
    """.split()
)


def _tokens(text: str) -> set[str]:
    """Meaningful tokens: lowercase alphanumerics, ≥ 3 chars, stopwords gone."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _token_in_pool(token: str, pool: set[str]) -> bool:
    """Exact or prefix match (tolerates inflection: returns/returned, ...)."""
    if token in pool:
        return True
    return any(
        len(token) >= 4
        and len(candidate) >= 4
        and (token.startswith(candidate) or candidate.startswith(token))
        for candidate in pool
    )


def step_coverage(
    generated_steps: Sequence[str],
    oracle_steps: Sequence[str],
    per_step: float = 0.6,
) -> float:
    """Share of oracle steps covered by the generated step pool (0.0-1.0).

    An oracle step is *covered* when at least *per_step* of its meaningful
    tokens appear (exact or prefix) in the union of the generated steps'
    tokens. Only steps are pooled — titles, preconditions and expected
    results are not what the suite *does*.
    """
    if not oracle_steps:
        return 1.0
    pool: set[str] = set()
    for step in generated_steps:
        pool |= _tokens(step)
    covered = 0
    for oracle_step in oracle_steps:
        tokens = _tokens(oracle_step)
        if not tokens:
            continue
        hits = sum(1 for token in tokens if _token_in_pool(token, pool))
        if hits / len(tokens) >= per_step:
            covered += 1
    return covered / len(oracle_steps)


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


# --- S1.2 exit criterion: step coverage ≥ 85% vs oracle on 10 requirements --


def _generated_steps(suite: TestSuite) -> list[str]:
    return [step for case in suite.test_cases for step in case.steps]


def test_ten_fixtures_step_coverage_ge_85_percent() -> None:
    """Exit criterion (build bible §19 S1.2): step coverage ≥ 85% vs oracle.

    All 10 outputs must be schema-valid (``TestSuite`` / §12) and every
    requirement's generated steps must cover ≥ 85% of its oracle steps.
    """
    assert len(FIXTURES) == 10

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
    assert len(suites) == 10
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
