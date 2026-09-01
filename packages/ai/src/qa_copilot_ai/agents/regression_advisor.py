"""Optional LLM regression advisor (S6.3, build bible §19 Phase 6).

S6.3's ranking is the **deterministic** :func:`qa_copilot_repository.recommend`
core — the S6.1 change-impact set joined with the S6.2 flaky/risk ranking. This
agent is strictly **optional**: it summarizes an already-computed
:class:`qa_copilot_domain.RecommendationSet` into a short, human-readable brief
(which tests to run first, and why). It never re-orders or re-ranks — the
deterministic core owns the ranking, and the advisor only adds a ``summary``
string on top (the §31.7 "the LLM never re-derives the deterministic answer"
rule, the S4.1/S4.2/S5.4 agent pattern).

The advisor degrades **safely**: if the model is unavailable, times out, or
returns schema-invalid output, :meth:`RegressionAdvisorAgent.run` falls back to
:func:`stub_summary` — a deterministic, LLM-free brief built from the same
evidence — and reports ``source == "stub"``. The ranking is never affected, so
a flaky or offline model can never change *which* tests a release re-runs.

All model calls go through the LLM gateway (§31.1); the output is
schema-validated (:class:`RegressionSummary`). The prompt is
``regression-advisor@1`` (§31.6).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError
from qa_copilot_domain import RecommendationSet

from ..config import load_model_settings
from ..gateway import AICallResult, LLMError, LLMGateway
from ..prompts import PromptError, PromptStore, render_prompt

ADVISOR_NAME = "regression-advisor"

#: Where the summary text came from — the live model or the deterministic stub.
SUMMARY_SOURCE_LLM = "llm"
SUMMARY_SOURCE_STUB = "stub"


@dataclass(frozen=True, slots=True)
class AdvisorInput:
    """One computed :class:`~qa_copilot_domain.RecommendationSet` to summarize.

    The ranking is already fixed by the deterministic core (S6.3); the advisor
    only reads it to produce a brief — it never mutates or re-orders it.
    """

    set: RecommendationSet


class RegressionSummary(BaseModel):
    """The advisor's output contract (schema: ``regression-summary/v1``).

    ``summary`` is a short human brief of the top-N recommendation — which
    tests to run first and the dominant risk signal; ``focus`` names the single
    highest-risk test (the rank-1 key unless there is a specific reason).
    """

    summary: str = Field(min_length=1, max_length=1000)
    focus: str | None = Field(default=None, min_length=1)


def parse_summary(text: str) -> RegressionSummary:
    """Parse the model's JSON output into a validated :class:`RegressionSummary`.

    Tolerates a stray markdown fence or leading prose (local models sometimes
    wrap JSON); the first ``{`` … last ``}`` span is parsed. Invalid JSON or a
    schema violation raises ``ValueError`` (the caller falls back to the stub).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"regression advisor output has no JSON object: {text[:200]!r}")
    payload = text[start : end + 1]
    try:
        return RegressionSummary.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(f"regression advisor output failed schema validation: {exc}") from exc


def stub_summary(set: RecommendationSet) -> str:
    """Deterministic, LLM-free brief of a :class:`~qa_copilot_domain.RecommendationSet`.

    This is the safe fallback when the model is unavailable or invalid — and
    also the content a good model should approximate. It is pure: equal inputs
    ⇒ equal output (no wall clock), so it is unit-testable offline.
    """
    recs = set.recommendations
    if not recs:
        return (
            "No impacted tests to re-run for this change — the S6.1 impact set "
            "is empty, so the regression suite is unchanged."
        )
    top = recs[0]
    listed = ", ".join(f"{rec.test_key} (risk {rec.risk_score:.2f})" for rec in recs[:5])
    if len(recs) > 5:
        listed += f", … ({len(recs)} total)"
    rationale = "; ".join(top.rationale) if top.rationale else "no evidence recorded yet"
    return (
        f"Run the top {len(recs)} impacted test(s) first: {listed}. "
        f"Prioritize {top.test_key} ({rationale})."
    )


