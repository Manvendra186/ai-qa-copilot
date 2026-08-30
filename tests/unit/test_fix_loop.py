"""S4.3 Approve → re-run loop (full-loop E2E) — offline unit suite (no LLM, no browser).

Covers:
  - ``resolve_approval`` — the §26 human gate: explicit approve/reject
    (always wins), interactive ``y``/``n`` (Enter and unknown answers ⇒
    reject), fail-safe reject without a TTY, loud failure on an unknown
    explicit decision;
  - ``run_fix_loop`` — every outcome with a fake spec runner and fake
    agents: ``passing`` (no LLM calls), ``fixed`` (approve → re-run
    passes), ``declined`` (the correct S4.2 action — no re-run),
    ``rejected`` (nothing applied, no re-run), ``not_fixed`` (red
    re-run), ``error`` (investigation failure / unapplicable patch /
    executor crash);
  - the S4.2 flag contract — loop run flags are derived from ``app_env``
    true-valued entries only (``{"FLAG": "0"}`` must NOT count as a flag);
  - the report contract — ``LoopReport`` JSON shape, ``closed``, and
    ``exit_code_for`` (``0`` closed · ``1`` not closed · ``2`` error);
  - ``PlaywrightLoopRunner`` — the adapter mapping the S4.2 verifier to
    ``SpecRun`` (duck-typed verifier — no Playwright here).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

import pytest
from qa_copilot_ai.agents import (
    Diagnosis,
    FailureInvestigatorAgentResult,
    FixerAgentResult,
    FixerInput,
    FixProposal,
    InvestigatorInput,
)
from qa_copilot_ai.fixer.live import required_flags
from qa_copilot_ai.fixer.patch import make_patch
from qa_copilot_ai.gateway import AICallResult, TokenUsage
from qa_copilot_ai.loop.approval import APPROVE, REJECT, resolve_approval
from qa_copilot_ai.loop.live import PlaywrightLoopRunner
from qa_copilot_ai.loop.runner import (
    LoopReport,
    LoopTarget,
    SpecRun,
    exit_code_for,
    run_fix_loop,
)
from qa_copilot_domain import FailureCategory

MODEL = "fake-model"

BROKEN_TEST = (
    "import { test, expect } from '@playwright/test';\n"
    "\n"
    "test('shows the count of 50', async ({ page }) => {\n"
    "  await page.goto('/');\n"
    "  expect(await page.locator('#count').textContent()).toBe('50');\n"
    "});\n"
)
FIXED_TEST = BROKEN_TEST.replace("count of 50", "count of 49").replace(
    "toBe('50')", "toBe('49')"
)
PATCH = make_patch(BROKEN_TEST, FIXED_TEST, "e2e/loop_probe.spec.js")

#: A syntactically valid hunk whose old-side block is not in the broken
#: file — the S4.2 "applicable" gate must catch it (PatchError → error).
BAD_PATCH = (
    "@@ -1,3 +1,3 @@\n"
    " import { test, expect } from '@playwright/test';\n"
    "-  await page.goto('/somewhere-that-never-exists');\n"
    "+  await page.goto('/');\n"
)


def _call(tokens_in: int, tokens_out: int) -> AICallResult:
    return AICallResult(
        agent="fake",
        model=MODEL,
        text="{}",
        usage=TokenUsage(tokens_in=tokens_in, tokens_out=tokens_out),
        latency_ms=1,
        redactions=0,
        retries=0,
        input_hash="0" * 64,
    )


def _investigation() -> FailureInvestigatorAgentResult:
    return FailureInvestigatorAgentResult(
        diagnosis=Diagnosis(
            category=FailureCategory.AUTOMATION_DEFECT,
            root_cause="stale expected value",
            confidence=0.9,
            evidence=["expect(await page.locator('#count').textContent()).toBe('50')"],
            suggested_fix="update the expected count to 49",
        ),
        call=_call(11, 3),
        prompt_ref="failure-investigator@1",
    )


def _proposal(
    action: Literal["patch", "decline"] = "patch", patch: str = PATCH
) -> FixerAgentResult:
    return FixerAgentResult(
        proposal=FixProposal(
            action=action,
            target_file=None if action == "decline" else "e2e/items.spec.js",
            patch=None if action == "decline" else patch,
            rationale=(
                "stale expectation"
                if action == "patch"
                else "product defect — no safe test-side fix"
            ),
        ),
        call=_call(22, 5),
        prompt_ref="fix-agent@2",
    )


class FakeInvestigator:
    """S4.1 stand-in (duck-typed against ``FailureInvestigatorAgent``)."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.inputs: list[Any] = []

    async def run(self, investigation: InvestigatorInput) -> FailureInvestigatorAgentResult:
        self.inputs.append(investigation)
        if self._error is not None:
            raise self._error
        return _investigation()


