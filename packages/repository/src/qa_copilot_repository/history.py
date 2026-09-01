"""Flaky + risk core over run history (build bible §7, §19 S6.2).

Deterministic, LLM-free statistics and risk scoring over a project's
``test_runs`` / ``test_results`` / ``failures`` history. This is the
regression-intelligence signal the S6.3 recommender (LLM) ranks on top of the
S6.1 change-impact set, and the S6.4 API serves it as JSON (build bible
§19 S6.1–S6.4).

Core functions are **pure** — no DB, no LLM, no network, no wall clock — so
they are deterministic (equal inputs ⇒ equal output), auditable, and
unit-testable without a database (the S2.1/S3.3/S5.1 pattern). The thin ORM
seam (:func:`project_test_history`) reads a project's execution history and
hands the pure core its per-test outcomes.

Per-test history (build bible §19 S6.2):

- ``flakiness_rate`` — the share of executions that were flaky (a ``flaky``
  outcome *or* a ``flaky_behavior`` diagnosis);
- ``failure_rate`` / ``recent_failure_rate`` — the share of executions (all /
  most-recent window) that ended ``failed``;
- a min-sample gate: fewer than ``DEFAULT_MIN_SAMPLE`` executions raises no
  flaky/failing flag ("no flags from a single run").

The risk score is a deterministic, explainable
``f(impact kind, failure rate, flakiness rate, requirement risk,
test-case priority)`` (see :func:`compute_risk_score`); :func:`rank_tests`
orders a set of tests by it with a stable tie-break on ``test_key``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from qa_copilot_domain import (
    DEFAULT_FAILING_THRESHOLD,
    DEFAULT_FLAKY_THRESHOLD,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_RECENT_WINDOW,
    ImpactKind,
    Priority,
    RiskLevel,
    RiskRanking,
    TestHistoryStats,
    TestResultStatus,
    TestRisk,
)
from qa_copilot_domain.enums import FailureCategory
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "IMPACT_WEIGHT",
    "RISK_WEIGHT_FAILURE",
    "RISK_WEIGHT_FLAKY",
    "RISK_WEIGHT_PRIORITY",
    "RISK_WEIGHT_REQUIREMENT",
    "TestOutcome",
    "TestRiskInput",
    "build_risk_ranking",
    "compute_risk_score",
    "compute_test_stats",
    "project_test_history",
    "rank_tests",
    "strongest_impact_kind",
]

#: Outcomes that actually ran. ``skipped`` / ``pending`` never executed and are
#: excluded from every rate's denominator (a skipped run tells us nothing).
_EXECUTED: frozenset[TestResultStatus] = frozenset(
    {TestResultStatus.PASSED, TestResultStatus.FAILED, TestResultStatus.FLAKY}
)

#: Deterministic risk-score weights (build bible §19 S6.2). The score is a
#: bounded, explainable sum with a *monotonic* mapping (higher impact, higher
#: failure/flakiness, higher requirement risk, or higher test-case priority
#: each raise the score). Impact kind dominates — a directly-impacted test is
#: the most relevant regression candidate — then live failure/flakiness, then
#: the static §10 context (requirement risk, test-case priority). The total
#: stays in [0, 110].
IMPACT_WEIGHT: dict[ImpactKind, float] = {
    ImpactKind.DIRECT: 40.0,
    ImpactKind.GENERATED: 25.0,
    ImpactKind.REFERENCED: 15.0,
}
RISK_WEIGHT_FAILURE = 30.0
RISK_WEIGHT_FLAKY = 20.0
RISK_WEIGHT_REQUIREMENT: dict[str, float] = {"high": 10.0, "medium": 5.0, "low": 2.0}
RISK_WEIGHT_PRIORITY: dict[str, float] = {"high": 10.0, "medium": 5.0, "low": 2.0}


@dataclass(frozen=True, slots=True)
class TestOutcome:
    """One normalized execution of a test (a ``test_results`` row, §10).

    ``run_order`` is the test's position across the project's runs (0 =
    oldest, ascending) — it drives the ``recent`` window and the ``last_*``
    fields, so the caller must supply a total time order (the ORM seam orders
    by run ``started_at`` / ``created_at``). ``flaky_diagnosis`` is True when
    the linked ``Failure.category`` is ``flaky_behavior`` (the S4.1 diagnosis)
    — a test that ended ``failed`` but was *diagnosed* as flaky still counts
    as a flaky event.
    """

    run_id: str
    run_order: int
    status: TestResultStatus
    flaky_diagnosis: bool = False


@dataclass(frozen=True, slots=True)
class TestRiskInput:
    """Per-test inputs to the S6.2 ranking (one entry per test to rank).

    ``outcomes`` are the test's normalized execution history (fed to
    :func:`compute_test_stats`); ``impact_kind`` is its strongest change-impact
    kind (S6.1 — the S6.3 recommender resolves ``ImpactedTest.kinds`` through
    :func:`strongest_impact_kind`); ``requirement_risk`` /
    ``test_case_priority`` are the §10 context the risk score weighs. All but
    the history are optional (a test with no run history still ranks on its
    impact + context).
    """

    test_key: str
    outcomes: tuple[TestOutcome, ...] = ()
    impact_kind: ImpactKind | None = None
    requirement_risk: RiskLevel | None = None
    test_case_priority: Priority | None = None


def compute_test_stats(
    test_key: str,
    outcomes: Sequence[TestOutcome],
    *,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    flaky_threshold: float = DEFAULT_FLAKY_THRESHOLD,
    failing_threshold: float = DEFAULT_FAILING_THRESHOLD,
) -> TestHistoryStats:
    """Deterministic per-test stats + flaky/failing flags (S6.2 core).

    Pure: no DB, no LLM, no wall clock. ``executions`` counts only *executed*
    outcomes (``passed`` / ``failed`` / ``flaky``); ``skipped`` is reported
    but excluded from the denominators. A test with fewer than ``min_sample``
    executions is ``insufficient_samples`` and raises no flaky/failing flag
    ("no flags from a single run"). Equal inputs ⇒ equal output.
    """
    executed = [o for o in outcomes if o.status in _EXECUTED]
    executed.sort(key=lambda o: o.run_order)  # stable; most-recent = last

    total = len(executed)
    passed = sum(1 for o in executed if o.status is TestResultStatus.PASSED)
    failed = sum(1 for o in executed if o.status is TestResultStatus.FAILED)
    flaky = sum(1 for o in executed if o.status is TestResultStatus.FLAKY or o.flaky_diagnosis)
    skipped = sum(1 for o in outcomes if o.status is TestResultStatus.SKIPPED)

    insufficient = total < min_sample
    flakiness_rate = flaky / total if total else 0.0
    failure_rate = failed / total if total else 0.0

    window = executed[-recent_window:] if executed and recent_window > 0 else []
    recent_failed = sum(1 for o in window if o.status is TestResultStatus.FAILED)
    recent_failure_rate = recent_failed / len(window) if window else 0.0

    last = executed[-1] if executed else None
    is_flaky = (not insufficient) and flakiness_rate >= flaky_threshold
    is_failing = (not insufficient) and recent_failure_rate >= failing_threshold

    return TestHistoryStats(
        test_key=test_key,
        executions=total,
        passed=passed,
        failed=failed,
        flaky=flaky,
        skipped=skipped,
        flakiness_rate=flakiness_rate,
        failure_rate=failure_rate,
        recent_failure_rate=recent_failure_rate,
        is_flaky=is_flaky,
        is_failing=is_failing,
        insufficient_samples=insufficient,
        last_status=last.status if last is not None else None,
        last_run_id=last.run_id if last is not None else None,
    )


def compute_risk_score(
    *,
    impact_kind: ImpactKind | None,
    failure_rate: float,
    flakiness_rate: float,
    requirement_risk: RiskLevel | None,
    test_case_priority: Priority | None,
) -> float:
    """Deterministic risk score = f(impact, failure, flakiness, risk, priority).

    A bounded, explainable sum (see :data:`IMPACT_WEIGHT` etc.) that is
    *monotonic* in every factor: higher impact kind, higher failure/flakiness
    rate, higher requirement risk, or higher test-case priority each raise the
    score. Pure and deterministic — equal inputs ⇒ equal score. The S6.3
    recommender orders a set of tests by it (stable tie-break on ``test_key``).
    """
    score = 0.0
    if impact_kind is not None:
        score += IMPACT_WEIGHT.get(impact_kind, 0.0)
    score += RISK_WEIGHT_FAILURE * failure_rate
    score += RISK_WEIGHT_FLAKY * flakiness_rate
    if requirement_risk is not None:
        score += RISK_WEIGHT_REQUIREMENT.get(requirement_risk.value, 0.0)
    if test_case_priority is not None:
        score += RISK_WEIGHT_PRIORITY.get(test_case_priority.value, 0.0)
    return round(score, 6)


def strongest_impact_kind(kinds: Sequence[ImpactKind]) -> ImpactKind | None:
    """The highest-weight impact kind (``direct`` > ``generated`` > ``referenced``).

    S6.1's ``ImpactedTest.kinds`` can carry several; the risk score weighs the
    strongest (the most direct hit). Deterministic: duplicates collapse and the
    max weight is unique, so the same set always yields the same kind.
    """
    unique = {k for k in kinds}
    if not unique:
        return None
    return max(unique, key=lambda k: (IMPACT_WEIGHT.get(k, 0.0), k.value))


def _signals(
    stats: TestHistoryStats,
    *,
    impact_kind: ImpactKind | None,
    requirement_risk: RiskLevel | None,
    test_case_priority: Priority | None,
) -> list[str]:
    """Deterministic, human-readable reasons the flags fired (S6.4 UI chips).

    Fixed order and content for equal inputs — this is the evidence trail the
    S6.3/S6.4 UI shows next to a ranked test ("flaky 50%", "impact direct", …).
    """
    out: list[str] = []
    if impact_kind is not None:
        out.append(f"impact:{impact_kind.value}")
    if stats.executions == 0:
        out.append("no-run-history")
    else:
        if stats.is_flaky:
            out.append(f"flaky {round(stats.flakiness_rate * 100)}%")
        if stats.is_failing:
            out.append(f"failing {round(stats.recent_failure_rate * 100)}% recently")
        if stats.insufficient_samples:
            out.append(f"low-sample ({stats.executions})")
    if requirement_risk is not None:
        out.append(f"requirement-risk:{requirement_risk.value}")
    if test_case_priority is not None:
        out.append(f"priority:{test_case_priority.value}")
    return out


def rank_tests(
    inputs: Sequence[TestRiskInput],
    *,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    flaky_threshold: float = DEFAULT_FLAKY_THRESHOLD,
    failing_threshold: float = DEFAULT_FAILING_THRESHOLD,
) -> list[TestRisk]:
    """Deterministic ranked list — highest risk first, stable ties (S6.2 core).

    Sort key ``(-risk_score, test_key)``: equal scores order by ``test_key``
    ascending, so the same inputs always yield the same order. Pure (no DB /
    no LLM / no wall clock); the S6.3 recommender wraps the top-N of this in
    the LLM's rationale.
    """
    ranked: list[TestRisk] = []
    for item in inputs:
        stats = compute_test_stats(
            item.test_key,
            item.outcomes,
            min_sample=min_sample,
            recent_window=recent_window,
            flaky_threshold=flaky_threshold,
            failing_threshold=failing_threshold,
        )
        score = compute_risk_score(
            impact_kind=item.impact_kind,
            failure_rate=stats.failure_rate,
            flakiness_rate=stats.flakiness_rate,
            requirement_risk=item.requirement_risk,
            test_case_priority=item.test_case_priority,
        )
        ranked.append(
            TestRisk(
                test_key=item.test_key,
                risk_score=score,
                signals=_signals(
                    stats,
                    impact_kind=item.impact_kind,
                    requirement_risk=item.requirement_risk,
                    test_case_priority=item.test_case_priority,
                ),
                stats=stats,
                impact_kind=item.impact_kind,
                requirement_risk=item.requirement_risk,
                test_case_priority=item.test_case_priority,
            )
        )
    ranked.sort(key=lambda t: (-t.risk_score, t.test_key))
    return ranked


def build_risk_ranking(
    project_id: str,
    inputs: Sequence[TestRiskInput],
    *,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    recent_window: int = DEFAULT_RECENT_WINDOW,
    flaky_threshold: float = DEFAULT_FLAKY_THRESHOLD,
    failing_threshold: float = DEFAULT_FAILING_THRESHOLD,
) -> RiskRanking:
    """Wrap :func:`rank_tests` in the serializable :class:`RiskRanking` set.

    Adds the flagging policy (``min_sample`` / window / thresholds) and the
    wall-clock ``computed_at`` (the one non-deterministic field; golden tests
    compare the rest). This is the payload the S6.4 API serves as JSON.
    """
    return RiskRanking(
        project_id=project_id,
        ranked=rank_tests(
            inputs,
            min_sample=min_sample,
            recent_window=recent_window,
            flaky_threshold=flaky_threshold,
            failing_threshold=failing_threshold,
        ),
        min_sample=min_sample,
        recent_window=recent_window,
        flaky_threshold=flaky_threshold,
        failing_threshold=failing_threshold,
        computed_at=datetime.now(UTC),
    )


def project_test_history(
    session: Session,
    project_id: str,
) -> dict[str, list[TestOutcome]]:
    """A project's per-test execution history (S6.2 ORM seam).

    Groups ``test_results`` by ``test_case_id`` (the §10 link) across the
    project's runs, oldest first (run ``started_at`` / ``created_at``, then
    result id), and flags each outcome's ``flaky_behavior`` diagnosis via its
    linked ``Failure``. Results with no ``test_case_id`` are omitted (they
    can't be ranked per test). Deterministic: the same rows ⇒ the same
    outcome lists in the same order. Read-only.
    """
    stmt = (
        select(
            models.TestResult.test_case_id,
            models.TestResult.run_id,
            models.TestResult.status,
            models.Failure.category,
        )
        .join(models.TestRun, models.TestResult.run_id == models.TestRun.id)
        .outerjoin(models.Failure, models.Failure.test_result_id == models.TestResult.id)
        .where(models.TestRun.project_id == project_id)
        .where(models.TestResult.test_case_id.is_not(None))
        .order_by(
            models.TestRun.started_at.asc().nullsfirst(),
            models.TestRun.created_at.asc(),
            models.TestResult.id.asc(),
        )
    )
    rows = session.execute(stmt).all()

    run_order: dict[str, int] = {}
    grouped: dict[str, list[TestOutcome]] = {}
    for test_case_id, run_id, status, category in rows:
        if run_id not in run_order:
            run_order[run_id] = len(run_order)
        outcome = TestOutcome(
            run_id=run_id,
            run_order=run_order[run_id],
            status=status,
            flaky_diagnosis=category == FailureCategory.FLAKY_BEHAVIOR,
        )
        grouped.setdefault(test_case_id, []).append(outcome)
    return grouped
