"""S4.3 Approve → re-run loop (build bible §19 Phase 4, §20 MVP).

Closes the full loop **S3 → S4 → re-run** on one failing test:

1. **run** (S3) — execute the broken spec; a passing run closes the loop
   immediately (nothing to fix, no LLM call);
2. **normalize** (S3.3) — ``normalize_failure`` over the raw failure text;
3. **investigate** (S4.1) — Failure Investigator diagnosis (§12 contract);
4. **propose** (S4.2) — Fix Agent fix proposal (§22: patch | decline); a
   decline closes the loop (the correct action, §26);
5. **gate** — the S4.2 "applicable" contract (the patch must apply to the
   broken file) and the §26 human gate (:mod:`~qa_copilot_ai.loop.approval`
   — no auto-heal);
6. **re-run** (S3) — execute the patched spec: a pass closes the loop
   (``fixed``); a red re-run is reported (``not_fixed``) — never retried
   silently, never faked (§31.7).

DB-free, like the S4.1/S4.2 runners: the loop returns a
:class:`LoopReport` (stable JSON contract) and the caller — the CLI, a
job, the API — persists and serves it. The spec executor is injected
(:class:`LoopSpecRunner`), so the loop is fully offline-testable; the live
CLI wires in the Playwright-backed runner (``loop.live``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from qa_copilot_execution.failure import normalize_failure

from ..agents import (
    FailureInvestigatorAgentResult,
    FixerAgentResult,
    FixerInput,
    InvestigatorInput,
)
from ..fixer.live import required_flags
from ..fixer.patch import PatchError, apply_patch
from ..gateway import LLMError
from .approval import ApprovalDecision, default_prompt, resolve_approval

__all__ = [
    "CLOSED_OUTCOMES",
    "LoopFixer",
    "LoopInvestigator",
    "LoopReport",
    "LoopSpecRunner",
    "LoopTarget",
    "SpecRun",
    "exit_code_for",
    "run_fix_loop",
]

#: Outcomes that close the loop (S3 → S4 → re-run finished its job).
CLOSED_OUTCOMES = frozenset({"passing", "fixed", "declined"})

_SCHEMA = "fix-loop-report/v1"


@dataclass(frozen=True, slots=True)
class SpecRun:
    """One spec execution (S3) — the loop's only notion of pass/fail.

    ``detail`` carries the raw failure text on a red run (it feeds the
    S3.3 normalizer), or a short reason on an executor error.
    """

    ok: bool
    detail: str | None = None


class LoopSpecRunner(Protocol):
    """Executes one spec in the target app (S3).

    Called twice per loop — the initial run of the broken spec and the
    re-run of the patched spec (same ``spec_name``/``flags``). Implementations
    must return ``SpecRun`` (never raise) for expected failures.
    """

    async def run(
        self,
        spec_text: str,
        *,
        spec_name: str,
        flags: frozenset[str],
    ) -> SpecRun: ...


class LoopInvestigator(Protocol):
    """The S4.1 step — anything that can run the investigation.

    The real :class:`~qa_copilot_ai.agents.FailureInvestigatorAgent`
    satisfies this; unit tests inject a fake (no LLM, no network).
    """

    async def run(self, investigation: InvestigatorInput) -> FailureInvestigatorAgentResult: ...


class LoopFixer(Protocol):
    """The S4.2 step — anything that can run the fix.

    The real :class:`~qa_copilot_ai.agents.FixerAgent` satisfies this;
    unit tests inject a fake (no LLM, no network).
    """

    async def run(self, fix_input: FixerInput) -> FixerAgentResult: ...


@dataclass(frozen=True, slots=True)
class LoopTarget:
    """The one failing test the loop works on.

    ``file_path`` is the file the Fix Agent targets (the golden fixture's
    path — what the operator sees); ``spec_name`` is where the spec
    executor runs it (a probe file, so the live run never clobbers a real
    test of the app under test).
    """

    fixture_id: str
    title: str
    file_path: str
    test_code: str
    app_env: Mapping[str, str] = field(default_factory=dict)  # defect flags
    spec_name: str = "e2e/loop_probe.spec.js"


@dataclass(frozen=True, slots=True)
class LoopReport:
    """The S4.3 loop report — the stable JSON contract (§19 S4.3, §29).

    Fields fill in as the loop progresses: a report that stops at
    ``rejected`` has a patch and an approval but no ``re_run_*``; one that
    stops at ``declined`` has a diagnosis and a proposal but no approval.
    """

    schema: str
    fixture_id: str
    title: str
    target_file: str
    outcome: str  # passing | fixed | declined | rejected | not_fixed | error
    model: str
    duration_ms: int
    initial_run_ok: bool
    initial_run_detail: str | None = None
    # S4.1 diagnosis (None until reached)
    category: str | None = None
    root_cause: str | None = None
    confidence: float | None = None
    suggested_fix: str | None = None
    # S4.2 proposal (None until reached)
    action: str | None = None  # patch | decline
    rationale: str | None = None
    patch: str | None = None
    # S4.3 approval gate (None when the loop stopped before it)
    approval: ApprovalDecision | None = None
    # re-run (None when the loop stopped before it)
    re_run_ok: bool | None = None
    re_run_detail: str | None = None
    # audit
    prompt_refs: tuple[str, ...] = ()
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None

    @property
    def closed(self) -> bool:
        """The loop finished its job: nothing to fix, fixed, or correctly declined."""
        return self.outcome in CLOSED_OUTCOMES

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form (nested dataclasses flattened)."""
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def exit_code_for(outcome: str) -> int:
    """CLI exit code: ``0`` closed · ``1`` ran but not closed · ``2`` error."""
    if outcome in CLOSED_OUTCOMES:
        return 0
    if outcome in {"rejected", "not_fixed"}:
        return 1
    return 2


