"""LLM gateway — the only code allowed to call the model (build bible §31.1).

- OpenAI-compatible ``/chat/completions`` (LM Studio / llama.cpp / Ollama).
- Non-streaming (:meth:`LLMGateway.chat`) and streaming
  (:meth:`LLMGateway.chat_stream`).
- **Token accounting on every call**: ``usage`` from the server when
  reported, else an estimate; each call emits one structured ``ai_call`` log
  record — ``agent, model, tokens_in, tokens_out, latency_ms, retries,
  redactions, input_hash`` — the exact payload an ``ai_actions`` row is made
  from (§31.1 "every call records … into ai_actions", §31.5).
- **Reliability (§31.1)**: per-call timeout (default 120 s), one retry on
  transport errors, otherwise a hard :class:`LLMError` with a clear message —
  no silent model-swap.
- **Budgets (§9)**: the input budget is enforced here — the one door — by
  estimating prompt tokens and failing loud with
  :class:`LLMInputBudgetError` when the budget is exceeded (never a silent
  truncation). Budgets, temperature, timeouts and retries are tunable via
  the ``AI_*`` environment variables (see :mod:`qa_copilot_ai.config`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

import httpx

from .config import load_extra_body, load_model_settings
from .redaction import DEFAULT_REDACTOR, Redactor

logger = logging.getLogger("qa_copilot_ai")

#: §31.1 — per-call timeout (local inference is slow by definition).
#: Built-in default; overridable at construction time with the
#: ``AI_TIMEOUT_S`` / ``AI_CONNECT_TIMEOUT_S`` environment variables.
DEFAULT_TIMEOUT_S = 12000.0
CONNECT_TIMEOUT_S = 100.0
#: Rough fallback estimate (BPE ≈ 4 chars/token for English) — used only
#: when the server does not report ``usage``.
_CHARS_PER_TOKEN = 4


class LLMError(RuntimeError):
    """A model call failed after all retries (clear error, no model-swap)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMInputBudgetError(LLMError):
    """The prompt exceeds the input token budget — no model call was made.

    Raise :data:`~qa_copilot_ai.config.ModelSettings.max_input_tokens` (or
    the prompt's ``input_budget``) to allow larger context, or shorten the
    input. Failing loud is deliberate (§9): a truncated prompt would quietly
    degrade the analysis.
    """


