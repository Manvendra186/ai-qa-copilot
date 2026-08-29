"""SQLAlchemy 2.0 ORM models for the §10 core data model (S0.5).

Conventions (build bible §10, §12, §16; STATE.md S0.5 notes):

- One ORM class per §10 table. Column names mirror the S0.4 domain entity
  field names (``qa_copilot_domain``) so ``model_validate(orm_obj)``
  (``from_attributes=True``) works without converters.
- Enum columns store the domain enum's **wire string**: plain ``VARCHAR``,
  no PostgreSQL enum type and no CHECK copy of the vocabulary — the domain
  package stays the single source of truth for vocabularies.
- IDs are native PostgreSQL ``UUID`` columns, exposed as ``str`` to match
  the domain entities (``id: str | None``).
- ``artifacts.metadata`` / ``knowledge_documents.metadata``: ``metadata``
  is reserved by the declarative API, so the Python attribute is
  ``metadata_`` while the database column stays ``metadata``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import pgvector.sqlalchemy
import sqlalchemy as sa
from qa_copilot_domain.enums import (
    ArtifactType,
    FailureCategory,
    GeneratedTestStatus,
    JobStatus,
    JobType,
    Priority,
    ProjectRole,
    RiskLevel,
    RunStatus,
    TestResultStatus,
    TestType,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: Dimension of ``embeddings.vector``. Assumed until the embedding model is
#: chosen (S0.6 / Phase 5); changing it requires a new migration.
VECTOR_DIM = 1536


def _new_id() -> str:
    """Python-side PK default: UUID4 as a string (matches domain ``id: str``)."""
    return str(uuid.uuid4())


def _enum_column(enum_cls: type[StrEnum]) -> sa.Enum:
    """Enum column storing the domain enum's wire string.

    ``native_enum=False`` → ``VARCHAR`` DDL; ``create_constraint=False`` →
    no CHECK copy of the vocabulary (single source: ``qa_copilot_domain``).
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=32,
        values_callable=lambda enum: [member.value for member in enum],
    )


class Base(DeclarativeBase):
    """Declarative base — ``Base.metadata`` drives Alembic autogenerate."""


class Organization(Base):
    """Tenant container (build bible §10 ``organizations``)."""

    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(sa.String(255))
    plan: Mapped[str] = mapped_column(sa.String(32), default="dev")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    projects: Mapped[list[Project]] = relationship(back_populates="organization")


class User(Base):
    """A user of the platform (build bible §10 ``users``; roles at S0.8, §31.3).

    ``role`` is the user's default role; authorization is decided by the
    project-scoped ``project_members`` row (``ProjectMember``), which wins.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(sa.String(320), unique=True)
    role: Mapped[str] = mapped_column(sa.String(32), default="owner")
    # S0.8: PBKDF2-SHA256 hash from ``qa_copilot_api.auth.hash_password``
    # (``pbkdf2_sha256$<iter>$<salt>$<digest>``). Nullable so pre-S0.8 rows
    # keep working; login requires a non-null hash.
    password_hash: Mapped[str | None] = mapped_column(sa.String(255))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    memberships: Mapped[list[ProjectMember]] = relationship(back_populates="user")


class Repository(Base):
    """A target code repository under QA (build bible §10 ``repositories``)."""

    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    provider: Mapped[str] = mapped_column(sa.String(64), default="github")
    url: Mapped[str | None] = mapped_column(sa.String(2048))
    default_branch: Mapped[str | None] = mapped_column(sa.String(255))
    scan_status: Mapped[str] = mapped_column(sa.String(32), default="pending")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    projects: Mapped[list[Project]] = relationship(back_populates="repository")
    files: Mapped[list[File]] = relationship(back_populates="repository")


class Project(Base):
    """A project under QA (build bible §10 ``projects``; domain ``Project``).

    ``repository_id`` links to the target repository (domain naming kept —
    build bible §10 says ``repo_id``; decision logged in STATE.md S0.4).
    """

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    organization_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False), sa.ForeignKey("organizations.id")
    )
    name: Mapped[str] = mapped_column(sa.String(255))
    repository_id: Mapped[str | None] = mapped_column(
        sa.Uuid(as_uuid=False), sa.ForeignKey("repositories.id")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    organization: Mapped[Organization] = relationship(back_populates="projects")
    repository: Mapped[Repository | None] = relationship(back_populates="projects")
    requirements: Mapped[list[Requirement]] = relationship(back_populates="project")
    jobs: Mapped[list[Job]] = relationship(back_populates="project")
    knowledge_documents: Mapped[list[KnowledgeDocument]] = relationship(back_populates="project")
    ai_sessions: Mapped[list[AISession]] = relationship(back_populates="project")
    members: Mapped[list[ProjectMember]] = relationship(back_populates="project")
    generated_tests: Mapped[list[GeneratedTest]] = relationship(back_populates="project")


class ProjectMember(Base):
    """A user's role within one project (build bible §31.3, S0.8 auth baseline).

    Composite primary key ``(project_id, user_id)``; the extra index on
    ``user_id`` covers the reverse lookup "which projects can this user see".
    ``role`` stores the :class:`~qa_copilot_domain.enums.ProjectRole` wire
    string (``owner`` / ``member`` / ``viewer``).
    """

    __tablename__ = "project_members"

    project_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    role: Mapped[ProjectRole] = mapped_column(_enum_column(ProjectRole))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class File(Base):
    """An indexed file in a target repository (build bible §10 ``files``, §13)."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    repository_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("repositories.id", ondelete="CASCADE"),
    )
    path: Mapped[str] = mapped_column(sa.Text)
    hash: Mapped[str] = mapped_column(sa.String(64))
    language: Mapped[str | None] = mapped_column(sa.String(64))
    indexed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    repository: Mapped[Repository] = relationship(back_populates="files")