class FakeFixer:
    """S4.2 stand-in (duck-typed against ``FixerAgent``)."""

    def __init__(
        self, proposal: FixerAgentResult | None = None, error: Exception | None = None
    ) -> None:
        self._proposal = proposal
        self._error = error
        self.inputs: list[Any] = []

    async def run(self, fix_input: FixerInput) -> FixerAgentResult:
        self.inputs.append(fix_input)
        if self._error is not None:
            raise self._error
        return self._proposal or _proposal()


class FakeSpecRunner:
    """S3 executor stand-in — scripted ``SpecRun`` outcomes (or a crash).

    ``fail_after`` raises on the call after *N* successful ones
    (``fail_after=0`` ⇒ the very first call crashes).
    """

    def __init__(self, outcomes: list[Any], fail_after: int | None = None) -> None:
        self._outcomes = list(outcomes)
        self._fail_after = fail_after
        self.calls: list[dict[str, object]] = []

    async def run(self, spec_text: str, *, spec_name: str, flags: frozenset[str]) -> SpecRun:
        self.calls.append(
            {"spec_text": spec_text, "spec_name": spec_name, "flags": frozenset(flags)}
        )
        if self._fail_after is not None and len(self.calls) > self._fail_after:
            raise RuntimeError("playwright exploded")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, SpecRun):
            return outcome
        return SpecRun(
            ok=outcome,
            detail=None if outcome else "Error: expect(received).toBe('50') — received '49'",
        )


TARGET = LoopTarget(
    fixture_id="FIX-001",
    title="count shows 49",
    file_path="e2e/items.spec.js",
    test_code=BROKEN_TEST,
)


def _loop(
    runner: FakeSpecRunner,
    *,
    target: LoopTarget = TARGET,
    investigator: FakeInvestigator | None = None,
    fixer: FakeFixer | None = None,
    **kwargs: Any,
) -> LoopReport:
    return asyncio.run(
        run_fix_loop(
            target,
            investigator=investigator or FakeInvestigator(),
            fixer=fixer or FakeFixer(),
            spec_runner=runner,
            **kwargs,
        )
    )


# --- the §26 approval gate ------------------------------------------------------


def test_explicit_approve_wins_even_on_tty() -> None:
    decision = resolve_approval(
        APPROVE, is_tty=True, patch="p", prompt=lambda _p: pytest.fail("must not prompt")
    )
    assert (decision.approved, decision.decided_by) == (True, "explicit:approve")


def test_explicit_reject_wins_even_on_tty() -> None:
    decision = resolve_approval(
        REJECT, is_tty=True, patch="p", prompt=lambda _p: pytest.fail("must not prompt")
    )
    assert (decision.approved, decision.decided_by) == (False, "explicit:reject")


def test_interactive_yes_approves() -> None:
    decision = resolve_approval(None, is_tty=True, patch="p", prompt=lambda _p: "  y ")
    assert (decision.approved, decision.decided_by) == (True, "interactive:yes")


