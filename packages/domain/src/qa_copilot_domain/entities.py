"""Core domain entities (build bible §10, §12, §15, §16).

S0.4 scope: ``Project``, ``Requirement``, ``TestCase``, ``Failure``,
``Artifact``, ``Job``. S0.8 (auth baseline, §31.3): ``User`` and
``ProjectMember``.

Conventions:

- ``id`` is server-assigned and ``None`` until persisted; cross-entity links
  are stored as ID strings. The ``requirement_test_cases`` many-to-many join
  (build bible §10) is owned by the persistence layer (S0.5); ``TestCase``
  carries the same relationship as ``requirement_refs``.
- ``confidence``/``progress`` are bounded 0.0–1.0 fractions.
- Timestamps are optional UTC datetimes, assigned by the server.
"""

from datetime import datetime
from typing import Annotated, Any

from pydantic import Field, StringConstraints

from .base import DomainModel
from .enums import (
    ArtifactType,
    FailureCategory,
    JobStatus,
    JobType,
    Priority,
    ProjectRole,
    RiskLevel,
    TestType,
)

#: A required text value: whitespace-stripped, at least one character.
NonBlankStr = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


class User(DomainModel):
    """A user of the platform (build bible §10 ``users``; auth at §31.3).

    ``role`` is the user's *default* role; authorization is decided by the
    project-scoped :class:`ProjectMember` role, which wins (§31.3).
    """

    id: str | None = None
    email: NonBlankStr
    role: ProjectRole = ProjectRole.OWNER
    created_at: datetime | None = None


class ProjectMember(DomainModel):
    """A user's role within one project (build bible §31.3 project-scoped RBAC).

    Composite key ``(project_id, user_id)`` — the same shape as the
    ``requirement_test_cases`` join (build bible §10 v1.1).
    """

    project_id: NonBlankStr
    user_id: NonBlankStr
    role: ProjectRole
    created_at: datetime | None = None


class Project(DomainModel):
    """A project under QA (build bible §10 ``projects``)."""

    id: str | None = None
    organization_id: str | None = None
    name: NonBlankStr
    repository_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class RepositoryProfile(DomainModel):
    """Structured facts about a scanned target repository (build bible §7, §19 S2.1).

    Produced by :func:`qa_copilot_repository.scanner.scan_repository` — a
    deterministic, LLM-free scan. ``languages`` is ordered by prevalence
    (most files first); the other list fields are sorted for stable diffing.
    Wire strings are lowercase (``"python"``, ``"fastapi"``, ``"pytest"``);
    ``test_dirs`` are repo-relative POSIX paths.
    """

    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    test_dirs: list[str] = Field(default_factory=list)
    test_file_count: int = Field(default=0, ge=0)
    package_managers: list[str] = Field(default_factory=list)
    monorepo: bool = False
    file_count: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    scanned_at: datetime | None = None


class Requirement(DomainModel):
    """A product requirement with acceptance criteria (build bible §10)."""

    id: str | None = None
    project_id: str | None = None
    title: NonBlankStr
    content: NonBlankStr
    acceptance_criteria: list[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.MEDIUM
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TestCase(DomainModel):
    """A structured test case (build bible §10 ``test_cases``, §12 output)."""

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    id: str | None = None
    title: NonBlankStr
    type: TestType
    priority: Priority = Priority.MEDIUM
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    expected_results: list[str] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.MEDIUM
    requirement_refs: list[str] = Field(default_factory=list)


class Failure(DomainModel):
    """A failure plus its AI diagnosis (build bible §10, §12, §16).

    ``category`` defaults to ``unknown`` and ``confidence`` to ``None`` so a
    row can be persisted before analysis; the Failure Investigator fills both
    in (its output shape is the §12 example).
    """

    id: str | None = None
    test_result_id: str | None = None
    category: FailureCategory = FailureCategory.UNKNOWN
    root_cause: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    suggested_fix: str | None = None
    needs_human_approval: bool = True


class Artifact(DomainModel):
    """An execution artifact reference (build bible §10 ``artifacts``, §15)."""

    id: str | None = None
    test_result_id: str | None = None
    type: ArtifactType
    uri: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class Job(DomainModel):
    """An async AI-backed job (build bible §10 ``jobs``, §11 202+SSE contract)."""

    id: str | None = None
    project_id: str | None = None
    type: JobType
    status: JobStatus = JobStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    input_ref: str | None = None
    output_ref: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