async def run_fix_loop(
    target: LoopTarget,
    *,
    investigator: LoopInvestigator,
    fixer: LoopFixer,
    spec_runner: LoopSpecRunner,
    decision: str | None = None,
    is_tty: bool = False,
    app_context: str | None = None,
    prompt: Callable[[str], str] = default_prompt,
    model: str = "",
) -> LoopReport:
    """Run one failing test through S3 → S4 → approval → re-run.

    Expected loop outcomes never raise: LLM failures, an unapplicable
    patch, and a red re-run all surface as report outcomes
    (``error`` / ``not_fixed``) — the loop never half-succeeds (§31.7).
    Only operator/usage errors (e.g. a bad *decision* value) raise.
    """
    started = time.monotonic()
    # S4.2 flag semantics (single source of truth in ``fixer.live``): only
    # true-valued ``app_env`` entries are active defect switches.
    flags = required_flags(target.app_env)

    def finish(outcome: str, **phase: Any) -> LoopReport:
        report: dict[str, Any] = {
            "schema": _SCHEMA,
            "fixture_id": target.fixture_id,
            "title": target.title,
            "target_file": target.file_path,
            "outcome": outcome,
            "model": model,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
        report.update(phase)
        return LoopReport(**report)

    # 1 — S3: run the broken spec (the loop's ground truth).
    try:
        initial = await spec_runner.run(target.test_code, spec_name=target.spec_name, flags=flags)
    except Exception as exc:  # executor itself failed (stack, CLI, …)
        return finish("error", initial_run_ok=False, error=f"initial run failed: {exc}")
    if initial.ok:
        return finish("passing", initial_run_ok=True)

    # 2 — S3.3: normalize the raw failure text (pure, cannot fail here).
    normalized = normalize_failure(initial.detail or "(no failure detail captured)")
    phase: dict[str, Any] = {
        "initial_run_ok": False,
        "initial_run_detail": initial.detail,
    }

    # 3 — S4.1: investigate (diagnosis, §12 contract).
    try:
        investigation = await investigator.run(InvestigatorInput(normalized=normalized))
    except (ValueError, LLMError) as exc:
        return finish("error", **phase, error=f"investigation failed: {exc}")

    # 4 — S4.2: propose (fix-proposal/v1 contract: patch | decline).
    try:
        proposal_result = await fixer.run(
            FixerInput(
                failure=normalized,
                diagnosis=investigation.diagnosis,
                file_path=target.file_path,
                test_code=target.test_code,
                app_context=app_context,
            )
        )
    except (ValueError, LLMError) as exc:
        return finish(
            "error",
            **phase,
            prompt_refs=(investigation.prompt_ref,),
            error=f"fix proposal failed: {exc}",
        )
    diagnosis = investigation.diagnosis
    proposal = proposal_result.proposal
    phase.update(
        category=diagnosis.category.value,
        root_cause=diagnosis.root_cause,
        confidence=diagnosis.confidence,
        suggested_fix=diagnosis.suggested_fix,
        prompt_refs=(investigation.prompt_ref, proposal_result.prompt_ref),
        tokens_in=investigation.call.usage.tokens_in + proposal_result.call.usage.tokens_in,
        tokens_out=(investigation.call.usage.tokens_out + proposal_result.call.usage.tokens_out),
    )

    if proposal.action == "decline":
        # S4.2 §26: the correct action — the loop closes without a re-run.
        return finish("declined", **phase, action="decline", rationale=proposal.rationale)

    patch = proposal.patch
    if patch is None:
        # The schema forbids action=patch without a patch (FixProposal
        # validator) — but never let a malformed proposal reach the gate.
        return finish(
            "error",
            **phase,
            action="patch",
            rationale=proposal.rationale,
            error="patch proposal has no patch (schema violation)",
        )

    # 5a — S4.2 "applicable" contract: the patch must apply to the broken file.
    try:
        patched = apply_patch(target.test_code, patch)
    except PatchError as exc:
        return finish(
            "error",
            **phase,
            action="patch",
            rationale=proposal.rationale,
            patch=patch,
            error=f"patch does not apply: {exc}",
        )

    # 5b — §26 human gate: no auto-heal. Reject ⇒ stop; nothing is applied.
    approval = resolve_approval(decision, is_tty=is_tty, patch=patch, prompt=prompt)
    phase.update(action="patch", rationale=proposal.rationale, patch=patch)
    if not approval.approved:
        return finish("rejected", **phase, approval=approval)

    # 6 — S3: re-run the patched spec (the loop's exit condition).
    try:
        re_run = await spec_runner.run(patched, spec_name=target.spec_name, flags=flags)
    except Exception as exc:  # executor itself failed (stack, CLI, …)
        return finish("error", **phase, approval=approval, error=f"re-run failed: {exc}")
    if re_run.ok:
        return finish("fixed", **phase, approval=approval, re_run_ok=True)
    return finish(
        "not_fixed",
        **phase,
        approval=approval,
        re_run_ok=False,
        re_run_detail=re_run.detail,
    )