def test_interactive_yes_is_case_insensitive() -> None:
    decision = resolve_approval(None, is_tty=True, patch="p", prompt=lambda _p: "YES")
    assert decision.approved is True
    assert decision.decided_by == "interactive:yes"


def test_interactive_no_rejects() -> None:
    decision = resolve_approval(None, is_tty=True, patch="p", prompt=lambda _p: "n")
    assert (decision.approved, decision.decided_by) == (False, "interactive:no")


def test_interactive_empty_answer_rejects() -> None:
    """Enter (empty answer) is a rejection — the gate is opt-in."""
    decision = resolve_approval(None, is_tty=True, patch="p", prompt=lambda _p: "")
    assert (decision.approved, decision.decided_by) == (False, "interactive:no")


def test_interactive_unknown_answer_rejects() -> None:
    decision = resolve_approval(None, is_tty=True, patch="p", prompt=lambda _p: "maybe")
    assert (decision.approved, decision.decided_by) == (False, "interactive:other")


def test_non_tty_without_decision_fails_safe_to_reject() -> None:
    """Piped stdin (CI) with no explicit flag ⇒ never auto-applied (§26)."""
    decision = resolve_approval(
        None, is_tty=False, patch="p", prompt=lambda _p: pytest.fail("must not prompt")
    )
    assert (decision.approved, decision.decided_by) == (False, "auto:reject-no-tty")


def test_unknown_explicit_decision_raises() -> None:
    """A typo must fail loud — it may not silently pick a side."""
    with pytest.raises(ValueError, match="unknown approval decision"):
        resolve_approval("yes", is_tty=True, patch="p")


# --- run_fix_loop — the six outcomes -------------------------------------------


def test_passing_when_initial_run_is_green() -> None:
    runner = FakeSpecRunner([True])
    investigator, fixer = FakeInvestigator(), FakeFixer()
    report = _loop(runner, investigator=investigator, fixer=fixer)
    assert report.outcome == "passing"
    assert report.closed
    assert report.initial_run_ok is True
    assert len(runner.calls) == 1
    # Nothing to fix — the loop must not spend a single LLM call.
    assert not investigator.inputs
    assert not fixer.inputs
    assert report.category is None
    assert report.patch is None
    assert report.approval is None
    assert report.re_run_ok is None


def test_fixed_when_patch_approved_and_re_run_is_green() -> None:
    runner = FakeSpecRunner([False, True])
    investigator, fixer = FakeInvestigator(), FakeFixer()
    report = _loop(
        runner, investigator=investigator, fixer=fixer, decision=APPROVE, model=MODEL
    )
    assert report.outcome == "fixed"
    assert report.closed
    assert report.initial_run_ok is False
    # S4.1 diagnosis flowed into the report.
    assert report.category == "automation_defect"
    assert report.root_cause == "stale expected value"
    assert report.confidence == 0.9
    # S4.2 proposal flowed into the report.
    assert report.action == "patch"
    assert report.patch == PATCH
    # S4.3 gate + re-run.
    assert report.approval is not None and report.approval.approved is True
    assert report.approval.decided_by == "explicit:approve"
    assert report.re_run_ok is True
    # Audit trail: both prompts + summed token usage (§31).
    assert report.prompt_refs == ("failure-investigator@1", "fix-agent@2")
    assert (report.tokens_in, report.tokens_out) == (33, 8)
    assert report.error is None
    # The agents saw the S3.3 normalized failure, and the re-run executed
    # the patched spec (not the broken one).
    assert investigator.inputs[0].normalized is not None
    assert fixer.inputs[0].failure is not None
    assert fixer.inputs[0].diagnosis.category is FailureCategory.AUTOMATION_DEFECT
    assert len(runner.calls) == 2
    assert runner.calls[0]["spec_text"] == BROKEN_TEST
    assert runner.calls[1]["spec_text"] == FIXED_TEST
    assert runner.calls[0]["spec_name"] == runner.calls[1]["spec_name"] == "e2e/loop_probe.spec.js"


def test_declined_closes_the_loop_without_re_run() -> None:
    """A ``decline`` is the correct S4.2 action — the loop closes on it."""
    runner = FakeSpecRunner([False])
    report = _loop(runner, fixer=FakeFixer(proposal=_proposal(action="decline")))
    assert report.outcome == "declined"
    assert report.closed
    assert report.action == "decline"
    assert report.rationale is not None
    assert report.patch is None
    assert report.approval is None  # a decline is never put to the human
    assert report.re_run_ok is None
    assert len(runner.calls) == 1  # no re-run


def test_rejected_applies_nothing_and_does_not_re_run() -> None:
    runner = FakeSpecRunner([False])
    report = _loop(runner, decision=REJECT)
    assert report.outcome == "rejected"
    assert not report.closed
    assert report.patch == PATCH
    assert report.approval is not None and report.approval.decided_by == "explicit:reject"
    assert report.re_run_ok is None
    assert len(runner.calls) == 1
    # The broken spec went out, the patched spec never did.
    assert runner.calls[0]["spec_text"] == BROKEN_TEST


def test_not_fixed_when_re_run_is_red() -> None:
    """A red re-run is reported, never retried silently, never faked."""
    runner = FakeSpecRunner([False, False])
    report = _loop(runner, decision=APPROVE)
    assert report.outcome == "not_fixed"
    assert not report.closed
    assert report.approval is not None and report.approval.approved is True
    assert report.re_run_ok is False
    assert report.re_run_detail  # the raw failure text is kept for audit
    assert len(runner.calls) == 2  # exactly one re-run — no silent retry


def test_error_when_investigation_fails() -> None:
    runner = FakeSpecRunner([False])
    fixer = FakeFixer()
    report = _loop(
        runner,
        investigator=FakeInvestigator(error=ValueError("bad diagnosis JSON")),
        fixer=fixer,
    )
    assert report.outcome == "error"
    assert not report.closed
    assert "investigation failed" in (report.error or "")
    assert len(runner.calls) == 1
    assert not fixer.inputs  # the loop stopped before S4.2
    assert report.approval is None


def test_error_when_fix_proposal_fails() -> None:
    runner = FakeSpecRunner([False])
    report = _loop(runner, fixer=FakeFixer(error=ValueError("bad proposal JSON")))
    assert report.outcome == "error"
    assert not report.closed
    assert "fix proposal failed" in (report.error or "")
    assert report.prompt_refs == ("failure-investigator@1",)
    assert report.approval is None


def test_error_when_patch_does_not_apply() -> None:
    """The S4.2 "applicable" gate precedes the human gate — nothing is put
    to the operator that cannot be applied."""
    runner = FakeSpecRunner([False])
    report = _loop(
        runner, fixer=FakeFixer(proposal=_proposal(patch=BAD_PATCH)), decision=APPROVE
    )
    assert report.outcome == "error"
    assert not report.closed
    assert "patch does not apply" in (report.error or "")
    assert report.action == "patch"
    assert report.patch == BAD_PATCH
    assert report.approval is None  # the gate was never reached
    assert len(runner.calls) == 1  # no re-run


def test_error_when_initial_run_executor_crashes() -> None:
    runner = FakeSpecRunner([], fail_after=0)
    report = _loop(runner)
    assert report.outcome == "error"
    assert not report.closed
    assert "initial run failed" in (report.error or "")
    assert report.initial_run_ok is False


def test_error_when_re_run_executor_crashes() -> None:
    runner = FakeSpecRunner([False], fail_after=1)
    report = _loop(runner, decision=APPROVE)
    assert report.outcome == "error"
    assert not report.closed
    assert "re-run failed" in (report.error or "")
    assert report.approval is not None and report.approval.approved is True


# --- S4.2 flag contract ---------------------------------------------------------