class RequirementTestCase(Base):
    """Many-to-many join requirement ↔ test case (build bible §10 v1.1).

    Composite primary key (leading index on ``requirement_id``); the extra
    index covers the reverse lookup "which requirements does this test
    case cover".
    """

    __tablename__ = "requirement_test_cases"

    requirement_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("requirements.id", ondelete="CASCADE"),
        primary_key=True,
    )
    test_case_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class Requirement(Base):
    """A product requirement with acceptance criteria (build bible §10, §12)."""

    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    title: Mapped[str] = mapped_column(sa.String(512))
    content: Mapped[str] = mapped_column(sa.Text)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, default=list)
    risk: Mapped[RiskLevel] = mapped_column(_enum_column(RiskLevel), default=RiskLevel.MEDIUM)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )

    project: Mapped[Project] = relationship(back_populates="requirements")
    test_cases: Mapped[list[TestCase]] = relationship(
        secondary=RequirementTestCase.__table__, back_populates="requirements"
    )


class TestCase(Base):
    """A structured test case (build bible §10 ``test_cases``, §12)."""

    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    title: Mapped[str] = mapped_column(sa.String(512))
    type: Mapped[TestType] = mapped_column(_enum_column(TestType))
    priority: Mapped[Priority] = mapped_column(_enum_column(Priority), default=Priority.MEDIUM)
    preconditions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    steps: Mapped[list[str]] = mapped_column(JSONB, default=list)
    expected_results: Mapped[list[str]] = mapped_column(JSONB, default=list)
    risk: Mapped[RiskLevel] = mapped_column(_enum_column(RiskLevel), default=RiskLevel.MEDIUM)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    requirements: Mapped[list[Requirement]] = relationship(
        secondary=RequirementTestCase.__table__, back_populates="test_cases"
    )


class GeneratedTest(Base):
    """A generated test file awaiting human review (build bible §19 S2.4).

    The S2.3 Automation Agent produces one of these per approved test case;
    the S2.4 review flow transitions ``status`` (the domain
    :class:`~qa_copilot_domain.enums.GeneratedTestStatus` state machine:
    ``pending → approved → applied`` / ``pending|approved → rejected``) and
    records who reviewed it and why. ``apply`` writes ``content`` to
    ``<repository_path>/<file_path>`` (API side effect, not a DB column).
    ``applied`` and ``rejected`` are terminal — re-generating creates a new
    row (§19 S2.4: every AI output needs human review before it ships).
    """

    __tablename__ = "generated_tests"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    # The automation job that produced this test (audit trail, §31.1).
    job_id: Mapped[str | None] = mapped_column(
        sa.Uuid(as_uuid=False), sa.ForeignKey("jobs.id", ondelete="SET NULL")
    )
    # The approved test case the test automates (the S1.2/S2.3 handoff).
    test_case_id: Mapped[str | None] = mapped_column(
        sa.Uuid(as_uuid=False), sa.ForeignKey("test_cases.id", ondelete="SET NULL")
    )
    file_path: Mapped[str] = mapped_column(sa.String(512))
    file_path_pattern: Mapped[str | None] = mapped_column(sa.String(512))
    language: Mapped[str] = mapped_column(sa.String(32))
    framework: Mapped[str] = mapped_column(sa.String(64))
    content: Mapped[str] = mapped_column(sa.Text)
    notes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # The target repository checkout apply() writes into (server-local path).
    repository_path: Mapped[str | None] = mapped_column(sa.String(2048))
    status: Mapped[GeneratedTestStatus] = mapped_column(
        _enum_column(GeneratedTestStatus), default=GeneratedTestStatus.PENDING
    )
    # Reviewer trail (§31.1 approval persistence; the ai_actions row carries
    # the audit detail, these columns are the row-level human decision).
    reviewed_by: Mapped[str | None] = mapped_column(
        sa.Uuid(as_uuid=False), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()
    )

    project: Mapped[Project] = relationship(back_populates="generated_tests")
    job: Mapped[Job | None] = relationship()
    test_case: Mapped[TestCase | None] = relationship()


