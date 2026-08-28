"""S0.6 AI gateway — unit tests against a fake OpenAI-compatible server.

Exit criterion (build bible §19 S0.6): "unit tests green against a fake
server; one live call logs ``tokens_in``/``tokens_out``." The live call is
``scripts/llm_live_check.py``; here the gateway contract is verified against
``httpx.MockTransport`` (no network, no model).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence

import httpx
import pytest
from qa_copilot_ai import (
    AICallResult,
    InMemoryPromptStore,
    LLMError,
    LLMGateway,
    PromptNotFound,
    PromptRenderError,
    PromptSpec,
    Redactor,
    TokenUsage,
    render_prompt,
)

MESSAGES = [{"role": "user", "content": "Say hello."}]


def _assistant(text: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


def _gateway(
    handler: Callable[[httpx.Request], httpx.Response], max_retries: int = 1
) -> LLMGateway:
    return LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=max_retries,
        transport=_AsyncMockTransport(handler),
    )


class _AsyncMockTransport(httpx.AsyncBaseTransport):
    """Async-transport shim so ``AsyncClient`` accepts the fake handler
    (httpx.MockTransport is sync-only and breaks AsyncClient streaming)."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


class _AsyncByteStream(httpx.AsyncByteStream):
    """Async byte stream for streamed fake responses (SSE lines as chunks)."""

    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


# --- redaction (build bible §31.5, §31.7: secret leaks = 0) ------------------


def test_redactor_bearer_token() -> None:
    out = Redactor().redact("Authorization: Bearer abc123XYZ789token")
    assert out.text == "Authorization: Bearer ***REDACTED***"
    assert out.count == 1


def test_redactor_github_and_openai_keys() -> None:
    out = Redactor().redact("ghp_ABCDEFGHIJKLMNOPQRSTUVWX and sk-abcdefghijklmnop123456789")
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWX" not in out.text
    assert "sk-abcdefghijklmnop123456789" not in out.text
    assert out.count == 2


def test_redactor_connection_string_password() -> None:
    out = Redactor().redact("DATABASE_URL=postgresql://qa:secret99@localhost:5432/db")
    assert "secret99" not in out.text
    assert "postgresql://qa:***REDACTED***@localhost:5432/db" in out.text


def test_redactor_api_key_assignment() -> None:
    out = Redactor().redact("api_key = supersecretvalue123")
    assert "supersecretvalue123" not in out.text
    assert out.count == 1


def test_redactor_clean_text_untouched_and_idempotent() -> None:
    text = "The checkout total reflects item prices and discount."
    first = Redactor().redact(text)
    assert first.text == text and first.count == 0
    second = Redactor().redact(first.text)
    assert second.text == text and second.count == 0


# --- prompt registry (build bible §31.6) --------------------------------------


def test_in_memory_store_latest_and_pinned() -> None:
    store = InMemoryPromptStore(
        [
            PromptSpec(name="demo", version=1, body="v1"),
            PromptSpec(name="demo", version=2, body="v2"),
        ]
    )
    assert store.get("demo").version == 2
    assert store.get("demo", version=1).body == "v1"
    with pytest.raises(PromptNotFound):
        store.get("demo", version=9)
    with pytest.raises(PromptNotFound):
        store.get("missing")


def test_render_prompt_substitutes_variables() -> None:
    spec = PromptSpec(name="t", version=1, body="Analyze {{story}} against {{rules}}.")
    assert (
        render_prompt(spec, story="login", rules="auth spec") == "Analyze login against auth spec."
    )


def test_render_prompt_missing_variable_fails_loud() -> None:
    spec = PromptSpec(name="t", version=1, body="Analyze {{story}}.")
    with pytest.raises(PromptRenderError, match="story"):
        render_prompt(spec)


# --- gateway: non-streaming ----------------------------------------------------


def test_chat_non_streaming_reports_usage() -> None:
    async def run() -> AICallResult:
        gateway = _gateway(
            lambda request: httpx.Response(200, json=_assistant("Hello from fake LLM."))
        )
        try:
            return await gateway.chat(MESSAGES, agent="unit-test")
        finally:
            await gateway.aclose()

    result = asyncio.run(run())
    assert result.text == "Hello from fake LLM."
    assert result.usage == TokenUsage(tokens_in=12, tokens_out=7, source="reported")
    assert result.input_hash
    assert result.model == "fake-model"


def test_chat_emits_ai_call_log_record(caplog: pytest.LogCaptureFixture) -> None:
    async def run() -> None:
        gateway = _gateway(lambda request: httpx.Response(200, json=_assistant("ok")))
        try:
            await gateway.chat(MESSAGES, agent="unit-test")
        finally:
            await gateway.aclose()

    with caplog.at_level(logging.INFO, logger="qa_copilot_ai"):
        asyncio.run(run())
    record = next(r for r in caplog.records if r.msg == "ai_call")
    fields = record.__dict__
    assert fields["agent"] == "unit-test"
    assert fields["model"] == "fake-model"
    assert fields["tokens_in"] == 12
    assert fields["tokens_out"] == 7
    assert fields["latency_ms"] >= 0
    assert fields["stream"] is False


def test_redaction_applied_to_wire_and_counted() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["content"] = body["messages"][0]["content"]
        return httpx.Response(200, json=_assistant("ok"))

    async def run() -> AICallResult:
        gateway = _gateway(handler)
        try:
            return await gateway.chat(
                [{"role": "user", "content": "token is Bearer abc123XYZ789token"}],
                agent="unit-test",
            )
        finally:
            await gateway.aclose()

    result = asyncio.run(run())
    assert "abc123XYZ789token" not in seen["content"]
    assert "***REDACTED***" in seen["content"]
    assert result.redactions == 1


def test_chat_extra_body_merged_into_request() -> None:
    """``extra_body`` fields reach the wire; canonical fields always win
    (e.g. Qwen3 thinking off: ``chat_template_kwargs.enable_thinking``)."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_assistant("ok"))

    async def run() -> None:
        gateway = LLMGateway(
            "http://llm.test/v1",
            "fake-model",
            transport=_AsyncMockTransport(handler),
            extra_body={"chat_template_kwargs": {"enable_thinking": False}, "model": "evil"},
        )
        try:
            await gateway.chat(MESSAGES, agent="unit-test")
        finally:
            await gateway.aclose()

    asyncio.run(run())
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["model"] == "fake-model"  # canonical field not overridden


# --- gateway: streaming (build bible §31.1 "stream responses") ----------------


def test_chat_stream_assembles_text_and_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        lines = (
            'data: {"choices": [{"delta": {"content": "Hel"}}]}\n\n',
            'data: {"choices": [{"delta": {"content": "lo!"}}]}\n\n',
            'data: {"choices": [{"delta": {}}], '
            '"usage": {"prompt_tokens": 5, "completion_tokens": 2}}\n\n',
            "data: [DONE]\n\n",
        )
        return httpx.Response(
            200,
            stream=_AsyncByteStream([line.encode() for line in lines]),
            headers={"content-type": "text/event-stream"},
        )

    async def run() -> str:
        gateway = _gateway(handler)
        try:
            chunks = [chunk async for chunk in gateway.chat_stream(MESSAGES, agent="unit-test")]
        finally:
            await gateway.aclose()
        final = chunks[-1]
        assert final.text == "" and final.usage is not None
        assert final.usage.tokens_in == 5
        assert final.usage.tokens_out == 2
        return "".join(c.text for c in chunks)

    assert asyncio.run(run()) == "Hello!"


def test_chat_stream_estimates_usage_when_server_omits_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        lines = (
            'data: {"choices": [{"delta": {"content": "Hello! "}}]}\n\n',
            "data: [DONE]\n\n",
        )
        return httpx.Response(200, stream=_AsyncByteStream([line.encode() for line in lines]))

    async def run() -> TokenUsage:
        gateway = _gateway(handler)
        try:
            chunks = [chunk async for chunk in gateway.chat_stream(MESSAGES, agent="unit-test")]
        finally:
            await gateway.aclose()
        final = chunks[-1]
        assert final.usage is not None
        return final.usage

    usage = asyncio.run(run())
    assert usage.source == "estimated"
    assert usage.tokens_out >= 1


# --- gateway: reliability (build bible §31.1) ---------------------------------


def test_retry_on_transport_error_then_success() -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.ConnectError("connection refused (fake)")
        return httpx.Response(200, json=_assistant("ok"))

    async def run() -> AICallResult:
        gateway = _gateway(handler, max_retries=1)
        try:
            return await gateway.chat(MESSAGES, agent="unit-test")
        finally:
            await gateway.aclose()

    result = asyncio.run(run())
    assert state["calls"] == 2
    assert result.retries == 1


def test_no_retry_without_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down (fake)")

    async def run() -> None:
        gateway = _gateway(handler, max_retries=0)
        try:
            await gateway.chat(MESSAGES, agent="unit-test")
        finally:
            await gateway.aclose()

    with pytest.raises(LLMError, match="transport error"):
        asyncio.run(run())


def test_http_error_raises_clear_llm_error_without_retry() -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(400, json={"error": {"message": "model not loaded"}})

    async def run() -> None:
        gateway = _gateway(handler, max_retries=1)
        try:
            await gateway.chat(MESSAGES, agent="unit-test")
        finally:
            await gateway.aclose()

    with pytest.raises(LLMError) as excinfo:
        asyncio.run(run())
    error = excinfo.value
    assert isinstance(error, LLMError)
    assert error.status == 400
    assert state["calls"] == 1
