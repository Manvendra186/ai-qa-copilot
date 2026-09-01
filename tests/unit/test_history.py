"""S6.2 regression intelligence: deterministic flaky/risk core over history.

Covers the pure core (``compute_test_stats`` / ``compute_risk_score`` /
``rank_tests`` / ``strongest_impact_kind`` / ``build_risk_ranking``) without a
DB or LLM, plus the ORM seam ``project_test_history`` — with a fake session for
its grouping/ordering/diagnosis logic and a real scratch DB on Postgres :5433
for the full query (the S2.1/S3.3/S5.1 pattern).
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from qa_copilot_domain.enums import (
    FailureCategory,
    ImpactKind,
    Priority,
    RiskLevel,
    RunStatus,
    TestResultStatus,
)
from qa_copilot_repository import (
    TestOutcome,
    TestRiskInput,
    build_risk_ranking,
    compute_risk_score,
    compute_test_stats,
    db,
    models,
    project_test_history,
    rank_tests,
    strongest_impact_kind,
)
from sqlalchemy.orm import Session

PASSED = TestResultStatus.PASSED
FAILED = TestResultStatus.FAILED
FLAKY = TestResultStatus.FLAKY
SKIPPED = TestResultStatus.SKIPPED

# Scratch Postgres (the test_auth.py / S2.1-S3.3-S5.1 pattern): a DEDICATED
# database on :5433 so the main dev ``qa_copilot`` schema is never touched.
TEST_DB = "qa_copilot_history_test"
TEST_URL = f"postgresql+psycopg://qa:qa@localhost:5433/{TEST_DB}"
ADMIN_URL = "postgresql+psycopg://qa:qa@localhost:5433/postgres"


def _admin(sql: str) -> None:
    """Run DDL against the ``postgres`` maintenance database."""
    from sqlalchemy import create_engine, text

    engine = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.fixture
def scratch() -> Iterator[Session]:
    """A clean scratch Postgres DB (``qa_copilot_history_test`` on :5433).

    A dedicated database (the test_auth.py / S2.1-S3.3-S5.1 pattern) so the main
    dev ``qa_copilot`` schema is never touched. The schema is created before the
    yield; the session is closed (releasing the ACCESS SHARE locks its SELECTs
    hold) before the database is dropped in teardown — otherwise the drop would
    block on those locks.
    """
    _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin(f"CREATE DATABASE {TEST_DB}")
    engine = db.make_engine(TEST_URL)
    try:
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            models.Base.metadata.create_all(conn)
        factory = db.make_session_factory(engine)
        with factory() as session:
            yield session
    finally:
        engine.dispose()
        _admin(f"DROP DATABASE IF EXISTS {TEST_DB}")


class FakeResult:
    def __init__(self, rows: object) -> None:
        self._rows = rows

    def all(self) -> object:
        return self._rows


class FakeSession:
    def __init__(self, rows: object) -> None:
        self._rows = rows
        self.executed: list[object] = []

    def execute(self, stmt: object) -> FakeResult:
        self.executed.append(stmt)
        return FakeResult(self._rows)


# --- compute_test_stats -----------------------------------------------------


def test_compute_test_stats_counts_and_last() -> None:
    outcomes = (
        TestOutcome("r1", 0, PASSED),
        TestOutcome("r2", 1, FLAKY),
        TestOutcome("r3", 2, PASSED),
        TestOutcome("r4", 3, FAILED),
        TestOutcome("r5", 4, PASSED),
    )
    stats = compute_test_stats("t", outcomes)
    assert stats.test_key == "t"
    assert stats.executions == 5
    assert stats.passed == 3
    assert stats.failed == 1
    assert stats.flaky == 1
    assert stats.skipped == 0
    assert stats.flakiness_rate == pytest.approx(0.2)
    assert stats.failure_rate == pytest.approx(0.2)
    assert stats.last_status == PASSED
    assert stats.last_run_id == "r5"
    assert stats.insufficient_samples is False
    assert stats.is_flaky is False  # 0.2 < 0.4 threshold
    assert stats.is_failing is False  # 0.2 < 0.5 threshold


def test_compute_test_stats_flags_flaky_when_rate_meets_threshold() -> None:
    outcomes = tuple(
        TestOutcome(f"r{i}", i, FLAKY if i < 2 else PASSED) for i in range(5)
    )  # 2 flaky / 5 = 0.4 >= 0.4
    stats = compute_test_stats("t", outcomes)
    assert stats.flakiness_rate == pytest.approx(0.4)
    assert stats.is_flaky is True


def test_compute_test_stats_flags_failing_by_recent_window() -> None:
    outcomes = tuple(TestOutcome(f"r{i}", i, FAILED) for i in range(5))
    stats = compute_test_stats("t", outcomes)
    assert stats.failure_rate == pytest.approx(1.0)
    assert stats.recent_failure_rate == pytest.approx(1.0)
    assert stats.is_failing is True


def test_compute_test_stats_skipped_excluded_from_denominators() -> None:
    outcomes = (
        TestOutcome("r1", 0, PASSED),
        TestOutcome("r2", 1, SKIPPED),
        TestOutcome("r3", 2, SKIPPED),
        TestOutcome("r4", 3, PASSED),
    )
    stats = compute_test_stats("t", outcomes)
    assert stats.executions == 2
    assert stats.skipped == 2
    assert stats.flakiness_rate == 0.0
    assert stats.failure_rate == 0.0


def test_compute_test_stats_insufficient_samples_no_flag() -> None:
    outcomes = tuple(TestOutcome(f"r{i}", i, FLAKY) for i in range(2))  # 2 < DEFAULT_MIN_SAMPLE (3)
    stats = compute_test_stats("t", outcomes)
    assert stats.executions == 2
    assert stats.flakiness_rate == pytest.approx(1.0)
    assert stats.insufficient_samples is True
    assert stats.is_flaky is False


def test_compute_test_stats_empty() -> None:
    stats = compute_test_stats("t", ())
    assert stats.executions == 0
    assert stats.last_status is None
    assert stats.last_run_id is None
    assert stats.insufficient_samples is True
    assert stats.is_flaky is False
    assert stats.is_failing is False


def test_compute_test_stats_flaky_diagnosis_counts_as_flaky() -> None:
    # A FAILED outcome diagnosed ``flaky_behavior`` counts as a flaky event.
    outcomes = (
        TestOutcome("r1", 0, FAILED, flaky_diagnosis=True),
        TestOutcome("r2", 1, FAILED, flaky_diagnosis=True),
        TestOutcome("r3", 2, PASSED),
        TestOutcome("r4", 3, PASSED),
        TestOutcome("r5", 4, PASSED),
    )
    stats = compute_test_stats("t", outcomes)
    assert stats.failed == 2
    assert stats.flaky == 2  # both diagnosed-flaky
    assert stats.is_flaky is True  # 2/5 = 0.4 >= 0.4


# --- compute_risk_score -----------------------------------------------------


def test_compute_risk_score_zero_when_no_factors() -> None:
    score = compute_risk_score(
        impact_kind=None,
        failure_rate=0.0,
        flakiness_rate=0.0,
        requirement_risk=None,
        test_case_priority=None,
    )
    assert score == 0.0


def test_compute_risk_score_impact_kind_is_monotonic() -> None:
    def score(impact_kind: ImpactKind | None) -> float:
        return compute_risk_score(
            impact_kind=impact_kind,
            failure_rate=0.0,
            flakiness_rate=0.0,
            requirement_risk=None,
            test_case_priority=None,
        )

    none_ = score(None)
    ref = score(ImpactKind.REFERENCED)
    gen = score(ImpactKind.GENERATED)
    direct = score(ImpactKind.DIRECT)
    assert none_ < ref < gen < direct


def test_compute_risk_score_weighs_rates_and_context() -> None:
    low = compute_risk_score(
        impact_kind=None,
        failure_rate=0.0,
        flakiness_rate=0.0,
        requirement_risk=RiskLevel.LOW,
        test_case_priority=Priority.LOW,
    )
    high = compute_risk_score(
        impact_kind=ImpactKind.DIRECT,
        failure_rate=1.0,
        flakiness_rate=1.0,
        requirement_risk=RiskLevel.HIGH,
        test_case_priority=Priority.HIGH,
    )
    assert low < high


def test_compute_risk_score_deterministic() -> None:
    def score() -> float:
        return compute_risk_score(
            impact_kind=ImpactKind.GENERATED,
            failure_rate=0.5,
            flakiness_rate=0.25,
            requirement_risk=RiskLevel.MEDIUM,
            test_case_priority=Priority.HIGH,
        )

    assert score() == score()


# --- strongest_impact_kind --------------------------------------------------


def test_strongest_impact_kind_picks_highest() -> None:
    kinds = [ImpactKind.REFERENCED, ImpactKind.DIRECT, ImpactKind.GENERATED]
    assert strongest_impact_kind(kinds) is ImpactKind.DIRECT
    assert strongest_impact_kind([ImpactKind.REFERENCED]) is ImpactKind.REFERENCED


def test_strongest_impact_kind_empty_or_duplicated() -> None:
    assert strongest_impact_kind([]) is None
    kinds = [ImpactKind.GENERATED, ImpactKind.GENERATED]
    assert strongest_impact_kind(kinds) is ImpactKind.GENERATED


# --- rank_tests -------------------------------------------------------------


def test_rank_tests_orders_by_score_then_key() -> None:
    failing = (TestOutcome("r1", 0, FAILED), TestOutcome("r2", 1, FAILED))
    inputs = [
        TestRiskInput("zeta", failing, ImpactKind.DIRECT),
        TestRiskInput("alpha", failing, ImpactKind.DIRECT),  # same score, earlier key
        TestRiskInput("beta", (), ImpactKind.REFERENCED),  # lower score
    ]
    ranked = rank_tests(inputs)
    assert [t.test_key for t in ranked] == ["alpha", "zeta", "beta"]
    assert ranked[0].risk_score == ranked[1].risk_score  # tie
    assert ranked[2].risk_score < ranked[0].risk_score
    assert ranked[0].stats.executions == 2
    assert "impact:direct" in ranked[0].signals
    assert "no-run-history" in ranked[2].signals


def test_rank_tests_is_stable_and_empty_safe() -> None:
    inputs = [
        TestRiskInput("b", (), ImpactKind.REFERENCED),
        TestRiskInput("a", (), ImpactKind.REFERENCED),
    ]
    assert [t.test_key for t in rank_tests(inputs)] == ["a", "b"]
    assert rank_tests([]) == []


# --- build_risk_ranking -----------------------------------------------------


def test_build_risk_ranking_wraps_and_serializes() -> None:
    inputs = [TestRiskInput("a", (TestOutcome("r1", 0, PASSED),))]
    ranking = build_risk_ranking("proj", inputs)
    assert ranking.project_id == "proj"
    assert [t.test_key for t in ranking.ranked] == ["a"]
    assert ranking.min_sample == 3
    assert ranking.computed_at is not None
    payload = json.loads(ranking.model_dump_json())
    assert payload["project_id"] == "proj"
    assert payload["ranked"][0]["test_key"] == "a"
    assert payload["ranked"][0]["stats"]["executions"] == 1


# --- project_test_history (ORM seam) ----------------------------------------


def test_project_test_history_groups_orders_and_flags_diagnosis() -> None:
    # rows are (test_case_id, run_id, status, failure_category); run1 is older.
    rows = [
        ("tc-checkout", "run1", PASSED, None),
        ("tc-login", "run1", FAILED, FailureCategory.FLAKY_BEHAVIOR),
        ("tc-checkout", "run2", FLAKY, None),
        ("tc-login", "run2", PASSED, None),
    ]
    grouped = project_test_history(cast(Session, FakeSession(rows)), "proj")
    assert set(grouped) == {"tc-checkout", "tc-login"}

    checkout = grouped["tc-checkout"]
    assert [o.run_id for o in checkout] == ["run1", "run2"]
    assert [o.status for o in checkout] == [PASSED, FLAKY]
    assert [o.run_order for o in checkout] == [0, 1]

    login = grouped["tc-login"]
    assert login[0].flaky_diagnosis is True  # FAILED diagnosed flaky_behavior
    assert login[1].flaky_diagnosis is False
    # the diagnosis flows into the pure core's flaky count
    assert compute_test_stats("tc-login", login, min_sample=2).flaky == 1


def test_project_test_history_end_to_end(scratch: Session) -> None:
    session = scratch
    org = "6f000000-0000-4000-8000-000000000001"
    proj = "6f000000-0000-4000-8000-000000000002"
    other = "6f000000-0000-4000-8000-000000000003"
    tc = "6f000000-0000-4000-8000-000000000041"
    run1 = "6f000000-0000-4000-8000-000000000011"
    run2 = "6f000000-0000-4000-8000-000000000012"
    other_run = "6f000000-0000-4000-8000-000000000013"
    res1 = "6f000000-0000-4000-8000-000000000021"
    res0 = "6f000000-0000-4000-8000-000000000022"
    res2 = "6f000000-0000-4000-8000-000000000023"
    resother = "6f000000-0000-4000-8000-000000000024"
    fail2 = "6f000000-0000-4000-8000-000000000032"

    # organizations + projects first (FK parent before child), then runs,
    # then results, then the diagnosis — each group flushed so the FK
    # parent is committed before its dependents.
    session.add(models.Organization(id=org, name="Hist Org"))
    session.add(models.Project(id=proj, organization_id=org, name="Hist Project"))
    session.add(models.Project(id=other, organization_id=org, name="Other Project"))
    session.flush()
    # runs: run1 (older) and run2 on this project, other_run on another.
    session.add(
        models.TestRun(
            id=run1,
            project_id=proj,
            status=RunStatus.COMPLETED,
            started_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    session.add(
        models.TestRun(
            id=run2,
            project_id=proj,
            status=RunStatus.COMPLETED,
            started_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
    )
    session.add(
        models.TestRun(
            id=other_run,
            project_id=other,
            status=RunStatus.COMPLETED,
            started_at=datetime(2026, 8, 3, tzinfo=UTC),
        )
    )
    session.flush()
    # results: run1 tc PASSED (+ one unlinked, must be omitted), run2 tc FAILED,
    # other tc FAILED (must be excluded by project filter).
    session.add(models.TestResult(id=res1, run_id=run1, test_case_id=tc, status=PASSED))
    session.add(models.TestResult(id=res0, run_id=run1, test_case_id=None, status=FAILED))
    session.add(models.TestResult(id=res2, run_id=run2, test_case_id=tc, status=FAILED))
    session.add(models.TestResult(id=resother, run_id=other_run, test_case_id=tc, status=FAILED))
    session.flush()
    # the flaky_behavior diagnosis on run2's failed result.
    session.add(
        models.Failure(id=fail2, test_result_id=res2, category=FailureCategory.FLAKY_BEHAVIOR)
    )
    session.flush()

    grouped = project_test_history(session, proj)
    assert set(grouped) == {tc}
    outcomes = grouped[tc]
    assert [o.run_id for o in outcomes] == [run1, run2]  # oldest first, proj only
    assert [o.status for o in outcomes] == [PASSED, FAILED]
    assert outcomes[1].flaky_diagnosis is True

    stats = compute_test_stats(tc, outcomes)
    assert stats.executions == 2
    assert stats.passed == 1
    assert stats.failed == 1
    assert stats.flaky == 1  # the diagnosed-failed outcome
    assert stats.insufficient_samples is True  # 2 < DEFAULT_MIN_SAMPLE (3)
