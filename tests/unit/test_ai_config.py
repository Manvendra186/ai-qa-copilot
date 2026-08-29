"""AI tuning config (``qa_copilot_ai.config``) — environment-driven budgets.

Covers the centralized AI parameters (build bible §9 "Budgets", §31.1):

- ``ModelSettings`` defaults match the 60k input / 40k output budgets;
- ``AI_*`` env parsing, and invalid values failing loud (``ValueError``);
- ``load_dotenv`` semantics (the file fills gaps, process env always wins);
- gateway timeout / connect timeout / retries resolved from the env;
- the gateway input-budget gate (``LLMInputBudgetError``) — fail loud, never
  silently truncate, no model call is made;
- agent wiring: prompt front-matter wins, env fallbacks fill the gaps.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from collections.abc import Callable

import httpx
import pytest
from qa_copilot_ai import (
    InMemoryPromptStore,
    LLMGateway,
    LLMInputBudgetError,
    ModelSettings,
    PromptSpec,
    RequirementAgent,
    RequirementInput,
    load_dotenv,
    load_extra_body,
    load_model_settings,
)

Handler = Callable[[httpx.Request], httpx.Response]


class _AsyncMockTransport(httpx.AsyncBaseTransport):
    """Async-transport shim so ``AsyncClient`` accepts a sync fake handler."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        },
    )


def _gateway(handler: Handler | None = None, max_retries: int = 0) -> LLMGateway:
    return LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=max_retries,
        transport=_AsyncMockTransport(handler or _ok_handler),
    )


# --- ModelSettings defaults (build bible §9: 60k in / 40k out) -----------------


def test_model_settings_defaults_match_build_bible_budgets() -> None:
    settings = ModelSettings()
    assert settings.max_input_tokens == 60_000
    assert settings.max_output_tokens == 40_000
    assert settings.temperature == 0.3
    assert settings.timeout_s == 12_000.0
    assert settings.connect_timeout_s == 100.0
    assert settings.max_retries == 1


# --- AI_* environment parsing ----------------------------------------------------


def test_from_env_reads_all_ai_knobs() -> None:
    settings = ModelSettings.from_env(
        {
            "AI_MAX_INPUT_TOKENS": "120000",
            "AI_MAX_OUTPUT_TOKENS": "80000",
            "AI_TEMPERATURE": "0.5",
            "AI_TIMEOUT_S": "24000",
            "AI_CONNECT_TIMEOUT_S": "20.5",
            "AI_MAX_RETRIES": "2",
        }
    )
    assert settings.max_input_tokens == 120_000
    assert settings.max_output_tokens == 80_000
    assert settings.temperature == 0.5
    assert settings.timeout_s == 24_000.0
    assert settings.connect_timeout_s == 20.5
    assert settings.max_retries == 2


def test_from_env_unset_or_blank_variables_keep_defaults() -> None:
    settings = ModelSettings.from_env({"AI_TEMPERATURE": "  ", "AI_MAX_RETRIES": ""})
    assert settings == ModelSettings()


