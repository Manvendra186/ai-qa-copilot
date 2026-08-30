"""API response schemas (build bible §7)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness contract: process up, configuration readable, no I/O."""

    status: str = "ok"
    service: str
    version: str
    env: str
    timestamp: datetime


# --- Auth baseline (S0.8, §31.3) ---------------------------------------------


class LoginRequest(BaseModel):
    """Dev-mode login (single user per project team; SSO is Phase 8)."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    """A user; ``role`` is the default role (authorization uses project roles)."""

    id: str
    email: str
    role: str


class ProjectRef(BaseModel):
    """A project the caller is a member of, with the caller's role in it."""

    id: str
    name: str
    role: str


class TokenResponse(BaseModel):
    """Login result: Bearer access token + the caller's project memberships."""

    token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    projects: list[ProjectRef]


class MeResponse(BaseModel):
    """``GET /api/v1/auth/me``: who I am + where I have roles."""

    user: UserOut
    projects: list[ProjectRef]


class ProjectOut(BaseModel):
    """Project detail (read endpoints, viewer floor)."""

    id: str
    name: str
    settings: dict[str, Any]


# --- Jobs (S0.9, §11: 202 + SSE) ---------------------------------------------


class AnalyzeRequest(BaseModel):
    """``POST /api/v1/requirements/analyze`` (§11): inline requirement + project.

    S0.9 carries the requirement inline (no requirement row yet — the S1.x
    requirement agent persists it); ``project_id`` scopes the job for RBAC
    and the SSE project filter.
    """

    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)


class TestDesignRequest(BaseModel):
    """``POST /api/v1/requirements/test-cases`` (S1.2, §11).

    The requirement the Test Design Agent builds a test suite for. Same
    shape as :class:`AnalyzeRequest`; the S1.1 analysis can be chained in a
    later milestone.
    """

    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)


class JobCreated(BaseModel):
    """202 body (§11): job id + initial status (``Location`` points at ``GET /jobs/{id}``)."""

    job_id: str
    status: str


class JobOut(BaseModel):
    """``GET /api/v1/jobs/{id}`` (§11): status, progress, result/error refs."""

    id: str
    project_id: str | None
    type: str
    status: str
    progress: float
    input_ref: str | None
    output_ref: str | None
    error: str | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


# --- S1.3: persisted requirement + test cases read-back (§10, §12) -------------


class TestCaseOut(BaseModel):
    """One structured test case (§12 vocabulary; enum values as wire strings)."""

    id: str
    title: str
    type: str
    priority: str
    preconditions: list[str]
    steps: list[str]
    expected_results: list[str]
    risk: str
    created_at: datetime | None


class RequirementOut(BaseModel):
    """``GET /api/v1/requirements/{id}`` (S1.3 read-back).

    The ``test_case_generation`` job stores the requirement id in its
    ``output_ref`` (§11); this is what the shell renders after the job
    completes. ``test_cases`` are the §10 rows linked via the M:N join.
    """

    id: str
    project_id: str
    title: str
    content: str
    acceptance_criteria: list[str]
    risk: str
    created_at: datetime | None
    test_cases: list[TestCaseOut]


class RequirementSummaryOut(BaseModel):
    """One row of ``GET /api/v1/projects/{id}/requirements`` (history list).

    Summary only — id / title / risk / created_at / test-case count. The full
    suite + content of one row still comes from ``GET /requirements/{id}``
    (S1.3 read-back), which the shell fetches when a row is opened.
    """

    id: str
    title: str
    risk: str
    created_at: datetime | None
    test_case_count: int


# --- S2.4: automation generation + generated-test review (§19 S2.4) -----------


class AutomationRequest(BaseModel):
    """``POST /api/v1/automation/generate`` (S2.4, §11).

    Automates one approved test case (S1.2 output) into a generated test
    file. The S2.3 agent runs as an ``automation_generation`` job (202 +
    SSE); its output becomes a **pending** ``generated_tests`` row, and the
    review endpoints (approve / apply / reject) act on that row.
    ``repository_path`` is the target repository the scan + conventions
    (S2.1/S2.2) are extracted from, and where ``apply`` writes the file.
    """

    project_id: str = Field(min_length=1)
    test_case_id: str = Field(min_length=1)
    repository_path: str | None = Field(default=None, max_length=2048)


class GeneratedTestOut(BaseModel):
    """One generated test — S2.3 output, S2.4 review row.

    ``status`` is the domain ``GeneratedTestStatus`` wire string (``pending``
    / ``approved`` / ``applied`` / ``rejected``); the review endpoints
    enforce the state machine (invalid transitions → ``409``).
    """

    id: str
    project_id: str
    job_id: str | None
    test_case_id: str | None
    file_path: str
    file_path_pattern: str | None
    language: str
    framework: str
    content: str
    notes: list[str]
    repository_path: str | None
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime | None
    updated_at: datetime | None


class GeneratedTestReviewIn(BaseModel):
    """Optional reviewer note for approve / reject / apply (audit, §31.1)."""

    note: str | None = Field(default=None, max_length=2000)


# --- S3.2: run history, results, artifacts (§10, §15) -------------------------


class FailureOut(BaseModel):
    """A failure plus its AI diagnosis (§10 ``failures``, §16)."""

    id: str
    category: str
    root_cause: str | None
    confidence: float | None
    evidence: list[str]
    suggested_fix: str | None
    needs_human_approval: bool


class ArtifactOut(BaseModel):
    """One execution artifact row (§10 ``artifacts``, §15).

    ``uri`` is the store-relative reference (or an external ``file://`` /
    ``http://`` link in seed data); ``download_url`` is the API endpoint that
    streams the file bytes for artifacts living in the local store.
    """

    id: str
    test_result_id: str
    type: str
    uri: str
    metadata: dict[str, Any]
    created_at: datetime
    download_url: str | None = None


class TestResultOut(BaseModel):
    """Outcome of one test in a run (§10 ``test_results``, §15)."""

    id: str
    run_id: str
    test_case_id: str | None
    status: str
    duration: float | None
    failure: FailureOut | None = None
    artifacts: list[ArtifactOut] = Field(default_factory=list)


class RunListItem(BaseModel):
    """One run in a project's run history (S3.2 list row)."""

    id: str
    project_id: str
    commit_sha: str | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class RunDetail(BaseModel):
    """``GET /api/v1/runs/{id}`` (S3.2): the run, its results and artifacts.

    ``totals`` is computed from the run's test results and ``duration_s`` from
    the run timestamps — neither is stored on the ``test_runs`` row (§10 keeps
    outcomes per test result, not as per-run aggregates).
    """

    id: str
    project_id: str
    commit_sha: str | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    duration_s: float | None = None
    totals: dict[str, int]
    results: list[TestResultOut]
    artifacts: list[ArtifactOut]


# --- S5.3: project knowledge (§7, §14, §19) -----------------------------------


class KnowledgeIndexRequest(BaseModel):
    """S5.3: body for ``POST /projects/{id}/knowledge/index``.

    ``repository_path`` is the local repository root to index (its source
    files become ``repository_file`` documents). The project's persisted
    requirements, designed test cases, and run history are always part of the
    corpus, so the index is genuinely project-specific; the repository files
    extend it with the repository's own source.
    """

    repository_path: str | None = Field(
        default=None,
        min_length=1,
        max_length=2048,
        description="Optional local repository root to index; the project's "
        "persisted QA data is always included in the corpus.",
    )


class KnowledgeHit(BaseModel):
    """One search hit: a knowledge chunk with its source metadata (S5.3)."""

    score: float
    document_ref: str
    source_type: str
    title: str
    chunk_index: int
    content: str
    metadata: dict[str, Any]
    matched_terms: list[str]


class KnowledgeSearchResult(BaseModel):
    """S5.3: ``GET /projects/{id}/knowledge`` — project-specific chunks."""

    query: str
    total_candidates: int
    truncated: bool
    hits: list[KnowledgeHit]


class KnowledgeStatus(BaseModel):
    """S5.3: ``GET /projects/{id}/knowledge/status`` — what is indexed."""

    document_count: int
    by_source_type: dict[str, int]
    source_types: list[str]
    last_indexed_at: datetime | None = None


class KnowledgeDocumentOut(BaseModel):
    """S5.3: a project knowledge document (``GET .../knowledge/documents``)."""

    id: str
    source_type: str
    title: str
    source_ref: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime | None = None
