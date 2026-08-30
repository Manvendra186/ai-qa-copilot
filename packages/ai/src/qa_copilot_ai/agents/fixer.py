"""Fix Agent (S4.2, build bible §19 Phase 4).

S4.1 diagnosed the failure (:class:`Diagnosis` — category + root cause +
evidence + ``suggested_fix``); S4.2 turns it into a **reviewable
patch/diff** (build bible: "Produce a reviewable patch/diff, not a silent
change"). Design (Option B — evidence-driven planning): the agent
re-derives the patch from the *normalized failure*, the *diagnosis*, the
*actual broken test file*, and (v2) a **read-only context of the
application under test** (test-ids, routes, DOM, API shapes, seed data —
assembled by :func:`qa_copilot_ai.fixer.app_context.build_app_context`);
the diagnosis's ``suggested_fix`` is a **strong prior** the model starts
from — but when the prior conflicts with the code or the evidence, the
code and evidence win. A vague or wrong ``suggested_fix`` can never
silently own the patch.

Category guard (§26 never auto-heals; the gate must not be gamed by
"fixing" tests to hide app bugs): only test-side defects
(automation/test-data/flaky) produce a patch. ``product_defect`` /
``environment_defect`` / ``unknown`` → action ``decline`` with a rationale
of what to reproduce/verify instead. The guard is enforced in the prompt
and the output schema (:class:`FixProposal`); the runner records whether
the taken action matched the fixture's known ground truth.

All model calls go through the LLM gateway (§31.1); the output is
schema-validated — invalid output fails loud (``ValueError``), the job
never half-succeeds (§31.7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator
from qa_copilot_domain import NormalizedFailure

from ..config import load_model_settings
from ..gateway import AICallResult, LLMGateway
from ..prompts import PromptStore, render_prompt
from .failure_investigator import Diagnosis

FIXER_NAME = "fix-agent"

#: The two actions a fix proposal can take (the category guard, §26).
FixAction = Literal["patch", "decline"]

#: Rendered for ``FixerInput.app_context`` when no application context is
#: available (offline tests, or the v1 prompt without the app-context block).
NO_APP_CONTEXT = "Not available for this run — work from the evidence and test code only."


@dataclass(frozen=True, slots=True)
class FixerInput:
    """One diagnosed failure plus the broken test file to fix.

    ``failure`` is the S3.3 normalized failure (evidence, signals, HTTP
    status, selector, endpoint); ``diagnosis`` is the S4.1 output
    (category, root cause, confidence, evidence, suggested-fix prior);
    ``file_path``/``test_code`` are the actual broken test file — the
    ground truth the patch must be derived from (Option B).

    ``app_context`` (v2) is the optional **read-only application context**
    (test-ids, routes, DOM, API shapes, seed data — see
    :func:`qa_copilot_ai.fixer.app_context.build_app_context`); it informs
    the fix but never widens the §26 scope — the patch still touches only
    the target test file. ``None``/``""`` renders the "not available"
    fallback line (v1 prompts keep working).
    """

    failure: NormalizedFailure
    diagnosis: Diagnosis
    file_path: str
    test_code: str
    app_context: str | None = None


class FixProposal(BaseModel):
    """The S4.2 output contract (schema: ``fix-proposal/v1``).

    ``action`` is the category-guard decision: ``patch`` (a safe test-side
    fix exists) or ``decline`` (product/environment defect or too thin
    evidence — no safe test-side change; the rationale says what the human
    should reproduce/verify instead). ``patch`` is a unified diff against
    ``target_file`` (git style, 3 context lines). ``needs_human_approval``
    is always ``True`` — v1 never auto-heals (§26); a patch is reviewed,
    never silently applied.
    """

    action: FixAction
    target_file: str | None = None
    patch: str | None = None
    rationale: str = Field(min_length=1)
    needs_human_approval: bool = True

    @model_validator(mode="after")
    def _check_action_consistency(self) -> FixProposal:
        if self.action == "patch":
            if not (self.patch or "").strip():
                raise ValueError("action=patch requires a non-empty unified-diff patch")
            if not (self.target_file or "").strip():
                raise ValueError("action=patch requires a non-empty target_file")
        elif self.patch is not None:
            raise ValueError("action=decline must not carry a patch")
        return self


def parse_fix_proposal(text: str) -> FixProposal:
    """Parse the model's JSON output into a validated :class:`FixProposal`.

    Tolerates a stray markdown fence or leading prose (local models
    sometimes wrap JSON); the first ``{`` … last ``}`` span is parsed.
    Invalid JSON or a schema violation raises ``ValueError`` (fail loud).
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"fix proposal output has no JSON object: {text[:200]!r}")
    payload = text[start : end + 1]
    try:
        return FixProposal.model_validate_json(payload)
    except ValidationError as exc:
        raise ValueError(f"fix proposal output failed schema validation: {exc}") from exc


