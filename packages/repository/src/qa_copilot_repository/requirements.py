"""Requirement + test-case persistence (build bible §10, §12; §19 S1.3).

The S1.2 Test Design Agent is **pure** — it returns a validated
:class:`~qa_copilot_ai.TestSuite` and has no DB access (build bible §19:
"The agent is pure: no DB, no API, no side effects"). This module is the
single DB entry point that turns that suite into the §10 rows:

- one ``requirements`` row (from the job's inline requirement),
- one ``test_cases`` row per case in the suite,
- the ``requirement_test_cases`` M:N join linking them.

Rows are *flushed, not committed* — the caller owns the transaction (same
convention as :mod:`qa_copilot_repository.audit`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from qa_copilot_ai import TestSuite
from qa_copilot_domain.enums import Priority, RiskLevel, TestType
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models

__all__ = ["PersistedSuite", "list_requirements", "persist_requirement_with_suite"]


@dataclass(frozen=True, slots=True)
class PersistedSuite:
    """What :func:`persist_requirement_with_suite` created (flushed, not committed).

    ``requirement_id`` is the new ``requirements`` row; ``test_case_ids`` are
    the ``test_cases`` rows linked to it via the §10 M:N join.
    """

    requirement_id: str
    test_case_ids: tuple[str, ...]


def persist_requirement_with_suite(
    session: Session,
    *,
    project_id: str,
    title: str,
    content: str,
    acceptance_criteria: Sequence[str],
    suite: TestSuite,
) -> PersistedSuite:
    """Persist a requirement and its designed test cases as §10 rows.

    Creates one ``requirements`` row, one ``test_cases`` row per suite case,
    and the ``requirement_test_cases`` join rows. Flushed, not committed —
    the caller commits in its own scope.

    The suite's ``TC-###`` ids are suite-local (presentation); each DB row
    gets its own uuid primary key.
    """
    requirement = models.Requirement(
        project_id=project_id,
        title=title,
        content=content,
        acceptance_criteria=list(acceptance_criteria),
    )
    session.add(requirement)

    rows: list[models.TestCase] = []
    for case in suite.test_cases:
        row = models.TestCase(
            title=case.title,
            type=TestType(case.type),
            priority=Priority(case.priority),
            preconditions=list(case.preconditions),
            steps=list(case.steps),
            expected_results=list(case.expected_results),
            risk=RiskLevel(case.risk),
        )
        session.add(row)
        requirement.test_cases.append(row)  # §10 M:N join
        rows.append(row)

    session.flush()  # assign ids + insert the requirement_test_cases join rows
    return PersistedSuite(
        requirement_id=requirement.id,
        test_case_ids=tuple(row.id for row in rows),
    )


def list_requirements(
    session: Session, project_id: str
) -> Sequence[tuple[models.Requirement, int]]:
    """A project's requirements, newest first, each with its test-case count.

    The "past requirements" history list (``GET /api/v1/projects/{id}/requirements``)
    — summary rows for the web shell; the full suite of one row still comes
    from ``GET /api/v1/requirements/{id}`` (S1.3 read-back). Read-only.
    """
    case_count = (
        select(func.count(models.TestCase.id))
        .select_from(models.RequirementTestCase)
        .join(
            models.TestCase,
            models.TestCase.id == models.RequirementTestCase.test_case_id,
        )
        .where(models.RequirementTestCase.requirement_id == models.Requirement.id)
        .correlate(models.Requirement)
        .scalar_subquery()
    )
    stmt = (
        select(models.Requirement, case_count)
        .where(models.Requirement.project_id == project_id)
        .order_by(models.Requirement.created_at.desc(), models.Requirement.id.desc())
    )
    return session.execute(stmt).all()