class TestRun(Base):
    """A Playwright execution (build bible §10 ``test_runs``, §15)."""

    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    commit_sha: Mapped[str | None] = mapped_column(sa.String(64))
    # Status vocabulary from the domain package (S3.1, §15); values follow
    # the §31.2 job state machine (pending/running/completed/failed).
    status: Mapped[RunStatus] = mapped_column(_enum_column(RunStatus), default=RunStatus.RUNNING)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    results: Mapped[list[TestResult]] = relationship(back_populates="run")


class TestResult(Base):
    """Outcome of one test case in a run (build bible §10 ``test_results``).

    ``failure_id`` mirrors ``failures.test_result_id`` (1:1). The canonical
    foreign key lives on ``failures`` (matching the domain ``Failure``
    entity); this column is a plain UUID so queries can walk either way.
    """

    __tablename__ = "test_results"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    run_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
        index=True,
    )
    test_case_id: Mapped[str | None] = mapped_column(sa.Uuid(as_uuid=False), index=True)
    status: Mapped[TestResultStatus] = mapped_column(
        _enum_column(TestResultStatus), default=TestResultStatus.PENDING
    )
    duration: Mapped[float | None] = mapped_column(sa.Double)
    failure_id: Mapped[str | None] = mapped_column(sa.Uuid(as_uuid=False))

    run: Mapped[TestRun] = relationship(back_populates="results")
    failure: Mapped[Failure | None] = relationship(back_populates="test_result")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="test_result")


class Failure(Base):
    """A failure plus its AI diagnosis (build bible §10, §12, §16)."""

    __tablename__ = "failures"
    # Named so Alembic autogenerate matches the DB constraint by name
    # (anonymous constraints are misdetected as "removed" on the next
    # revision — see the S0.8 migration).
    __table_args__ = (
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="failures_confidence_check",
        ),
    )

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    test_result_id: Mapped[str | None] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("test_results.id", ondelete="CASCADE"),
        unique=True,
    )
    category: Mapped[FailureCategory] = mapped_column(
        _enum_column(FailureCategory), default=FailureCategory.UNKNOWN
    )
    root_cause: Mapped[str | None] = mapped_column(sa.Text)
    confidence: Mapped[float | None] = mapped_column(sa.Double)
    evidence: Mapped[list[str]] = mapped_column(JSONB, default=list)
    suggested_fix: Mapped[str | None] = mapped_column(sa.Text)
    needs_human_approval: Mapped[bool] = mapped_column(sa.Boolean, default=True)

    test_result: Mapped[TestResult | None] = relationship(back_populates="failure")