def test_load_model_settings_reads_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_TEMPERATURE", "0.9")
    monkeypatch.setenv("AI_MAX_OUTPUT_TOKENS", "41000")
    settings = load_model_settings()
    assert settings.temperature == 0.9
    assert settings.max_output_tokens == 41_000


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"AI_MAX_INPUT_TOKENS": "abc"}, "AI_MAX_INPUT_TOKENS must be an integer"),
        ({"AI_MAX_INPUT_TOKENS": "0"}, "AI_MAX_INPUT_TOKENS must be >= 1"),
        ({"AI_MAX_OUTPUT_TOKENS": "x"}, "AI_MAX_OUTPUT_TOKENS must be an integer"),
        ({"AI_TEMPERATURE": "3.5"}, "AI_TEMPERATURE must be <= 2"),
        ({"AI_TEMPERATURE": "-0.1"}, "AI_TEMPERATURE must be >= 0"),
        ({"AI_TIMEOUT_S": "nope"}, "AI_TIMEOUT_S must be a number"),
        ({"AI_CONNECT_TIMEOUT_S": "-5"}, "AI_CONNECT_TIMEOUT_S must be >= 0"),
        ({"AI_MAX_RETRIES": "-1"}, "AI_MAX_RETRIES must be >= 0"),
    ],
)
def test_invalid_ai_values_fail_loud(environ: dict[str, str], expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        ModelSettings.from_env(environ)


# --- load_dotenv (the repo .env bridge) -----------------------------------------


def test_load_dotenv_sets_missing_keys_and_strips_quotes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AI_TEST_DOTENV_KEY", raising=False)
    monkeypatch.delenv("AI_TEST_QUOTED_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment line\n\nAI_TEST_DOTENV_KEY=from-file\nAI_TEST_QUOTED_KEY="with spaces"\n',
        encoding="utf-8",
    )
    count = load_dotenv(env_file)
    assert count == 2
    assert os.environ["AI_TEST_DOTENV_KEY"] == "from-file"
    assert os.environ["AI_TEST_QUOTED_KEY"] == "with spaces"


def test_load_dotenv_never_overrides_process_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_TEST_PRIORITY_KEY", "shell-wins")
    env_file = tmp_path / ".env"
    env_file.write_text("AI_TEST_PRIORITY_KEY=from-file\n", encoding="utf-8")
    assert load_dotenv(env_file) == 0
    assert os.environ["AI_TEST_PRIORITY_KEY"] == "shell-wins"


def test_load_dotenv_missing_file_is_a_noop(tmp_path: pathlib.Path) -> None:
    assert load_dotenv(tmp_path / "no-such-file.env") == 0


# --- gateway: timeouts / retries from AI_* env -----------------------------------


def test_gateway_timeouts_and_retries_resolve_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_TIMEOUT_S", "30")
    monkeypatch.setenv("AI_CONNECT_TIMEOUT_S", "5")
    monkeypatch.setenv("AI_MAX_RETRIES", "2")
    gateway = LLMGateway("http://llm.test/v1", "fake-model")
    assert gateway._timeout.read == 30.0
    assert gateway._timeout.connect == 5.0
    assert gateway._max_retries == 2


def test_gateway_explicit_args_win_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_TIMEOUT_S", "30")
    monkeypatch.setenv("AI_MAX_RETRIES", "2")
    gateway = LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        timeout=99.0,
        connect_timeout=7.0,
        max_retries=0,
    )
    assert gateway._timeout.read == 99.0
    assert gateway._timeout.connect == 7.0
    assert gateway._max_retries == 0


# --- gateway: extra_body from AI_EXTRA_BODY env (Qwen3 thinking off) -------------


def test_load_extra_body_unset_or_blank_returns_empty_dict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_EXTRA_BODY", raising=False)
    assert load_extra_body() == {}
    monkeypatch.setenv("AI_EXTRA_BODY", "   ")
    assert load_extra_body() == {}


def test_load_extra_body_parses_json_object() -> None:
    value = load_extra_body(
        {"AI_EXTRA_BODY": '{"chat_template_kwargs": {"enable_thinking": false}}'}
    )
    assert value == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a":', "AI_EXTRA_BODY must be a JSON object"),
        ("[1, 2]", "AI_EXTRA_BODY must be a JSON object"),
        ('"just a string"', "AI_EXTRA_BODY must be a JSON object"),
    ],
)
def test_load_extra_body_invalid_values_fail_loud(raw: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        load_extra_body({"AI_EXTRA_BODY": raw})


def test_gateway_env_extra_body_reaches_the_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _ok_handler(request)

    gateway = LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=0,
        transport=_AsyncMockTransport(handler),
    )
    asyncio.run(gateway.chat([{"role": "user", "content": "hi"}], agent="unit-test"))
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured[0]["model"] == "fake-model"  # canonical fields still win


def test_gateway_explicit_extra_body_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_EXTRA_BODY", '{"from_env": true}')
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return _ok_handler(request)

    gateway = LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=0,
        transport=_AsyncMockTransport(handler),
        extra_body={"explicit": "wins"},
    )
    asyncio.run(gateway.chat([{"role": "user", "content": "hi"}], agent="unit-test"))
    assert captured[0]["explicit"] == "wins"
    assert "from_env" not in captured[0]


# --- gateway: input budget gate (fail loud, never truncate) ----------------------

LONG_CONTENT = "x" * 2000  # 2000 chars ≈ 500 estimated tokens (4 chars/token)


def test_chat_input_budget_exceeded_fails_loud_without_model_call() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return _ok_handler(request)

    gateway = _gateway(handler)
    with pytest.raises(LLMInputBudgetError, match="exceeds the budget"):
        asyncio.run(
            gateway.chat(
                [{"role": "user", "content": LONG_CONTENT}],
                agent="unit-test",
                max_input_tokens=100,
            )
        )
    assert calls["count"] == 0  # rejected before any model call