def test_loop_flags_follow_s42_truthy_semantics() -> None:
    """``{"FLAG": "0"}`` means the flag is OFF — the S4.3 loop derives run
    flags the same way the S4.2 verifier does (shared helper)."""
    target = LoopTarget(
        fixture_id="FIX-010",
        title="defect-flagged",
        file_path="e2e/x.spec.js",
        test_code=BROKEN_TEST,
        app_env={"DEFECT_B": "1", "DEFECT_OFF": "0"},
    )
    runner = FakeSpecRunner([True])
    _loop(runner, target=target)
    assert runner.calls[0]["flags"] == frozenset({"DEFECT_B"})


def test_required_flags_helper() -> None:
    assert required_flags({"A": "1", "B": "0", "C": "true", "D": "off"}) == frozenset({"A", "C"})
    assert required_flags({}) == frozenset()
    assert required_flags({"A": " yes "}) == frozenset({"A"})  # case/whitespace tolerant


# --- report contract ------------------------------------------------------------


def test_exit_codes() -> None:
    assert exit_code_for("passing") == 0
    assert exit_code_for("fixed") == 0
    assert exit_code_for("declined") == 0
    assert exit_code_for("rejected") == 1
    assert exit_code_for("not_fixed") == 1
    assert exit_code_for("error") == 2


def test_report_json_contract() -> None:
    runner = FakeSpecRunner([False, True])
    report = _loop(runner, decision=APPROVE, model=MODEL)
    payload = json.loads(report.to_json())
    assert payload["schema"] == "fix-loop-report/v1"
    assert payload["fixture_id"] == "FIX-001"
    assert payload["title"] == "count shows 49"
    assert payload["target_file"] == "e2e/items.spec.js"
    assert payload["outcome"] == "fixed"
    assert payload["model"] == MODEL
    assert isinstance(payload["duration_ms"], int)
    assert payload["approval"] == {"approved": True, "decided_by": "explicit:approve"}
    assert payload["re_run_ok"] is True
    # A rejected report must still serialize with its None fields.
    rejected = _loop(FakeSpecRunner([False]), decision=REJECT)
    rejected_payload = json.loads(rejected.to_json())
    assert rejected_payload["approval"] == {"approved": False, "decided_by": "explicit:reject"}
    assert rejected_payload["re_run_ok"] is None


# --- PlaywrightLoopRunner adapter (duck-typed verifier — no Playwright) ----------


class _FakeVerifier:
    def __init__(self, outcome: tuple[bool, str]) -> None:
        self._outcome = outcome
        self.runs: list[tuple[str, str, frozenset[str]]] = []
        self.closed = False

    async def run_spec(
        self, spec_text: str, *, spec_name: str, flags: frozenset[str]
    ) -> tuple[bool, str]:
        self.runs.append((spec_text, spec_name, flags))
        return self._outcome

    async def aclose(self) -> None:
        self.closed = True


def test_playwright_loop_runner_maps_verifier_to_spec_run() -> None:
    verifier = _FakeVerifier((True, "1 passed (49 ms)"))
    runner = PlaywrightLoopRunner(verifier)
    result = asyncio.run(
        runner.run("spec", spec_name="e2e/loop_probe.spec.js", flags=frozenset({"X"}))
    )
    assert result == SpecRun(ok=True, detail="1 passed (49 ms)")
    assert verifier.runs == [("spec", "e2e/loop_probe.spec.js", frozenset({"X"}))]


def test_playwright_loop_runner_empty_detail_becomes_none() -> None:
    verifier = _FakeVerifier((False, ""))
    result = asyncio.run(
        PlaywrightLoopRunner(verifier).run("spec", spec_name="s", flags=frozenset())
    )
    assert result == SpecRun(ok=False, detail=None)


def test_playwright_loop_runner_delegates_aclose() -> None:
    verifier = _FakeVerifier((True, "ok"))
    runner = PlaywrightLoopRunner(verifier)
    asyncio.run(runner.aclose())
    assert verifier.closed is True