@dataclass(frozen=True, slots=True)
class FixerAgentResult:
    """Everything the caller needs: the validated proposal + the audit payload."""

    proposal: FixProposal
    call: AICallResult
    prompt_ref: str


class FixerAgent:
    """The Fix Agent (S4.2, build bible §19 Phase 4).

    Loads its prompt from the registry (§31.6, ``fix-agent@latest``),
    renders it with the diagnosis (strong prior) + normalized failure +
    the broken test file + the optional read-only app context, calls the
    model through the gateway (§31.1), and returns a schema-valid
    :class:`FixProposal`.

    Pure: no DB, no I/O beyond the gateway — the caller records the
    proposal and (S4.3) routes it to human review.
    """

    def __init__(
        self,
        store: PromptStore,
        gateway: LLMGateway,
        *,
        prompt_name: str = FIXER_NAME,
        prompt_version: int | None = None,
    ) -> None:
        self._store = store
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._prompt_version = prompt_version

    def _variables(self, fix_input: FixerInput) -> dict[str, str]:
        diagnosis = fix_input.diagnosis
        failure = fix_input.failure
        if diagnosis.evidence:
            evidence_text = "\n".join(f"- {line}" for line in diagnosis.evidence)
        else:
            evidence_text = "(none cited)"
        if failure.category_signals:
            signals = ", ".join(failure.category_signals)
        else:
            signals = "(none detected)"
        return {
            "category": diagnosis.category.value,
            "root_cause": diagnosis.root_cause,
            "suggested_fix": diagnosis.suggested_fix,
            "confidence": f"{diagnosis.confidence:.2f}",
            "evidence": evidence_text,
            "signals": signals,
            "http_status": str(failure.http_status) if failure.http_status else "n/a",
            "selector": failure.selector or "n/a",
            "endpoint": failure.endpoint or "n/a",
            "file_path": fix_input.file_path,
            "test_code": fix_input.test_code,
            "app_context": fix_input.app_context or NO_APP_CONTEXT,
        }

    async def run(self, fix_input: FixerInput) -> FixerAgentResult:
        """Propose a fix (or decline) for one diagnosed failure.

        Raises ``ValueError`` when the model output is not schema-valid
        JSON, or ``PromptNotFound`` when the registry has no such prompt.
        """
        spec = self._store.get(self._prompt_name, self._prompt_version)
        body = render_prompt(spec, **self._variables(fix_input))
        messages = [{"role": "user", "content": body}]
        # §9 budgets: the prompt's own values win; the AI_* environment
        # defaults (qa_copilot_ai.config) are the fallback.
        settings = load_model_settings()
        result = await self._gateway.chat(
            messages,
            agent=FIXER_NAME,
            temperature=spec.temperature if spec.temperature is not None else settings.temperature,
            max_tokens=(
                spec.output_budget if spec.output_budget is not None else settings.max_output_tokens
            ),
            max_input_tokens=(
                spec.input_budget if spec.input_budget is not None else settings.max_input_tokens
            ),
        )
        proposal = parse_fix_proposal(result.text)
        return FixerAgentResult(proposal=proposal, call=result, prompt_ref=spec.ref)
