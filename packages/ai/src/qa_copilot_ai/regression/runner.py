"""S6.3 eval runner — the deterministic regression recommender vs the golden set.

Replays each fixture's :class:`qa_copilot_domain.ImpactSet` +
:class:`qa_copilot_domain.RiskRanking` through the pure, LLM-free
:func:`qa_copilot_repository.recommend` core and scores the ranked order
against the fixture's ``expect`` (build bible §19 S6.3, §22, §31.7).

Because the core is deterministic (no LLM, no network, no DB), the eval is a
**synchronous, fully offline** pass: the S6.3 exit criterion is a 100% match —
every fixture's expected ranked order (and impact-kind join, where asserted)
must hold. A fixture **fails** only when the core's join/order/tie-break/
truncation diverges from its expected output.

``qa_copilot_repository`` is a **runtime-only dependency** (imported here, not
in ``pyproject.toml``) — the same pattern as the S4.1 investigator runner with
``qa_copilot_execution``: the monorepo venv provides both packages.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field
from qa_copilot_repository import recommend

from .golden import RegressionFixture, RegressionGoldenSet


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


class RegressionCaseResult(BaseModel):
    """One fixture's deterministic outcome (stable JSON contract).

    ``expected_keys`` is the fixture's expected ranked order; ``actual_keys``
    is the core's; ``correct`` is the order match; ``kinds_match`` records the
    impact-kind join check (``None`` when the fixture does not assert kinds);
    ``passed`` is the S6.3 per-fixture gate.
    """

    fixture_id: str
    title: str
    top_n: int
    expected_keys: list[str]
    actual_keys: list[str]
    expected_kinds: list[str] | None
    #: One entry per recommendation (``None`` where the join found no kind);
    #: unlike ``expected_kinds`` this is never the whole list being absent.
    actual_kinds: list[str | None]
    correct: bool
    kinds_match: bool | None
    passed: bool


class RegressionTotals(BaseModel):
    """Aggregate S6.3 gate numbers (the deterministic core is 100% or bust)."""

    fixtures: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_fraction: float = Field(ge=0.0, le=1.0)


class RegressionReport(BaseModel):
    """The S6.3 JSON artifact (same shape family as the S1.4/S2.3/S4.1 reports)."""

    schema_version: int = 1
    step: str = "S6.3"
    golden_name: str
    golden_version: str
    golden_fixtures: int = Field(ge=1)
    targets: dict[str, float]
    totals: RegressionTotals
    passed: bool
    generated_at: str
    #: Optional S6.3 advisor brief (LLM or deterministic stub) over one
    #: fixture's recommendation set — present only when the CLI is run with
    #: ``--advise``; never part of the deterministic gate.
    summary: str | None = None
    summary_source: str | None = None
    summary_fixture: str | None = None
    cases: list[RegressionCaseResult]


def _evaluate(fixture: RegressionFixture) -> RegressionCaseResult:
    """Run one fixture through the deterministic core and score its order."""
    result = recommend(fixture.impact, fixture.ranking, top_n=fixture.top_n)
    actual_keys = [item.test_key for item in result.recommendations]
    actual_kinds = [
        item.impact_kind.value if item.impact_kind is not None else None
        for item in result.recommendations
    ]

    correct = actual_keys == fixture.expect.ordered_keys
    if fixture.expect.impact_kinds is not None:
        kinds_match = actual_kinds == fixture.expect.impact_kinds
    else:
        kinds_match = None

    return RegressionCaseResult(
        fixture_id=fixture.id,
        title=fixture.title,
        top_n=fixture.top_n,
        expected_keys=fixture.expect.ordered_keys,
        actual_keys=actual_keys,
        expected_kinds=fixture.expect.impact_kinds,
        actual_kinds=actual_kinds,
        correct=correct,
        kinds_match=kinds_match,
        passed=correct and kinds_match is not False,
    )


def run_regression_eval(golden: RegressionGoldenSet) -> RegressionReport:
    """Run every fixture through the deterministic core and score the order.

    A fixture **passes** when the core's ranked order matches ``expect`` (and
    the impact-kind join, where the fixture asserts it). The run **passes**
    when the pass fraction meets ``targets.pass_min`` (S6.3 gate = 1.0). The
    core is deterministic, so there is no per-failure isolation to manage —
    every fixture is evaluated.

    Raises:
        ValueError: when the deterministic core rejects an input (e.g. a
            ``top_n < 1`` fixture) — a golden-set authoring error (fail loud).
    """
    results = [_evaluate(fixture) for fixture in golden.fixtures]
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    pass_fraction = passed / total
    targets = {"pass_min": golden.targets.pass_min}
    return RegressionReport(
        golden_name=golden.name,
        golden_version=golden.version,
        golden_fixtures=total,
        targets=targets,
        totals=RegressionTotals(
            fixtures=total,
            passed=passed,
            failed=total - passed,
            pass_fraction=pass_fraction,
        ),
        passed=pass_fraction >= targets["pass_min"],
        generated_at=_utcnow(),
        cases=results,
    )


__all__ = [
    "RegressionCaseResult",
    "RegressionReport",
    "RegressionTotals",
    "run_regression_eval",
]
