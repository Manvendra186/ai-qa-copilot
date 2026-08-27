"""S1.4 eval runner — agents over the golden set, scored against §31.7.

Runs the Test Design Agent over every golden fixture and builds the JSON
report (the S1.4 artifact, ``EvaluationReport``). Failures are isolated per
fixture: a schema violation or an LLM error is recorded on its fixture and
the run continues — the report *is* the regression artifact (§22 "regression
tests for every important prompt/schema change", §31.6 prompt pinning).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..agents import TestDesignAgent, TestDesignInput
from ..gateway import LLMError
from .golden import GoldenFixture, GoldenSet, step_coverage

__all__ = [
    "EvalTotals",
    "EvaluationReport",
    "FixtureEvalResult",
    "run_test_design_eval",
]


class FixtureEvalResult(BaseModel):
    """Outcome of one golden fixture.

    ``schema_valid`` is False when the model output failed the §12
    ``TestSuite`` schema *or* the LLM call itself failed (``error`` says
    which). ``coverage`` is the §31.7 step-coverage score vs the oracle.
    """

    model_config = ConfigDict(frozen=True)

    fixture_id: str
    title: str
    schema_valid: bool
    coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    case_count: int = Field(default=0, ge=0)
    passed: bool
    error: str | None = None
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)


class EvalTotals(BaseModel):
    """Aggregate scores for the run."""

    model_config = ConfigDict(frozen=True)

    fixtures: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    schema_valid_fraction: float = Field(ge=0.0, le=1.0)
    coverage_avg: float | None = Field(default=None, ge=0.0, le=1.0)


class EvaluationReport(BaseModel):
    """The S1.4 artifact: ``eval run`` emits this as JSON (stdout / --report)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    agent: str = "test-designer"
    model: str
    prompt_ref: str
    golden_name: str
    golden_version: str
    golden_fixtures: int = Field(ge=1)
    targets: dict[str, float]
    totals: EvalTotals
    passed: bool
    generated_at: str
    fixtures: list[FixtureEvalResult] = Field(min_length=1)


async def run_test_design_eval(
    golden: GoldenSet,
    *,
    agent: TestDesignAgent,
    model: str,
    prompt_ref: str,
) -> EvaluationReport:
    """Run *agent* over every golden fixture and score the §31.7 targets.

    A fixture passes when its output is schema-valid (§12 ``TestSuite``) and
    its steps cover ≥ ``oracle_step_coverage_min`` of the oracle. The report
    passes only when *every* fixture passes — the S1.2 gate was per
    requirement, and one silent regression would otherwise hide in the mean.
    """
    results = [
        await _eval_fixture(fixture, golden=golden, agent=agent) for fixture in golden.fixtures
    ]
    scored = [r.coverage for r in results if r.coverage is not None]
    coverage_avg = sum(scored) / len(scored) if scored else None
    totals = EvalTotals(
        fixtures=len(results),
        passed=sum(1 for r in results if r.passed),
        failed=sum(1 for r in results if not r.passed),
        schema_valid_fraction=sum(1 for r in results if r.schema_valid) / len(results),
        coverage_avg=coverage_avg,
    )
    return EvaluationReport(
        model=model,
        prompt_ref=prompt_ref,
        golden_name=golden.name,
        golden_version=golden.version,
        golden_fixtures=len(golden.fixtures),
        targets={
            "schema_valid_min": golden.targets.schema_valid_min,
            "oracle_step_coverage_min": golden.targets.oracle_step_coverage_min,
        },
        totals=totals,
        passed=all(r.passed for r in results),
        generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        fixtures=results,
    )


async def _eval_fixture(
    fixture: GoldenFixture,
    *,
    golden: GoldenSet,
    agent: TestDesignAgent,
) -> FixtureEvalResult:
    try:
        outcome = await agent.run(
            TestDesignInput(
                title=fixture.title,
                content=fixture.content,
                acceptance_criteria=tuple(fixture.acceptance_criteria),
            )
        )
    except (ValueError, LLMError) as exc:
        # ValueError: output failed the §12 TestSuite schema. LLMError: the
        # local LLM call failed (transport/HTTP). Both are fixture failures —
        # recorded, never fatal to the run.
        return FixtureEvalResult(
            fixture_id=fixture.id,
            title=fixture.title,
            schema_valid=False,
            passed=False,
            error=str(exc),
        )
    generated = [step for case in outcome.suite.test_cases for step in case.steps]
    coverage = step_coverage(generated, fixture.oracle_steps)
    return FixtureEvalResult(
        fixture_id=fixture.id,
        title=fixture.title,
        schema_valid=True,
        coverage=coverage,
        case_count=len(outcome.suite.test_cases),
        passed=coverage >= golden.targets.oracle_step_coverage_min,
        tokens_in=outcome.call.usage.tokens_in,
        tokens_out=outcome.call.usage.tokens_out,
        latency_ms=outcome.call.latency_ms,
    )
