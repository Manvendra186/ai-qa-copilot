"""API response schemas (build bible §7)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


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


# --- S5.5: project-knowledge Ask (§7, §14, §19) --------------------------------


class KnowledgeAskRequest(BaseModel):
    """S5.5: body for ``POST /projects/{id}/knowledge/ask``.

    A project-scoped question. The answer is grounded **only** in this
    project's knowledge base (S5.3 ``search_project_knowledge``) and returned
    asynchronously as a job (202 + ``job_id``, build bible §11) whose
    ``knowledge.answer`` event carries the answer text and its citations.
    """

    question: str = Field(
        min_length=1,
        max_length=4000,
        description="The project question to answer from the project knowledge base.",
    )


# --- S6.4: regression / impact / history / advice (§19 S6.4) -----------------


class RegressionAnalysisRequest(BaseModel):
    """S6.4: body for ``POST /projects/{id}/regression/analyze`` (§19 S6.4).

    The change to analyze is given one of two ways (exactly one):

    * ``files`` — repo-relative changed paths (the diff); or
    * ``base_ref`` + ``head_ref`` — a git range resolved server-side
      (:func:`qa_copilot_repository.changed_files_from_range`).

    The deterministic S6.1 change-impact set (computed from ``repository_path``)
    is joined with the project's S6.2 test history and ranked (S6.3); the
    optional S6.5 advisor brief summarizes the top-N. All delivered
    asynchronously as a job (202 + ``job_id``, §11) whose ``regression.set``
    SSE event carries the recommendation set.
    """

    repository_path: str = Field(
        min_length=1,
        description="Server-local path to the repository checkout (for S6.1 impact).",
    )
    files: list[str] = Field(
        default_factory=list,
        description="Repo-relative changed files; mutually exclusive with base_ref/head_ref.",
    )
    base_ref: str | None = Field(
        default=None, description="Git base ref (with head_ref); mutually exclusive with files."
    )
    head_ref: str | None = Field(
        default=None, description="Git head ref (with base_ref); mutually exclusive with files."
    )
    top_n: int = Field(default=10, ge=1, le=500, description="Top-N recommendation size.")

    @model_validator(mode="after")
    def _check_change_source(self) -> "RegressionAnalysisRequest":
        """Exactly one change source: ``files`` *or* the ``base_ref``/``head_ref`` pair."""
        has_files = bool(self.files)
        has_range = self.base_ref is not None and self.head_ref is not None
        if has_files and has_range:
            raise ValueError("provide either `files` or a `base_ref`/`head_ref` pair, not both")
        if not has_files and not has_range:
            raise ValueError("provide either `files` or both `base_ref` and `head_ref`")
        return self


class RunRequest(BaseModel):
    """S6.4 "Run this set" (§19 S6.4): run the selected regression tests.

    The selected tests (repo-relative Playwright test files from the
    ``regression.set`` recommendation) run through the existing S3 execution
    path: the ``run_execution`` job (202 + ``job_id``, §11) drives
    ``qa_copilot_execution.run_playwright`` and persists the run via
    ``qa_copilot_repository.persist_run``; the ``run.result`` SSE event
    carries the persisted run id and totals, and the job's ``output_ref`` is
    the persisted run id.
    """

    repository_path: str = Field(
        min_length=1,
        description="Server-local path to the repository checkout (Playwright target dir).",
    )
    tests: list[str] = Field(
        min_length=1,
        description="Repo-relative Playwright test file paths to run (from the regression set).",
    )
    timeout_s: float = Field(
        default=600.0, gt=0, le=3600, description="Playwright run timeout (seconds)."
    )


class KnowledgeCitation(BaseModel):
    """S5.5: one grounding source for an answer (mirrors the S5.3 hit metadata)."""

    document_ref: str
    source_type: str
    title: str
    score: float


class KnowledgeAnswer(BaseModel):
    """S5.5: the grounded answer payload (the ``knowledge.answer`` job event).

    ``in_scope`` is ``False`` when the knowledge base has nothing to ground the
    answer in; ``answer`` then explains why, and ``citations`` is empty.
    """

    in_scope: bool
    answer: str
    citations: list[KnowledgeCitation] = []
    confidence: float = 0.0


# --- Integrations (S7.1, §19 S7.1) ---------------------------------------------


class IntegrationConfigIn(BaseModel):
    """``PUT /api/v1/projects/{id}/integrations/{provider}`` body (owner+).

    Only the secret's *reference* is accepted (env-var name or secret-manager
    key) — a token value has no place in this API (build bible §17).
    """

    base_url: str | None = Field(default=None, max_length=1024)
    token_ref: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class IntegrationConfigOut(BaseModel):
    """An integration config row.

    The token value is never in this payload (§17): callers see
    ``token_configured`` (a ref is set) plus the ref's name itself.
    """

    project_id: str
    provider: str
    base_url: str | None
    token_ref: str | None
    token_configured: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime
