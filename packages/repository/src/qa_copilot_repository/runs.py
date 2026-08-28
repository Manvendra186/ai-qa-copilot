"""Execution-run persistence (build bible §10, §15; S3.1).

Maps the execution worker's :class:`qa_copilot_execution.RunReport` onto the
§10 ``test_runs`` / ``test_results`` / ``artifacts`` rows:

- one ``test_runs`` row (status vocabulary from the domain package, §15);
- one ``test_results`` row per test outcome (``duration`` in *seconds* —
  the report carries milliseconds);
- one ``artifacts`` row per captured artifact — URI only, never file
  contents (§15: "keep artifact storage separate from relational metadata").

The report's richer fields (totals, error tails, browser) are the worker's
contract, not DB schema — the §10 tables are the source of truth for what
is persisted (see the S3.1 models). Rows are *flushed, not committed* —
the caller owns the transaction (same convention as
:mod:`qa_copilot_repository.generated_tests`).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from qa_copilot_execution.report import RunReport
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "get_run",
    "list_artifacts",
    "list_results",
    "list_runs",
    "persist_run",
]


def _parse_ts(value: str) -> datetime:
    """ISO-8601 (report) → tz-aware datetime (the report always emits UTC)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def persist_run(
    session: Session,
    *,
    project_id: str,
    report: RunReport,
) -> models.TestRun:
    """Persist one worker report: run row + result rows + artifact rows.

    Flushed, not committed — the caller commits in its own scope. The run
    keeps the worker's terminal status (``completed`` even when tests
    failed — outcomes are per-test data; ``failed`` means the worker
    itself could not produce a report).
    """
    run = models.TestRun(
        project_id=project_id,
        status=report.status,
        commit_sha=report.commit_sha,
        started_at=_parse_ts(report.started_at),
        completed_at=_parse_ts(report.completed_at),
    )
    session.add(run)
    session.flush()

    for result in report.results:
        row = models.TestResult(
            run_id=run.id,
            status=result.status,
            duration=result.duration_ms / 1000.0,
        )
        session.add(row)
        session.flush()
        for artifact in result.artifacts:
            session.add(
                models.Artifact(
                    test_result_id=row.id,
                    type=artifact.type,
                    uri=artifact.uri,
                    metadata_=dict(artifact.metadata),
                )
            )
    session.flush()
    return run


def get_run(session: Session, run_id: str) -> models.TestRun | None:
    """One run row (the S3.2 ``GET /runs/{id}`` read path)."""
    return session.get(models.TestRun, run_id)


def list_runs(session: Session, project_id: str) -> Sequence[models.TestRun]:
    """A project's runs, newest first (the S3.2 run-history UI)."""
    stmt = (
        select(models.TestRun)
        .where(models.TestRun.project_id == project_id)
        .order_by(models.TestRun.created_at.desc(), models.TestRun.id.desc())
    )
    return session.scalars(stmt).all()


def list_results(session: Session, run_id: str) -> Sequence[models.TestResult]:
    """All test outcomes of one run."""
    stmt = (
        select(models.TestResult)
        .where(models.TestResult.run_id == run_id)
        .order_by(models.TestResult.id)
    )
    return session.scalars(stmt).all()


def list_artifacts(session: Session, run_id: str) -> Sequence[models.Artifact]:
    """All artifact rows of one run (across its results).

    The S3.2 ``GET /runs/{id}/artifacts`` read path — each row's ``uri``
    resolves against the artifact store root (``ArtifactStore.resolve``).
    """
    stmt = (
        select(models.Artifact)
        .join(models.TestResult, models.Artifact.test_result_id == models.TestResult.id)
        .where(models.TestResult.run_id == run_id)
        .order_by(models.Artifact.id)
    )
    return session.scalars(stmt).all()
