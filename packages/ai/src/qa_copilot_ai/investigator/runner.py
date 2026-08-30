"""S4.1 eval runner — the 30-broken-test set vs the Failure Investigator.

Scores the agent's **top-1 category** against each fixture's expected §16
category (build bible §19 S4.1, §22, §31.7). The pipeline per fixture is:

``raw failure text`` → :func:`qa_copilot_execution.failure.normalize_failure`
(S3.3, deterministic) → :class:`FailureInvestigatorAgent` (S4.1, AI) →
:class:`~qa_copilot_ai.agents.Diagnosis`.

Failure isolation (same contract as the S1.4/S2.3 runners): a schema-invalid
output or an LLM error fails *its* fixture and the run continues — the
report is always produced.

``qa_copilot_execution`` is a **runtime-only dependency** (imported here,
not in ``pyproject.toml``) — the same pattern as the S2.3 automation runner
with ``qa_copilot_repository``: the monorepo venv provides both packages.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field
from qa_copilot_execution.failure import normalize_failure
from qa_copilot_execution.golden import FailureFixture, FailureGoldenSet

from ..agents import INVESTIGATOR_NAME, FailureInvestigatorAgent, InvestigatorInput
from ..gateway import LLMError


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class FixtureInvestigationResult(BaseModel):
    """One fixture's investigation outcome (stable JSON contract).

    ``suggested`` is the S3.3 normalizer's best guess; ``category`` is the
    agent's top-1; ``correct`` is the S4.1 top-1 hit; ``schema_valid`` is
    False when the model output could not be parsed into a Diagnosis (or the
    call failed).
    """

    fixture_id: str
    title: str
    suggested: str
    category: str | None
    root_cause: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    correct: bool
    schema_valid: bool
    passed: bool
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int | None = Field(default=None, ge=0)


class InvestigationTotals(BaseModel):
    fixtures: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    top1_fraction: float = Field(ge=0.0, le=1.0)
    schema_valid_fraction: float = Field(ge=0.0, le=1.0)


class InvestigationReport(BaseModel):
    """The S4.1 JSON artifact (same shape family as the S1.4/S2.3 reports)."""

    schema_version: int = 1
    agent: str = INVESTIGATOR_NAME
    model: str
    prompt_ref: str
    golden_name: str
    golden_version: str
    golden_fixtures: int = Field(ge=1)
    targets: dict[str, float]
    totals: InvestigationTotals
    passed: bool
    generated_at: str
    fixtures: list[FixtureInvestigationResult]


async def _investigate(
    agent: FailureInvestigatorAgent,
    fixture: FailureFixture,
) -> FixtureInvestigationResult:
    normalized = normalize_failure(fixture.raw)
    result = await agent.run(InvestigatorInput(normalized=normalized))
    diagnosis = result.diagnosis
    call = result.call
    return FixtureInvestigationResult(
        fixture_id=fixture.id,
        title=fixture.title,
        suggested=normalized.category.value,
        category=diagnosis.category.value,
        root_cause=diagnosis.root_cause,
        confidence=diagnosis.confidence,
        correct=diagnosis.category == fixture.expect.category,
        schema_valid=True,
        passed=diagnosis.category == fixture.expect.category,
        tokens_in=call.usage.tokens_in,
        tokens_out=call.usage.tokens_out,
        latency_ms=call.latency_ms,
    )


async def run_investigation_eval(
    golden: FailureGoldenSet,
    *,
    agent: FailureInvestigatorAgent,
    model: str,
    prompt_ref: str,
) -> InvestigationReport:
    """Run every fixture through normalizer → investigator and score top-1.

    A fixture **passes** when the agent's top-1 category equals
    ``expect.category``. The run **passes** when the top-1 fraction meets
    ``targets.top1_min`` (S4.1 gate, §19/§31.7). Failures are isolated: a
    schema-invalid output or LLM error marks *its* fixture failed and the
    run continues — the report is always produced.
    """
    results: list[FixtureInvestigationResult] = []
    for fixture in golden.fixtures:
        try:
            results.append(await _investigate(agent, fixture))
        except (ValueError, LLMError) as exc:
            # Schema-invalid output / LLM error: fails this fixture only.
            results.append(
                FixtureInvestigationResult(
                    fixture_id=fixture.id,
                    title=fixture.title,
                    suggested="unknown",
                    category=None,
                    correct=False,
                    schema_valid=False,
                    passed=False,
                    error=str(exc)[:500],
                )
            )

    total = len(results)
    passed = sum(1 for result in results if result.passed)
    top1_fraction = passed / total
    targets = {"top1_min": golden.targets.top1_min}
    return InvestigationReport(
        agent=INVESTIGATOR_NAME,
        model=model,
        prompt_ref=prompt_ref,
        golden_name=golden.name,
        golden_version=golden.version,
        golden_fixtures=total,
        targets=targets,
        totals=InvestigationTotals(
            fixtures=total,
            passed=passed,
            failed=total - passed,
            top1_fraction=top1_fraction,
            schema_valid_fraction=sum(1 for result in results if result.schema_valid) / total,
        ),
        passed=top1_fraction >= targets["top1_min"],
        generated_at=_utcnow(),
        fixtures=results,
    )
