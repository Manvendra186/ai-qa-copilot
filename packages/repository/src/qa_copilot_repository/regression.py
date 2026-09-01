"""Deterministic regression recommender (build bible §7, §19 S6.3).

Joins the S6.1 change-impact set (which test files to re-run, and *why* —
:func:`qa_copilot_repository.impact.compute_impact`) with the S6.2 flaky/risk
ranking (how risky each is — :func:`qa_copilot_repository.history.build_risk_ranking`
/ :func:`qa_copilot_repository.history.compute_risk_score`) into a
**deterministic, top-N regression recommendation**. This is the LLM-free core
the S6.4 API serves as JSON and the S6.3 optional advisor
(``regression-advisor@1``) summarizes on top of.

The core (:func:`recommend`) is **pure** — it takes two already-computed
domain objects (an :class:`qa_copilot_domain.ImpactSet` and a
:class:`qa_copilot_domain.RiskRanking`) and produces a
:class:`qa_copilot_domain.RecommendationSet`. No DB, no LLM, no network, no
wall clock (except ``computed_at``), so equal inputs always produce equal
output — the S2.1/S3.3/S5.1/S6.1/S6.2 deterministic-core pattern.

Join semantics:

- one :class:`~qa_copilot_domain.RecommenderItem` per **impacted** test file
  (S6.1) — a test that is *not* impacted never enters the recommendation, no
  matter how risky its history is;
- each impacted test is joined with its S6.2 risk by ``test_key`` (the test
  file path); an impacted test with no run history still ranks (score ``0``,
  a ``no-run-history`` rationale);
- the strongest impact kind
  (:func:`qa_copilot_repository.history.strongest_impact_kind`) and the
  changed files that pulled the test in ride along as evidence.

Ordering: ``risk_score`` descending, then ``test_key`` ascending (the stable
tie-break), truncated to ``top_n``; ``rank`` is the 1-based position. The
per-test ``rationale`` is deterministic and human-readable (impact kind,
failure rate, flakiness rate, requirement risk, test-case priority, changed
files) — the evidence trail the S6.4 UI shows next to a ranked test.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from qa_copilot_domain import (
    ImpactKind,
    ImpactSet,
    Priority,
    RecommendationSet,
    RecommenderItem,
    RiskLevel,
    RiskRanking,
    TestHistoryStats,
    TestRisk,
)

from .history import strongest_impact_kind

__all__ = [
    "DEFAULT_TOP_N",
    "recommend",
]

#: Default number of recommendations to surface (build bible §19 S6.3). The
#: S6.4 API can override per request; the deterministic core never exceeds it.
DEFAULT_TOP_N = 10


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One impacted test, joined with its S6.2 risk (before the stable rank)."""

    test_key: str
    risk_score: float
    impact_kind: ImpactKind | None
    changed_files: tuple[str, ...]
    requirement_risk: RiskLevel | None
    test_case_priority: Priority | None
    stats: TestHistoryStats


def _no_history(test_key: str) -> TestHistoryStats:
    """Zero per-test history for an impacted test with no runs (S6.2 shape)."""
    return TestHistoryStats(test_key=test_key)


def _rationale(
    *,
    impact_kind: ImpactKind | None,
    stats: TestHistoryStats,
    requirement_risk: RiskLevel | None,
    test_case_priority: Priority | None,
    changed_files: tuple[str, ...],
) -> list[str]:
    """Deterministic per-test rationale (build bible §19 S6.3 evidence trail).

    Fixed order and content for equal inputs — impact kind, failure rate,
    flakiness rate, requirement risk, test-case priority, changed files. This
    is what the S6.4 UI shows next to a ranked test (the §19 S6.3 chips) and
    what the ``regression-advisor@1`` prompt summarizes.
    """
    out: list[str] = []
    if impact_kind is not None:
        out.append(f"impact:{impact_kind.value}")
    if stats.executions == 0:
        out.append("no-run-history")
    else:
        if stats.failure_rate > 0:
            out.append(f"failure {round(stats.failure_rate * 100)}%")
        if stats.flakiness_rate > 0:
            out.append(f"flaky {round(stats.flakiness_rate * 100)}%")
    if requirement_risk is not None:
        out.append(f"requirement-risk:{requirement_risk.value}")
    if test_case_priority is not None:
        out.append(f"priority:{test_case_priority.value}")
    if changed_files:
        out.append(f"changed:{len(changed_files)}")
    return out


def recommend(
    impact: ImpactSet,
    ranking: RiskRanking,
    *,
    top_n: int = DEFAULT_TOP_N,
) -> RecommendationSet:
    """Join the S6.1 impact set with the S6.2 risk ranking (build bible §19 S6.3).

    Produces a deterministic, top-N
    :class:`~qa_copilot_domain.RecommendationSet`: one
    :class:`~qa_copilot_domain.RecommenderItem` per **impacted** test (S6.1),
    each joined with its S6.2 risk (score + history stats + §10 context),
    ordered by ``risk_score`` descending then ``test_key`` ascending (stable
    tie-break) and truncated to ``top_n``.

    Pure — no DB, no LLM, no network, no wall clock (except ``computed_at``) —
    so equal inputs always yield equal output (the deterministic-core pattern).
    The S6.4 API serves this as JSON; the S6.3 advisor
    (``regression-advisor@1``) optionally summarizes it.

    Raises ``ValueError`` when ``top_n < 1``.
    """
    if top_n < 1:
        raise ValueError("top_n must be >= 1")

    risk_by_key: dict[str, TestRisk] = {risk.test_key: risk for risk in ranking.ranked}

    candidates: list[_Candidate] = []
    for impacted in impact.impacted:
        risk = risk_by_key.get(impacted.path)
        candidates.append(
            _Candidate(
                test_key=impacted.path,
                risk_score=risk.risk_score if risk is not None else 0.0,
                impact_kind=strongest_impact_kind(impacted.kinds),
                changed_files=tuple(impacted.changed_files),
                requirement_risk=risk.requirement_risk if risk is not None else None,
                test_case_priority=(risk.test_case_priority if risk is not None else None),
                stats=risk.stats if risk is not None else _no_history(impacted.path),
            )
        )

    # Stable rank: highest risk first, then test_key ascending (reproducible).
    candidates.sort(key=lambda candidate: (-candidate.risk_score, candidate.test_key))

    recommendations = [
        RecommenderItem(
            test_key=candidate.test_key,
            stats=candidate.stats,
            rank=position + 1,
            risk_score=candidate.risk_score,
            impact_kind=candidate.impact_kind,
            changed_files=list(candidate.changed_files),
            requirement_risk=candidate.requirement_risk,
            test_case_priority=candidate.test_case_priority,
            rationale=_rationale(
                impact_kind=candidate.impact_kind,
                stats=candidate.stats,
                requirement_risk=candidate.requirement_risk,
                test_case_priority=candidate.test_case_priority,
                changed_files=candidate.changed_files,
            ),
        )
        for position, candidate in enumerate(candidates[:top_n])
    ]

    return RecommendationSet(
        project_id=ranking.project_id,
        changed=list(impact.changed),
        recommendations=recommendations,
        top_n=top_n,
        min_sample=ranking.min_sample,
        recent_window=ranking.recent_window,
        flaky_threshold=ranking.flaky_threshold,
        failing_threshold=ranking.failing_threshold,
        computed_at=datetime.now(UTC),
    )