def test_chat_within_input_budget_succeeds() -> None:
    gateway = _gateway()
    result = asyncio.run(
        gateway.chat(
            [{"role": "user", "content": "Say hello."}],
            agent="unit-test",
            max_input_tokens=1000,
        )
    )
    assert result.text == "ok"


def test_chat_stream_input_budget_exceeded_fails_loud() -> None:
    gateway = _gateway()

    async def run() -> None:
        stream = gateway.chat_stream(
            [{"role": "user", "content": LONG_CONTENT}],
            agent="unit-test",
            max_input_tokens=100,
        )
        async for _ in stream:
            pass

    with pytest.raises(LLMInputBudgetError):
        asyncio.run(run())


# --- agents: prompt front-matter wins, env fallbacks fill the gaps ---------------


def _analysis_payload() -> dict[str, object]:
    return {
        "summary": "s",
        "actors": ["user"],
        "testable_criteria": ["c"],
        "preconditions": ["p"],
        "suggested_test_types": ["functional"],
        "risks": ["r"],
        "open_questions": [],
        "confidence": 0.8,
    }


def _analysis_handler(captured: list[dict[str, object]]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_analysis_payload()),
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    return handler


def _spec(
    *,
    input_budget: int | None = None,
    output_budget: int | None = None,
    temperature: float | None = None,
) -> PromptSpec:
    return PromptSpec(
        name="requirement-analyst",
        version=1,
        body="Analyze: {{title}} / {{content}} / {{acceptance_criteria}}",
        model_class="coder",
        input_budget=input_budget,
        output_budget=output_budget,
        schema_ref="requirement-analysis/v1",
        temperature=temperature,
    )


def _login_requirement(content: str = "Users can log in.") -> RequirementInput:
    return RequirementInput(
        title="Login",
        content=content,
        acceptance_criteria=("Valid credentials return a session",),
    )


async def _run_requirement(store: InMemoryPromptStore, handler: Handler | None) -> None:
    gateway = _gateway(handler)
    agent = RequirementAgent(store, gateway)
    try:
        await agent.run(_login_requirement(content="x" * 400 if handler is None else "x"))
    finally:
        await gateway.aclose()


def test_agent_falls_back_to_env_budgets_when_prompt_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Deterministic fallbacks regardless of the ambient environment.
    monkeypatch.delenv("AI_TEMPERATURE", raising=False)
    monkeypatch.delenv("AI_MAX_OUTPUT_TOKENS", raising=False)

    captured: list[dict[str, object]] = []
    store = InMemoryPromptStore([_spec()])

    async def run() -> None:
        await _run_requirement(store, _analysis_handler(captured))

    asyncio.run(run())
    body = captured[0]
    assert body["temperature"] == 0.3  # ModelSettings.temperature default
    assert body["max_tokens"] == 40_000  # ModelSettings.max_output_tokens default


def test_agent_prompt_front_matter_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_TEMPERATURE", "0.9")
    monkeypatch.setenv("AI_MAX_OUTPUT_TOKENS", "99999")

    captured: list[dict[str, object]] = []
    store = InMemoryPromptStore([_spec(input_budget=2048, output_budget=1024, temperature=0.2)])

    async def run() -> None:
        await _run_requirement(store, _analysis_handler(captured))

    asyncio.run(run())
    body = captured[0]
    assert body["temperature"] == 0.2  # prompt's own temperature wins
    assert body["max_tokens"] == 1024  # prompt's output_budget wins


def test_agent_enforces_prompt_input_budget() -> None:
    # input_budget=10 is tiny — the rendered prompt (~300 estimated tokens)
    # must be rejected before any model call.
    store = InMemoryPromptStore([_spec(input_budget=10)])

    async def run() -> None:
        await _run_requirement(store, None)

    with pytest.raises(LLMInputBudgetError, match="exceeds the budget"):
        asyncio.run(run())


def test_agent_env_input_budget_gate_blocks_oversized_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No input_budget in the prompt → the AI_MAX_INPUT_TOKENS env value is the
    # gate (env → load_model_settings → agent → gateway → fail loud).
    monkeypatch.setenv("AI_MAX_INPUT_TOKENS", "10")
    store = InMemoryPromptStore([_spec()])

    async def run() -> None:
        await _run_requirement(store, None)

    with pytest.raises(LLMInputBudgetError, match="exceeds the budget"):
        asyncio.run(run())