@dataclass(frozen=True, slots=True)
class TokenUsage:
    tokens_in: int
    tokens_out: int
    #: ``"reported"`` (server usage) or ``"estimated"`` (char fallback).
    source: str = "reported"

    @classmethod
    def estimate(cls, prompt_text: str, completion_text: str) -> TokenUsage:
        return cls(
            tokens_in=max(1, len(prompt_text) // _CHARS_PER_TOKEN),
            tokens_out=max(1, len(completion_text) // _CHARS_PER_TOKEN),
            source="estimated",
        )


@dataclass(slots=True)
class AIChunk:
    """One streaming piece; the final chunk carries the :class:`TokenUsage`."""

    text: str = ""
    usage: TokenUsage | None = None


@dataclass(slots=True)
class AICallResult:
    """Everything an ``ai_actions`` audit row needs about one model call."""

    agent: str
    model: str
    text: str
    usage: TokenUsage
    latency_ms: int
    redactions: int
    retries: int
    input_hash: str

    def audit_dict(self) -> dict[str, object]:
        """The ``ai_actions`` / ``ai_call`` payload (§31.1, §31.5)."""
        return {
            "agent": self.agent,
            "model": self.model,
            "tokens_in": self.usage.tokens_in,
            "tokens_out": self.usage.tokens_out,
            "usage_source": self.usage.source,
            "latency_ms": self.latency_ms,
            "redactions": self.redactions,
            "retries": self.retries,
            "input_hash": self.input_hash,
        }


def _messages_text(messages: Sequence[dict[str, str]]) -> str:
    return "\n".join(message.get("content", "") for message in messages)


def _input_hash(messages: Sequence[dict[str, str]]) -> str:
    """Stable hash of the redacted prompt (→ ``ai_actions.input_hash``)."""
    payload = json.dumps(list(messages), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _usage_from(payload: dict[str, object]) -> TokenUsage | None:
    raw = payload.get("usage")
    if isinstance(raw, dict):
        prompt_tokens = raw.get("prompt_tokens")
        completion_tokens = raw.get("completion_tokens")
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            return TokenUsage(tokens_in=prompt_tokens, tokens_out=completion_tokens)
    return None


class LLMGateway:
    """Async client for the local OpenAI-compatible LLM endpoint (§31.1).

    Inject a ``transport`` (e.g. ``httpx.MockTransport``) for tests; in
    production the default transport hits ``LLM_BASE_URL``.

    ``extra_body`` merges server-specific fields into every request body
    (e.g. llama.cpp/LM Studio's ``chat_template_kwargs`` to disable Qwen3
    thinking); canonical fields (``model``, ``messages``, ``stream`` …)
    always win over it.

    ``timeout`` / ``connect_timeout`` / ``max_retries`` / ``extra_body`` left
    unset resolve from the ``AI_*`` environment variables (see
    :mod:`qa_copilot_ai.config`), which is how an operator tunes them without
    code changes.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        redactor: Redactor | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = None,
        max_retries: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        extra_body: Mapping[str, object] | None = None,
    ) -> None:
        if not base_url or not model:
            raise ValueError("base_url and model are required (§31.1)")
        settings = load_model_settings()
        resolved_timeout = settings.timeout_s if timeout is None else timeout
        resolved_connect = (
            settings.connect_timeout_s if connect_timeout is None else connect_timeout
        )
        self._max_retries = settings.max_retries if max_retries is None else max_retries
        if self._max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._base_url = base_url.rstrip("/")
        self.model = model
        self._redactor = redactor or DEFAULT_REDACTOR
        self._timeout = httpx.Timeout(resolved_timeout, connect=resolved_connect)
        self._transport = transport
        self._extra_body = dict(extra_body) if extra_body is not None else load_extra_body()
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self._transport is not None:
                self._client = httpx.AsyncClient(
                    base_url=self._base_url,
                    timeout=self._timeout,
                    transport=self._transport,
                )
            else:
                self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> LLMGateway:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        agent: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        max_input_tokens: int | None = None,
    ) -> AICallResult:
        """One non-streaming call; returns the full result + accounting.

        ``max_input_tokens`` (the prompt's ``input_budget`` or the
        ``AI_MAX_INPUT_TOKENS`` default) caps the estimated prompt size —
        exceeding it raises :class:`LLMInputBudgetError` before any call.
        """
        started = time.perf_counter()
        body, redacted_messages, redactions = self._build_body(
            messages,
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
            max_input_tokens=max_input_tokens,
        )
        response, retries = await self._post(body)
        payload: dict[str, object] = response.json()
        text = self._extract_content(payload)
        usage = _usage_from(payload) or TokenUsage.estimate(_messages_text(redacted_messages), text)
        result = AICallResult(
            agent=agent,
            model=self.model,
            text=text,
            usage=usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            redactions=redactions,
            retries=retries,
            input_hash=_input_hash(redacted_messages),
        )
        self._log_call(result, stream=False)
        return result

    async def chat_stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        agent: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        max_input_tokens: int | None = None,
    ) -> AsyncIterator[AIChunk]:
        """Streaming call: incremental text chunks, final chunk carries usage.

        Exhaust the stream for the ``ai_call`` audit record to be emitted
        (agents always consume fully; §31.1 streams every response). The
        input budget is checked up front, exactly like :meth:`chat`.
        """
        started = time.perf_counter()
        body, redacted_messages, redactions = self._build_body(
            messages,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            max_input_tokens=max_input_tokens,
        )
        response, retries = await self._post(body)
        pieces: list[str] = []
        usage: TokenUsage | None = None
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue  # keep-alive / malformed line — never fatal mid-stream
            if not isinstance(chunk, dict):
                continue
            if usage is None:
                usage = _usage_from(chunk)
            choices = chunk.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta")
                piece = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(piece, str) and piece:
                    pieces.append(piece)
                    yield AIChunk(text=piece)
        text = "".join(pieces)
        if usage is None:
            usage = TokenUsage.estimate(_messages_text(redacted_messages), text)
        result = AICallResult(
            agent=agent,
            model=self.model,
            text=text,
            usage=usage,
            latency_ms=int((time.perf_counter() - started) * 1000),
            redactions=redactions,
            retries=retries,
            input_hash=_input_hash(redacted_messages),
        )
        self._log_call(result, stream=True)
        yield AIChunk(usage=usage)

    def _build_body(
        self,
        messages: Sequence[dict[str, str]],
        *,
        stream: bool,
        temperature: float,
        max_tokens: int,
        max_input_tokens: int | None = None,
    ) -> tuple[dict[str, object], list[dict[str, str]], int]:
        redacted_messages, redactions = self._redactor.redact_messages(messages)
        if max_input_tokens is not None:
            estimated = max(1, len(_messages_text(redacted_messages)) // _CHARS_PER_TOKEN)
            if estimated > max_input_tokens:
                raise LLMInputBudgetError(
                    f"estimated input {estimated:,} tokens exceeds the budget of "
                    f"{max_input_tokens:,} — shorten the prompt or raise "
                    f"AI_MAX_INPUT_TOKENS (see qa_copilot_ai.config)"
                )
        body: dict[str, object] = dict(self._extra_body)
        body.update(
            {
                "model": self.model,
                "messages": redacted_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": stream,
            }
        )
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body, redacted_messages, redactions

    async def _post(self, body: dict[str, object]) -> tuple[httpx.Response, int]:
        """POST with §31.1 retry: one retry on transport errors only."""
        client = self._get_client()
        for attempt in range(self._max_retries + 1):
            try:
                response = await client.post("/chat/completions", json=body)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise LLMError(
                        f"LLM transport error after {attempt + 1} attempt(s) "
                        f"at {self._base_url}: {exc}"
                    ) from exc
                continue  # retry, then the loop ends and raises below
            if response.status_code >= 400:
                detail = response.text[:500]
                raise LLMError(
                    f"LLM HTTP {response.status_code}: {detail}", status=response.status_code
                )
            return response, attempt
        raise LLMError(f"LLM call failed at {self._base_url} after exhausting retries")

    @staticmethod
    def _extract_content(payload: dict[str, object]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError(f"LLM response has no choices: {json.dumps(payload)[:300]}")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMError(f"LLM response has no text content: {json.dumps(payload)[:300]}")
        return content

    @staticmethod
    def _log_call(result: AICallResult, *, stream: bool) -> None:
        record = result.audit_dict()
        record["stream"] = stream
        logger.info("ai_call", extra=record)


__all__ = [
    "AIChunk",
    "AICallResult",
    "CONNECT_TIMEOUT_S",
    "DEFAULT_TIMEOUT_S",
    "LLMError",
    "LLMGateway",
    "LLMInputBudgetError",
    "TokenUsage",
]
