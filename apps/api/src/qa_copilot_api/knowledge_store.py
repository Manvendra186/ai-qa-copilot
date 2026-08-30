"""Project knowledge corpus assembly + persistence (S5.3, build bible §7, §14).

The API package is the composition layer for knowledge: it depends on both
``qa_copilot_knowledge`` (document / chunk / search core + source adapters) and
``qa_copilot_repository`` (the ``knowledge_documents`` table). The corpus
assembly lives here rather than in the repository package because
``knowledge`` already depends on ``repository`` — a repository→knowledge edge
would be a circular package dependency.

The ``knowledge_documents`` table stores ``title`` inside ``metadata`` (the
table has no ``title`` column); :func:`_to_row` / :func:`_from_row` keep that
round-trip lossless.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from qa_copilot_domain import Priority, Requirement, RiskLevel, TestCase, TestType
from qa_copilot_knowledge import (
    KnowledgeDocument,
    KnowledgeIndex,
    KnowledgeSourceType,
    RunRecord,
    SearchResult,
    TestOutcomeRecord,
    history_documents,
    repository_file_documents,
    requirement_documents,
)
from qa_copilot_repository import models
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

__all__ = [
    "build_project_knowledge",
    "knowledge_status",
    "list_project_knowledge_documents",
    "persist_project_knowledge",
    "search_project_knowledge",
]


# --- corpus assembly (§7, §14) -------------------------------------------------


def build_project_knowledge(
    session: Session,
    project_id: str,
    repository_path: str | None,
) -> tuple[Sequence[KnowledgeDocument], bool]:
    """Assemble a project's knowledge corpus (S5.3, §7, §14).

    Document sources (each a project-specific document with source metadata):

    1. repository files — when *repository_path* is an existing directory;
    2. the project's persisted requirements + designed test cases (§10, §12);
    3. the project's run + failure history (§10, §16).

    Returns ``(documents, capped)`` where *capped* reflects the repository
    walk's file cap (``repository_file_documents``); ``False`` when there is no
    repository to walk.
    """
    docs: list[KnowledgeDocument] = []
    capped = False

    if repository_path is not None and repository_path.strip():
        root = Path(repository_path).expanduser()
        if not root.is_dir():
            raise ValueError(f"repository_path is not an existing directory: {repository_path!r}")
        repo_docs, capped = repository_file_documents(root)
        docs.extend(repo_docs)

    requirements = [_requirement(r) for r in _project_requirements(session, project_id)]
    test_cases = [_test_case(t) for t in _project_test_cases(session, project_id)]
    docs.extend(requirement_documents(requirements, test_cases))

    runs = [_run_record(session, run) for run in _project_runs(session, project_id)]
    docs.extend(history_documents(runs))

    return docs, capped


def persist_project_knowledge(
    session: Session,
    project_id: str,
    documents: Sequence[KnowledgeDocument],
) -> int:
    """Replace a project's stored knowledge documents (S5.3 index/re-index).

    Delete-then-insert in one transaction (idempotent, stable per-document ids
    from :func:`_stable_document_id`), so re-indexing is a clean refresh.
    Returns the number of documents stored. Flushed, not committed — the
    caller owns the transaction (repository-package convention).
    """
    now = datetime.now(UTC)
    session.execute(
        delete(models.KnowledgeDocument).where(models.KnowledgeDocument.project_id == project_id)
    )
    for doc in documents:
        session.add(_to_row(project_id, doc, now))
    session.flush()
    return len(documents)


# --- read paths (status / list / search) ---------------------------------------


class KnowledgeStatusDict(TypedDict):
    """The ``GET /projects/{id}/knowledge/status`` payload (S5.3, §7).

    Typed (rather than ``dict[str, object]``) so callers — the route and the
    tests — can index it and pass it straight to :class:`KnowledgeStatus`
    without unpacking untyped values.
    """

    document_count: int
    by_source_type: dict[str, int]
    source_types: list[str]
    last_indexed_at: datetime | None


def knowledge_status(session: Session, project_id: str) -> KnowledgeStatusDict:
    """Per-source document counts + last index time for a project (S5.3)."""
    rows = session.execute(
        select(
            models.KnowledgeDocument.source_type,
            func.count(models.KnowledgeDocument.id),
        )
        .where(models.KnowledgeDocument.project_id == project_id)
        .group_by(models.KnowledgeDocument.source_type)
    ).all()
    # Explicit row unpacking — ``Row`` is not a plain tuple, so ``dict(rows)``
    # is neither mypy-safe nor guaranteed by SQLAlchemy's contract.
    by_type: dict[str, int] = {}
    for row in rows:
        by_type[row[0]] = row[1]
    last_indexed = session.scalar(
        select(func.max(models.KnowledgeDocument.created_at)).where(
            models.KnowledgeDocument.project_id == project_id
        )
    )
    return KnowledgeStatusDict(
        document_count=sum(by_type.values()),
        by_source_type=by_type,
        source_types=sorted(by_type),
        last_indexed_at=last_indexed,
    )


def list_project_knowledge_documents(
    session: Session,
    project_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> Sequence[models.KnowledgeDocument]:
    """A project's stored knowledge documents, newest first (S5.3 list)."""
    return session.scalars(
        select(models.KnowledgeDocument)
        .where(models.KnowledgeDocument.project_id == project_id)
        .order_by(
            models.KnowledgeDocument.created_at.desc(),
            models.KnowledgeDocument.id.desc(),
        )
        .limit(max(1, limit))
        .offset(max(0, offset))
    ).all()


