"""Environment-driven AI tuning (build bible §31.1, §9 "Budgets").

The single place an operator edits AI parameters **without touching code** —
token budgets, sampling temperature, timeouts, retries. Every value is
overridable with an environment variable (or a repo ``.env`` line, the same
file as ``LLM_BASE_URL``):

    AI_MAX_INPUT_TOKENS    hard cap on estimated prompt tokens per call (default 60000)
    AI_MAX_OUTPUT_TOKENS   ``max_tokens`` fallback when a prompt has no
    ``output_budget`` (default 40000)
    AI_TEMPERATURE         sampling temperature fallback (default 0.3)
    AI_TIMEOUT_S           per-call timeout in seconds (default 12000 — local inference is slow)
    AI_CONNECT_TIMEOUT_S   connect timeout in seconds (default 100)
    AI_MAX_RETRIES         retries on transport errors (default 1)
    AI_EXTRA_BODY          extra chat-completions body fields (JSON object), e.g.
    ``{"chat_template_kwargs": {"enable_thinking": false}}`` to disable Qwen3
    thinking (LM Studio / llama.cpp) — thinking consumes 10k–30k+ output tokens
    and can starve the final answer (default: no extra fields)

Precedence: a prompt version's own front-matter/DB values
(``input_budget`` / ``output_budget`` / ``temperature`` in
``packages/ai/prompts/*.md``) still win when set; these env values are the
shared fallback. Input-budget enforcement lives in the gateway — the one
door for model calls (§31.1) — and fails loud with
:class:`~qa_copilot_ai.gateway.LLMInputBudgetError` when the prompt exceeds
the budget.

Invalid values raise ``ValueError`` at construction — a mistuned budget must
fail loud, never silently shrink or grow the model's contract.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Resolved AI parameters (token budgets, sampling, timeouts, retries).

    Defaults match the build bible §9 budgets: 60k input / 40k output.
    """

    max_input_tokens: int = 60_000
    max_output_tokens: int = 40_000
    temperature: float = 0.3
    timeout_s: float = 12_000.0
    connect_timeout_s: float = 100.0
    max_retries: int = 1

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> ModelSettings:
        """Build settings from *environ* (default: the process environment).

        Unset variables keep the dataclass defaults; invalid values raise
        ``ValueError`` naming the offending variable.
        """
        env = os.environ if environ is None else environ
        # Slot dataclasses keep defaults on the instance, so read them from
        # ``cls()`` — ``cls.field`` is a member_descriptor, not the default.
        defaults = cls()
        return cls(
            max_input_tokens=_int_env(env, "AI_MAX_INPUT_TOKENS", defaults.max_input_tokens),
            max_output_tokens=_int_env(env, "AI_MAX_OUTPUT_TOKENS", defaults.max_output_tokens),
            temperature=_float_env(
                env, "AI_TEMPERATURE", defaults.temperature, minimum=0.0, maximum=2.0
            ),
            timeout_s=_float_env(env, "AI_TIMEOUT_S", defaults.timeout_s, minimum=0.0),
            connect_timeout_s=_float_env(
                env, "AI_CONNECT_TIMEOUT_S", defaults.connect_timeout_s, minimum=0.0
            ),
            max_retries=_int_env(env, "AI_MAX_RETRIES", defaults.max_retries, minimum=0),
        )


def load_model_settings() -> ModelSettings:
    """Current AI parameters from the process environment (see module doc)."""
    return ModelSettings.from_env()


def load_extra_body(environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """Extra chat-completions body fields from ``AI_EXTRA_BODY`` (JSON object).

    Server-specific fields merged into every model call by
    :class:`~qa_copilot_ai.gateway.LLMGateway` when its ``extra_body`` keyword
    is not passed (canonical fields such as ``model`` / ``messages`` still win).
    The canonical use is disabling Qwen3 thinking on LM Studio / llama.cpp —
    ``{"chat_template_kwargs": {"enable_thinking": false}}`` — because thinking
    consumes 10k–30k+ output tokens and can starve the final answer.

    Unset/blank → ``{}``; invalid JSON or a non-object value raises
    ``ValueError`` (fail loud — a mistuned body must never silently change the
    model's contract).
    """
    env = os.environ if environ is None else environ
    raw = (env.get("AI_EXTRA_BODY") or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AI_EXTRA_BODY must be a JSON object (got {raw!r}): {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"AI_EXTRA_BODY must be a JSON object (got {type(value).__name__}: {raw!r})"
        )
    return value


def _int_env(env: Mapping[str, str], name: str, default: int, *, minimum: int = 1) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer (got {raw!r})") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum} (got {value})")
    return value


def _float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be a number (got {raw!r})") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum:g} (got {value:g})")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum:g} (got {value:g})")
    return value


def load_dotenv(path: str | Path) -> int:
    """Minimal ``.env`` loader (no dependency): ``KEY=VALUE`` lines, ``#`` comments.

    Existing process environment variables always win (``setdefault``), so
    a shell export overrides the file. Returns the number of keys set.
    Missing files are a no-op (returns 0).
    """
    env_file = Path(path)
    if not env_file.exists():
        return 0
    set_count = 0
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue  # existing process environment always wins
        os.environ[key] = value.strip().strip('"').strip("'")
        set_count += 1
    return set_count


__all__ = ["ModelSettings", "load_dotenv", "load_extra_body", "load_model_settings"]