class Artifact(Base):
    """An execution artifact reference (build bible §10 ``artifacts``, §15).

    The database column is ``metadata`` (build bible §10); the attribute is
    ``metadata_`` because ``metadata`` is reserved by the declarative API.
    """

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    test_result_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("test_results.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[ArtifactType] = mapped_column(_enum_column(ArtifactType))
    uri: Mapped[str] = mapped_column(sa.String(2048))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    test_result: Mapped[TestResult] = relationship(back_populates="artifacts")


class KnowledgeDocument(Base):
    """A project knowledge document (build bible §10, §14).

    The database column is ``metadata`` (build bible §10); the attribute is
    ``metadata_`` because ``metadata`` is reserved by the declarative API.
    """

    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    source_type: Mapped[str] = mapped_column(sa.String(64))
    source_ref: Mapped[str | None] = mapped_column(sa.String(1024))
    content: Mapped[str] = mapped_column(sa.Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    project: Mapped[Project] = relationship(back_populates="knowledge_documents")
    embeddings: Mapped[list[Embedding]] = relationship(back_populates="knowledge_document")


class Embedding(Base):
    """A vector embedding of a knowledge document (build bible §10, pgvector)."""

    __tablename__ = "embeddings"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    knowledge_document_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        index=True,
    )
    vector: Mapped[list[float]] = mapped_column(pgvector.sqlalchemy.Vector(VECTOR_DIM))

    knowledge_document: Mapped[KnowledgeDocument] = relationship(back_populates="embeddings")


class AISession(Base):
    """A session of AI agent activity (build bible §10 ``ai_sessions``, §9)."""

    __tablename__ = "ai_sessions"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(sa.Uuid(as_uuid=False), sa.ForeignKey("users.id"))
    task_type: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[str] = mapped_column(sa.String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    project: Mapped[Project] = relationship(back_populates="ai_sessions")
    actions: Mapped[list[AIAction]] = relationship(back_populates="session")


class AIAction(Base):
    """One AI action audit record (build bible §10 ``ai_actions``, §31.1/§31.5).

    One row per model call: model, tokens in/out, latency, approval status.
    """

    __tablename__ = "ai_actions"

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("ai_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    agent: Mapped[str] = mapped_column(sa.String(64))
    tool: Mapped[str | None] = mapped_column(sa.String(64))
    model: Mapped[str] = mapped_column(sa.String(128))
    tokens_in: Mapped[int] = mapped_column(sa.Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, default=0)
    input_hash: Mapped[str | None] = mapped_column(sa.String(128))
    output_ref: Mapped[str | None] = mapped_column(sa.String(1024))
    approval_status: Mapped[str | None] = mapped_column(sa.String(32))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )

    session: Mapped[AISession] = relationship(back_populates="actions")


class Job(Base):
    """An async AI-backed job (build bible §10 ``jobs``, §11 202 + SSE)."""

    __tablename__ = "jobs"
    # Named so Alembic autogenerate matches the DB constraint by name (see
    # the note on ``Failure.__table_args__``).
    __table_args__ = (
        sa.CheckConstraint("progress >= 0.0 AND progress <= 1.0", name="jobs_progress_check"),
    )

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    project_id: Mapped[str | None] = mapped_column(
        sa.Uuid(as_uuid=False),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )
    type: Mapped[JobType] = mapped_column(_enum_column(JobType))
    status: Mapped[JobStatus] = mapped_column(_enum_column(JobStatus), default=JobStatus.PENDING)
    progress: Mapped[float] = mapped_column(sa.Double, default=0.0)
    input_ref: Mapped[str | None] = mapped_column(sa.String(1024))
    output_ref: Mapped[str | None] = mapped_column(sa.String(1024))
    error: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    project: Mapped[Project | None] = relationship(back_populates="jobs")


class PromptVersion(Base):
    """A versioned prompt (build bible §31.6 prompt registry).

    Agents reference prompts by ``name@version``; golden evals (§22) pin
    the pair, so prompt changes are regression-tested, never silent.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (
        sa.UniqueConstraint("name", "version", name="uq_prompt_versions_name_version"),
    )

    id: Mapped[str] = mapped_column(sa.Uuid(as_uuid=False), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(sa.String(128), index=True)
    version: Mapped[int] = mapped_column(sa.Integer)
    model_class: Mapped[str] = mapped_column(sa.String(16), default="coder")
    input_budget: Mapped[int | None] = mapped_column(sa.Integer)
    output_budget: Mapped[int | None] = mapped_column(sa.Integer)
    schema_ref: Mapped[str | None] = mapped_column(sa.String(128))
    temperature: Mapped[float | None] = mapped_column(sa.Double)
    body: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )


__all__ = [
    "AIAction",
    "AISession",
    "Artifact",
    "Base",
    "Embedding",
    "Failure",
    "File",
    "GeneratedTest",
    "Job",
    "KnowledgeDocument",
    "Organization",
    "Project",
    "ProjectMember",
    "PromptVersion",
    "Repository",
    "Requirement",
    "RequirementTestCase",
    "TestResult",
    "TestRun",
    "TestCase",
    "User",
    "VECTOR_DIM",
]