@dataclass(frozen=True, slots=True)
class RegressionAdvisorResult:
    """Everything the caller needs: the summary + provenance + audit payload.

    ``source`` records which path produced the text ("llm" or "stub") so the
    S6.4 API can label the summary; ``call`` is the gateway audit payload when
    the LLM path ran (``None`` for the stub fallback).
    """

    summary: str
    source: str
    call: AICallResult | None = None
    prompt_ref: str = ADVISOR_NAME


class RegressionAdvisorAgent:
    """The optional S6.3 regression advisor (build bible §19 Phase 6).

    Loads its prompt from the registry (§31.6, ``regression-advisor@1``),
    renders it with the computed recommendation set, calls the model through
    the gateway (§31.1), and returns a schema-valid brief. When the model is
    unavailable or returns invalid output it falls back to
    :func:`stub_summary` — the deterministic ranking is never affected.

    Pure: no DB, no I/O beyond the gateway — the caller persists/records the
    summary and the audit payload.
    """

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = ADVISOR_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, recommendation: AdvisorInput) -> dict[str, str]:
        set_ = recommendation.set
        if set_.recommendations:
            lines = [
                f"{rec.rank}. {rec.test_key} (risk {rec.risk_score:.2f}) — "
                f"{'; '.join(rec.rationale) if rec.rationale else 'no evidence recorded yet'}"
                for rec in set_.recommendations
            ]
            recommendations_text = "\n".join(lines)
        else:
            recommendations_text = "(no impacted tests — the S6.1 impact set is empty)"
        changed_text = ", ".join(set_.changed) if set_.changed else "(none)"
        return {
            "top_n": str(set_.top_n),
            "changed": changed_text,
            "recommendations": recommendations_text,
        }

    async def run(self, recommendation: AdvisorInput) -> RegressionAdvisorResult:
        """Summarize one recommendation set; fall back to the stub on any failure.

        Never raises on model/prompt problems — the summary is optional and the
        ranking (the deterministic core) is authoritative. Only a programming
        error (a non-recommendation-set input) would propagate.
        """
        set_ = recommendation.set
        prompt_ref = self._prompt_name
        try:
            spec = self._store.get(self._prompt_name, self._prompt_version)
            prompt_ref = spec.ref
            body = render_prompt(spec, **self._variables(recommendation))
            # §9 budgets: the prompt's own values win; the AI_* environment
            # defaults (qa_copilot_ai.config) are the fallback.
            settings = load_model_settings()
            messages = [{"role": "user", "content": body}]
            temperature = spec.temperature if spec.temperature is not None else settings.temperature
            max_tokens = (
                spec.output_budget if spec.output_budget is not None else settings.max_output_tokens
            )
            max_input_tokens = (
                spec.input_budget if spec.input_budget is not None else settings.max_input_tokens
            )
            call = await self._gateway.chat(
                messages,
                agent=ADVISOR_NAME,
                temperature=temperature,
                max_tokens=max_tokens,
                max_input_tokens=max_input_tokens,
            )
            summary = parse_summary(call.text)
            return RegressionAdvisorResult(
                summary=summary.summary,
                source=SUMMARY_SOURCE_LLM,
                call=call,
                prompt_ref=prompt_ref,
            )
        except (PromptError, LLMError, ValueError):
            # Prompt not found / render error, LLM error (incl. input-budget),
            # or schema-invalid output → the deterministic stub. Ranking intact.
            return RegressionAdvisorResult(
                summary=stub_summary(set_),
                source=SUMMARY_SOURCE_STUB,
                prompt_ref=prompt_ref,
            )


__all__ = [
    "ADVISOR_NAME",
    "AdvisorInput",
    "RegressionAdvisorAgent",
    "RegressionAdvisorResult",
    "RegressionSummary",
    "SUMMARY_SOURCE_LLM",
    "SUMMARY_SOURCE_STUB",
    "parse_summary",
    "stub_summary",
]