def search_project_knowledge(
    session: Session,
    project_id: str,
    query: str,
    *,
    top_k: int = 5,
) -> SearchResult:
    """Rank the project's stored knowledge documents for *query* (S5.3, §14).

    Loads the project's documents from the table, builds a
    :class:`~qa_copilot_knowledge.KnowledgeIndex`, and runs the §14 search
    (top-k ≤ 5, hard-truncated).
    """
    index = KnowledgeIndex(_load_project_documents(session, project_id))
    return index.search(query, top_k=top_k)


# --- ORM <-> domain mapping -----------------------------------------------------


def _load_project_documents(session: Session, project_id: str) -> list[KnowledgeDocument]:
    """A project's stored documents as domain :class:`KnowledgeDocument`s."""
    rows = session.scalars(
        select(models.KnowledgeDocument)
        .where(models.KnowledgeDocument.project_id == project_id)
        .order_by(models.KnowledgeDocument.id)
    )
    return [_from_row(row) for row in rows]


def _to_row(project_id: str, doc: KnowledgeDocument, now: datetime) -> models.KnowledgeDocument:
    """A corpus document → a ``knowledge_documents`` row (S5.3).

    ``title`` is preserved in ``metadata`` (the table has no ``title`` column)
    so :func:`_from_row` restores it; the stable id from
    :func:`_stable_document_id` is the primary key.
    """
    return models.KnowledgeDocument(
        id=_stable_document_id(project_id, doc),
        project_id=project_id,
        source_type=doc.source_type.value,
        source_ref=doc.source_ref,
        content=doc.content,
        metadata_={**dict(doc.metadata), "title": doc.title},
        created_at=now,
    )


def _from_row(row: models.KnowledgeDocument) -> KnowledgeDocument:
    """A ``knowledge_documents`` row → a domain :class:`KnowledgeDocument`."""
    metadata = dict(row.metadata_ or {})
    title = metadata.get("title") or row.source_ref or str(row.id)
    metadata.pop("title", None)
    return KnowledgeDocument(
        id=str(row.id),
        source_type=KnowledgeSourceType(row.source_type),
        title=str(title),
        source_ref=row.source_ref or str(row.id),
        content=row.content,
        metadata=metadata,
        created_at=row.created_at,
    )


def _stable_document_id(project_id: str, doc: KnowledgeDocument) -> str:
    """A stable, unique primary key for a corpus document (S5.3 idempotency).

    Derived from the (unique-per-corpus) ``(source_type, source_ref)`` pair, so
    re-indexing the same inputs produces the same ids and delete+insert is a
    clean refresh. Capped to the column width (512).
    """
    key = f"knowledge-doc:{project_id}:{doc.source_type.value}:{doc.source_ref}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


# --- domain reconstruction from §10 rows (§10, §12, §16) ------------------------


def _project_requirements(session: Session, project_id: str) -> Sequence[models.Requirement]:
    return session.scalars(
        select(models.Requirement)
        .where(models.Requirement.project_id == project_id)
        .order_by(models.Requirement.created_at, models.Requirement.id)
    ).all()


def _project_test_cases(session: Session, project_id: str) -> Sequence[models.TestCase]:
    return (
        session.scalars(
            select(models.TestCase)
            .join(
                models.RequirementTestCase,
                models.RequirementTestCase.test_case_id == models.TestCase.id,
            )
            .join(
                models.Requirement,
                models.Requirement.id == models.RequirementTestCase.requirement_id,
            )
            .where(models.Requirement.project_id == project_id)
            .order_by(models.TestCase.id)
        )
        .unique()
        .all()
    )


def _project_runs(session: Session, project_id: str) -> Sequence[models.TestRun]:
    return session.scalars(
        select(models.TestRun)
        .where(models.TestRun.project_id == project_id)
        .order_by(models.TestRun.created_at, models.TestRun.id)
    ).all()


def _requirement(row: models.Requirement) -> Requirement:
    return Requirement(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        content=row.content,
        acceptance_criteria=list(row.acceptance_criteria or []),
        risk=_requirement_risk(row.risk),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _test_case(row: models.TestCase) -> TestCase:
    return TestCase(
        id=row.id,
        title=row.title,
        type=TestType(row.type),
        priority=Priority(row.priority),
        preconditions=list(row.preconditions or []),
        steps=list(row.steps or []),
        expected_results=list(row.expected_results or []),
        risk=RiskLevel(row.risk),
    )


def _requirement_risk(value: object) -> RiskLevel:
    """ORM risk → :class:`RiskLevel`, defensively mapping ``none`` → ``low``.

    §10 forbids risk ``none`` on a requirement; a stored ``none`` would fail
    :class:`Requirement` validation, so it maps to the closest allowed level.
    """
    if value is None or str(value).strip().lower() in {"none", ""}:
        return RiskLevel.LOW
    return RiskLevel(str(value))


def _run_record(session: Session, run: models.TestRun) -> RunRecord:
    results: list[TestOutcomeRecord] = []
    for tr in session.scalars(select(models.TestResult).where(models.TestResult.run_id == run.id)):
        failure = session.scalar(
            select(models.Failure).where(models.Failure.test_result_id == tr.id)
        )
        record = TestOutcomeRecord(
            # The §10 test_results table stores no test name (S3.1 maps only
            # status/duration/failure) — identify the outcome by its row id,
            # matching the S3.2 runs API, which likewise exposes no name.
            test=f"result-{tr.id}",
            status=tr.status.value,
            failure_category=str(failure.category) if failure else None,
            failure_root_cause=failure.root_cause if failure else None,
        )
        if failure is not None:
            record.failure_evidence = [str(line) for line in (failure.evidence or [])]
        results.append(record)
    return RunRecord(
        run_id=run.id,
        status=run.status.value,
        commit_sha=run.commit_sha,
        started_at=run.started_at,
        results=results,
    )
