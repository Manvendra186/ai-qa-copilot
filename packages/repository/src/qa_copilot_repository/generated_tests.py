"""Generated-test review persistence (build bible §19 S2.4, §31.1).

The S2.3 Automation Agent output (``qa_copilot_ai.agents.automation``)
becomes one ``generated_tests`` row per test case — the reviewable artifact
of the S2.4 human-in-the-loop flow. The review endpoints then transition the
row's status through the domain state machine
(:func:`qa_copilot_domain.can_transition_generated_test`) and record the
reviewer + note.

Rows are *flushed, not committed* — the caller owns the transaction (same
convention as :mod:`qa_copilot_repository.requirements` / ``audit``).
Invalid status transitions raise :class:`ValueError` — the API maps that to
``409 Conflict`` (the domain stays the single source of truth for the
vocabulary, §10).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from qa_copilot_domain.enums import (
    ALLOWED_GENERATED_TEST_TRANSITIONS,
    GeneratedTestStatus,
    can_transition_generated_test,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "get_generated_test",
    "list_generated_tests",
    "persist_generated_test",
    "set_review_status",
]


def persist_generated_test(
    session: Session,
    *,
    project_id: str,
    job_id: str | None,
    test_case_id: str | None,
    file_path: str,
    file_path_pattern: str | None,
    language: str,
    framework: str,
    content: str,
    notes: Sequence[str],
    repository_path: str | None,
) -> models.GeneratedTest:
    """Create the pending ``generated_tests`` row for one agent output.

    Flushed, not committed — the caller commits in its own scope. The row
    starts ``pending``: the S2.4 rule that *every AI output needs human
    review before it ships* (§19 S2.4).
    """
    row = models.GeneratedTest(
        project_id=project_id,
        job_id=job_id,
        test_case_id=test_case_id,
        file_path=file_path,
        file_path_pattern=file_path_pattern,
        language=language,
        framework=framework,
        content=content,
        notes=list(notes),
        repository_path=repository_path,
        status=GeneratedTestStatus.PENDING,
    )
    session.add(row)
    session.flush()
    return row


def get_generated_test(session: Session, generated_test_id: str) -> models.GeneratedTest | None:
    """One generated-test row (the review endpoints' read path)."""
    return session.get(models.GeneratedTest, generated_test_id)


def list_generated_tests(session: Session, project_id: str) -> Sequence[models.GeneratedTest]:
    """All of a project's generated tests, newest first (the review queue)."""
    stmt = (
        select(models.GeneratedTest)
        .where(models.GeneratedTest.project_id == project_id)
        .order_by(models.GeneratedTest.created_at.desc(), models.GeneratedTest.id.desc())
    )
    return session.scalars(stmt).all()


def set_review_status(
    session: Session,
    row: models.GeneratedTest,
    *,
    target: GeneratedTestStatus,
    user_id: str,
    note: str | None = None,
) -> models.GeneratedTest:
    """Apply one human review transition (approve / apply / reject).

    Enforces the domain state machine (``pending → approved → applied``,
    ``pending|approved → rejected``; applied/rejected are terminal): an
    illegal or no-op transition raises :class:`ValueError` (→ ``409``).
    Sets the reviewer trail (``reviewed_by`` / ``reviewed_at`` /
    ``review_note``) on every transition. Flushed, not committed.
    """
    if row.status == target:
        raise ValueError(f"generated test is already {target.value}")
    if not can_transition_generated_test(row.status, target):
        allowed = ", ".join(sorted(s.value for s in ALLOWED_GENERATED_TEST_TRANSITIONS[row.status]))
        raise ValueError(
            f"invalid transition {row.status.value} -> {target.value} "
            f"(allowed from {row.status.value}: {allowed or 'none'})"
        )
    row.status = target
    row.reviewed_by = user_id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = note
    session.flush()
    return row
