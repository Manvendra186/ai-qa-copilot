"""S4.2 eval runner — 10 broken-test fixes vs the Fix Agent.

Scores the agent's **fixes** against the fix golden set (build bible §19
S4.2, §31.7: "≥ 5/10 fixes applicable and passing"). The pipeline per
fixture is the full S4 chain:

``raw failure text`` → :func:`qa_copilot_execution.failure.normalize_failure`
(S3.3, deterministic) → :class:`FailureInvestigatorAgent` (S4.1, AI) →
:class:`FixerAgent` (S4.2, AI) → :class:`~qa_copilot_ai.agents.FixProposal`
→ :func:`apply_patch` (**applicable?**) → *verifier* (**passing?**).

The "passing" check is injected (``verifier``), so the same runner serves
both the offline gate (patched file equals the fixture's known-good
``fixed_code``) and the live gate (the patched spec actually runs green
against the demo app — see :mod:`qa_copilot_ai.fixer.live`).

Failure isolation (same contract as the S1.4/S2.3/S4.1 runners): a
schema-invalid output, a non-applicable patch, or an LLM error fails *its*
fixture and the run continues — the report is always produced.

``qa_copilot_execution`` is a **runtime-only dependency** (imported here,
not in ``pyproject.toml``) — the same pattern as the S4.1 investigator
runner: the monorepo venv provides both packages.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from qa_copilot_execution.failure import normalize_failure
from qa_copilot_execution.golden import FixFixture, FixGoldenSet

from ..agents import (
    FIXER_NAME,
    FailureInvestigatorAgent,
    FixerAgent,
    FixerInput,
    InvestigatorInput,
)
from ..gateway import LLMError
from .patch import PatchError, apply_patch

__all__ = ["FixtureFixResult", "FixEvalReport", "FixVerifier", "run_fix_eval"]


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


#: Verifies one applicable patch: fixture + patched file → passing?
FixVerifier = Callable[[FixFixture, str], Awaitable[bool]]


class FixtureFixResult(BaseModel):
    """One fixture's fix outcome (stable JSON contract).

    ``expected_action`` is the fixture's ground truth (``patch`` when a
    test-side fix exists, else ``decline`` — the category guard);
    ``correct_action`` compares it with what the agent took.
    ``applicable`` is ``None`` for declined fixtures (no patch to apply);
    ``passing`` is ``True`` only when the patch applied **and** the
    verifier confirmed the patched test passes. The gate additionally
    requires ``correct_action`` — a patch that "passes" on a
    product/environment fixture is a gamed test, not a fix (§26).
    """

    fixture_id: str
    title: str
    expected_category: str
    diagnosis_category: str | None
    expected_action: str
    action: str | None
    correct_action: bool
    applicable: bool | None
    passing: bool | None
    patch: str | None = None
    rationale: str | None = None
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int | None = Field(default=None, ge=0)


class FixTotals(BaseModel):
    fixtures: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    applicable: int = Field(ge=0)
    declined: int = Field(ge=0)
    correct_action: int = Field(ge=0)
    passing_fraction: float = Field(ge=0.0, le=1.0)


class FixEvalReport(BaseModel):
    """The S4.2 JSON artifact (same shape family as the S1.4/S2.3/S4.1 reports)."""

    schema_version: int = 1
    agent: str = FIXER_NAME
    model: str
    fixer_prompt_ref: str
    golden_name: str
    golden_version: str
    golden_fixtures: int = Field(ge=1)
    targets: dict[str, float]
    totals: FixTotals
    passed: bool
    generated_at: str
    fixtures: list[FixtureFixResult]


async def _fix_one(
    investigator: FailureInvestigatorAgent,
    fixer: FixerAgent,
    verifier: FixVerifier,
    fixture: FixFixture,
    *,
    app_context: str | None = None,
) -> FixtureFixResult:
    normalized = normalize_failure(fixture.failure)
    investigation = await investigator.run(InvestigatorInput(normalized=normalized))
    diagnosis = investigation.diagnosis
    fix_result = await fixer.run(
        FixerInput(
            failure=normalized,
            diagnosis=diagnosis,
            file_path=fixture.file_path,
            test_code=fixture.test_code,
            app_context=app_context,
        )
    )
    proposal = fix_result.proposal
    expected_action = "patch" if fixture.has_fix else "decline"

    applicable: bool | None = None
    passing: bool | None = None
    error: str | None = None
    patched: str | None = None
    if proposal.action == "patch":
        patch_text = proposal.patch
        if patch_text is None:
            # Unreachable — the fix-proposal schema requires a non-empty patch
            # when action == "patch" — but fail loud rather than crash.
            return _failed_fixture(fixture, "action=patch without a patch text")
        try:
            patched = apply_patch(fixture.test_code, patch_text)
        except PatchError as exc:
            applicable = False
            error = f"patch does not apply: {exc}"
        else:
            applicable = True
            passing = await verifier(fixture, patched)
            if not passing:
                error = error or "patched test did not pass the verifier"

    return FixtureFixResult(
        fixture_id=fixture.id,
        title=fixture.title,
        expected_category=fixture.category.value,
        diagnosis_category=diagnosis.category.value,
        expected_action=expected_action,
        action=proposal.action,
        correct_action=proposal.action == expected_action,
        applicable=applicable,
        passing=passing,
        patch=patched if applicable else None,
        rationale=proposal.rationale,
        error=error,
        tokens_in=investigation.call.usage.tokens_in + fix_result.call.usage.tokens_in,
        tokens_out=investigation.call.usage.tokens_out + fix_result.call.usage.tokens_out,
        latency_ms=((investigation.call.latency_ms or 0) + (fix_result.call.latency_ms or 0)),
    )


def _failed_fixture(fixture: FixFixture, error: str) -> FixtureFixResult:
    return FixtureFixResult(
        fixture_id=fixture.id,
        title=fixture.title,
        expected_category=fixture.category.value,
        diagnosis_category=None,
        expected_action="patch" if fixture.has_fix else "decline",
        action=None,
        correct_action=False,
        applicable=None,
        passing=None,
        error=error[:500],
    )


async def run_fix_eval(
    golden: FixGoldenSet,
    *,
    investigator: FailureInvestigatorAgent,
    fixer: FixerAgent,
    model: str,
    fixer_prompt_ref: str,
    verifier: FixVerifier,
    app_context: str | None = None,
) -> FixEvalReport:
    """Run every fixture through S3.3 → S4.1 → S4.2 → apply → verify.

    ``app_context`` (v2) is the optional read-only application context
    passed to every :class:`FixerInput` (see
    :func:`qa_copilot_ai.fixer.app_context.build_app_context`) — it informs
    the agent's test-side fix without widening the §26 scope. ``None``/
    ``""`` keeps v1 behavior (the prompt renders its fallback line).

    A fixture **passes** when its action matches the fixture's ground truth,
    its patch applies, and the patched test passes (the S4.2 gate unit,
    §19 S4.2 — anti-gaming per §26: a "fix" that flips an assertion to match
    broken product/environment behavior does not count). Declined fixtures,
    non-applicable patches and failing patched tests all count as not
    passing. The run **passes**
    when the passing fraction meets ``targets.passing_min`` (default 0.5 —
    ≥ 5/10, §31.7). Failures are isolated: a schema-invalid output, an LLM
    error, or a non-applicable patch marks *its* fixture failed and the run
    continues — the report is always produced.
    """
    results: list[FixtureFixResult] = []
    for fixture in golden.fixtures:
        try:
            results.append(
                await _fix_one(investigator, fixer, verifier, fixture, app_context=app_context)
            )
        except (ValueError, LLMError) as exc:
            # Schema-invalid output / LLM error: fails this fixture only.
            results.append(_failed_fixture(fixture, str(exc)))

    total = len(results)
    # Gate unit (§19 S4.2, anti-gaming per §26): the action must match the
    # fixture's ground truth AND the patch must apply AND the patched test
    # must pass. A "fix" that flips an assertion to match broken product or
    # environment behavior would fool a naive verifier — it does not count.
    passed = sum(1 for result in results if result.passing and result.correct_action)
    passing_fraction = passed / total
    targets = {"passing_min": golden.targets.passing_min}
    return FixEvalReport(
        model=model,
        fixer_prompt_ref=fixer_prompt_ref,
        golden_name=golden.name,
        golden_version=golden.version,
        golden_fixtures=total,
        targets=targets,
        totals=FixTotals(
            fixtures=total,
            passed=passed,
            failed=total - passed,
            applicable=sum(1 for result in results if result.applicable),
            declined=sum(1 for result in results if result.action == "decline"),
            correct_action=sum(1 for result in results if result.correct_action),
            passing_fraction=passing_fraction,
        ),
        passed=passing_fraction >= targets["passing_min"],
        generated_at=_utcnow(),
        fixtures=results,
    )
