"""Core domain entities (build bible §10, §12, §15, §16).

S0.4 scope: ``Project``, ``Requirement``, ``TestCase``, ``Failure``,
``Artifact``, ``Job``. S0.8 (auth baseline, §31.3): ``User`` and
``ProjectMember``. S3.3 (§19): ``NormalizedFailure`` — raw failure text
normalized onto the §16 taxonomy (deterministic, LLM-free).

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


class LocatorStyle(DomainModel):
    """One observed UI locator API and how often the repo's test code uses it
    (build bible §19 S2.2).

    ``api`` is the locator method (``"getByRole"``, ``"getByTestId"``,
    ``"locator"``, …). ``framework`` attributes the usage: ``"playwright"`` or
    ``"testing-library"`` when the file imports that toolkit, otherwise
    ``"generic"``. ``count`` is occurrences in the test files.
    """

    api: NonBlankStr
    framework: str
    count: int = Field(default=0, ge=0)


class TestScript(DomainModel):
    """A ``package.json`` script that launches tests (build bible §19 S2.2)."""

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    name: NonBlankStr
    command: NonBlankStr


class TestConventions(DomainModel):
    """Extracted test conventions of a target repository (build bible §19 S2.2).

    Produced by :func:`qa_copilot_repository.conventions.extract_conventions` —
    deterministic, LLM-free, on top of the S2.1 scanner. This is the shared
    contract the S2.3 automation agent consumes to generate code that matches
    how the repo already tests.

    File paths are repo-relative POSIX, de-duplicated and sorted.
    ``locator_styles`` is ordered by usage (most-used first, then name);
    ``test_scripts`` by name. ``scanned_at`` is the only time-varying field.
    """

    # Prevents pytest from trying to collect this non-test class (name is Test*).
    __test__ = False

    test_file_patterns: list[str] = Field(default_factory=list)
    locator_styles: list[LocatorStyle] = Field(default_factory=list)
    page_object_files: list[str] = Field(default_factory=list)
    fixture_files: list[str] = Field(default_factory=list)
    helper_files: list[str] = Field(default_factory=list)
    test_configs: list[str] = Field(default_factory=list)
    test_ids: list[str] = Field(default_factory=list)
    base_url: str | None = None
    test_scripts: list[TestScript] = Field(default_factory=list)
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


class NormalizedFailure(DomainModel):
    """Structured taxonomy view of one raw failure (build bible §15, §16; §19 S3.3).

    Produced by :func:`qa_copilot_execution.failure.normalize_failure` —
    deterministic and LLM-free: the raw failure text (the shape of
    ``TestResultReport.error``) becomes consistent structured fields, so the
    S4.1 Failure Investigator (AI) reasons over a normalized shape, not raw
    logs (§16 v1.1: text-first).

    ``category`` is the normalizer's *best guess* (``unknown`` when no §16
    signal matches); the Investigator's final diagnosis (see :class:`Failure`)
    may override it. ``category_signals`` are the matched rule names (the
    deterministic "why", most decisive first); ``evidence`` are the raw lines
    that backed them (leading lines when the category is ``unknown``).
    ``http_status`` / ``selector`` / ``endpoint`` are the structural facts
    found in the text (``None`` when absent).
    """

    category: FailureCategory = FailureCategory.UNKNOWN
    category_signals: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    http_status: int | None = Field(default=None, ge=100, le=599)
    selector: str | None = None
    endpoint: str | None = None


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
