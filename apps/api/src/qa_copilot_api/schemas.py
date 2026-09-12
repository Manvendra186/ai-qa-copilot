"""API response schemas (build bible §7)."""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class HealthResponse(BaseModel):
    """Liveness contract: process up, configuration readable, no I/O."""

    status: str = "ok"
    service: str
    version: str
    env: str
    timestamp: datetime


# --- Auth baseline (S0.8, §31.3) + S8.1 hardening (§19) ------------------------


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


class OrganizationRef(BaseModel):
    """S8.1: an organization the caller belongs to, with the caller's org role."""

    id: str
    name: str
    role: str


class TokenResponse(BaseModel):
    """Login/refresh result: Bearer access token + rotating refresh token.

    S8.1: the opaque refresh token (returned exactly once — only its SHA-256
    hash is stored, §17) and the caller's organization memberships.
    """

    token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int
    user: UserOut
    organizations: list[OrganizationRef]
    projects: list[ProjectRef]


class MeResponse(BaseModel):
    """``GET /api/v1/auth/me``: who I am + where I have roles (S8.1: + orgs)."""

    user: UserOut
    organizations: list[OrganizationRef]
    projects: list[ProjectRef]


class RegisterRequest(BaseModel):
    """S8.1: ``POST /api/v1/auth/register`` — account **and** workspace.

    One signup creates the user and a new organization the user owns
    (build bible §19 S8.1); ``organization_name`` defaults to a derived
    name. Email + password are validated (policy: 10+ chars, letter + digit).
    """

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)
    organization_name: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email address")
        return v


class RegisterResponse(BaseModel):
    """S8.1: registration result — the new account + its owned workspace."""

    user: UserOut
    organization: OrganizationRef
    projects: list[ProjectRef]


class RefreshRequest(BaseModel):
    """S8.1: ``POST /api/v1/auth/refresh`` — rotate an opaque refresh token."""

    refresh_token: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """S8.1: ``POST /api/v1/auth/change-password`` (authenticated).

    Re-authenticates ``current_password``; the new password must satisfy the
    S8.1 policy. All refresh tokens are revoked on success (§17: neither
    password is ever logged or stored in cleartext).
    """

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


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
    # S7.4: the linked Jira issue key (``PROJECT-123``); ``None`` until the
    # ``jira_link`` job has filed/updated the issue for this failure.
    jira_issue_key: str | None = None


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


class PullRequestRef(BaseModel):
    """S7.2: a GitHub pull request by owner/repo/number (§19 S7.2).

    The PR is resolved through the project's S7.1 GitHub integration
    (``fetch_pull_request``): its changed files become the S6.1 ``files[]``
    input. GitHub name rules: ``[A-Za-z0-9_.-]+``.
    """

    owner: str = Field(
        min_length=1, max_length=391, pattern=r"^[A-Za-z0-9_.-]+$", description="GitHub owner."
    )
    repo: str = Field(
        min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_.-]+$", description="Repository name."
    )
    number: int = Field(ge=1, description="Pull request number.")


class RegressionAnalysisRequest(BaseModel):
    """S6.4: body for ``POST /projects/{id}/regression/analyze`` (§19 S6.4).

    The change to analyze is given one of three ways (exactly one):

    * ``files`` — repo-relative changed paths (the diff); or
    * ``base_ref`` + ``head_ref`` — a git range resolved server-side
      (:func:`qa_copilot_repository.changed_files_from_range`); or
    * ``pull_request`` (S7.2) — ``{owner, repo, number}``, resolved through
      the project's S7.1 GitHub integration; the PR's changed files become
      the S6.1 input.

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
    files: list[str] | None = Field(
        default=None,
        description="Repo-relative changed files; mutually exclusive with the other sources.",
    )
    base_ref: str | None = Field(
        default=None, description="Git base ref (with head_ref); mutually exclusive with files."
    )
    head_ref: str | None = Field(
        default=None, description="Git head ref (with base_ref); mutually exclusive with files."
    )
    pull_request: PullRequestRef | None = Field(
        default=None,
        description="S7.2: GitHub PR {owner, repo, number} to analyze; mutually exclusive.",
    )
    top_n: int = Field(default=10, ge=1, le=500, description="Top-N recommendation size.")

    @model_validator(mode="after")
    def _check_change_source(self) -> "RegressionAnalysisRequest":
        """Exactly one change source: ``files`` / ``base_ref``+``head_ref`` / ``pull_request``."""
        has_files = self.files is not None
        has_range = self.base_ref is not None or self.head_ref is not None
        has_pr = self.pull_request is not None
        if sum((has_files, has_range, has_pr)) != 1:
            raise ValueError(
                "provide exactly one of `files`, a `base_ref`/`head_ref` pair, or `pull_request`"
            )
        if self.base_ref is not None and self.head_ref is None:
            raise ValueError("`base_ref` and `head_ref` must be provided together")
        if self.head_ref is not None and self.base_ref is None:
            raise ValueError("`base_ref` and `head_ref` must be provided together")
        if self.files is not None and len(self.files) == 0:
            raise ValueError("`files` must be a non-empty list of repo-relative paths")
        return self


class RegressionPrCommentRequest(BaseModel):
    """S7.2: body for ``POST /projects/{id}/regression/pr-comment`` (§19 S7.2).

    Resolves ``pull_request`` through the project's S7.1 GitHub integration,
    computes the deterministic S6.1/S6.2/S6.3 regression set from
    ``repository_path`` (owner+ only — it writes to the PR), and posts it as
    an idempotent comment on the PR: first post creates, re-posts update the
    existing marker comment instead of duplicating. Delivered asynchronously
    (202 + ``job_id``, §11); the ``regression.comment`` SSE event carries
    ``action`` / ``comment_id`` / ``html_url``.
    """

    repository_path: str = Field(
        min_length=1,
        description="Server-local path to the repository checkout (for S6.1 impact).",
    )
    pull_request: PullRequestRef = Field(
        description="The GitHub PR {owner, repo, number} to comment on."
    )
    top_n: int = Field(default=10, ge=1, le=500, description="Top-N recommendation size.")


class RegressionPrCommentResult(BaseModel):
    """S7.2: the ``regression.comment`` SSE payload (idempotent upsert result)."""

    action: str = Field(description="`created`, `updated`, or `unchanged`.")
    comment_id: int = Field(description="GitHub comment id.")
    html_url: str = Field(description="Web URL of the comment on GitHub.")
    owner: str
    repo: str
    number: int


class JiraLinkRequest(BaseModel):
    """S7.4: body for ``POST /projects/{id}/failures/{failure_id}/jira`` (§19 S7.4).

    The ``failure_id`` is the path parameter; this carries the Jira target —
    the Jira ``project_key`` (e.g. ``QA``) the issue is filed under. The
    ``jira_link`` job builds the deterministic issue payload from the failure +
    its S4.1 diagnosis and create-or-updates it (first link creates, re-links
    update in place; a stale key is recreated — self-healing). Delivered
    asynchronously (202 + ``job_id``, §11); the ``jira.issue`` SSE event
    carries ``action`` / ``key`` / ``url`` / ``project_key``.
    """

    project_key: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z][A-Za-z0-9_]*$",
        description="Jira project key (e.g. 'QA') the issue is filed under.",
    )


class JiraLinkResult(BaseModel):
    """S7.4: the ``jira.issue`` SSE payload (create-or-update result)."""

    action: str = Field(description="`created`, `updated`, or `recreated`.")
    key: str = Field(description="The Jira issue key (e.g. 'QA-123').")
    url: str | None = Field(description="Web URL of the issue (when Jira returns one).")
    project_key: str


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


# --- Teams: organizations, members, invites (S8.2, §19 S8.2) -------------------


class OrganizationOut(BaseModel):
    """S8.2: an organization the caller belongs to, with the caller's org role."""

    id: str
    name: str
    role: str
    member_count: int
    created_at: datetime


class OrgMemberOut(BaseModel):
    """S8.2: one member of an organization (``id`` is the user id)."""

    id: str
    email: str
    role: str
    joined_at: datetime


class AddOrgMemberRequest(BaseModel):
    """S8.2: ``POST /organizations/{id}/members`` (owner only).

    Promotes an **existing** account (by email) into the organization.
    New people join through a code-based invite (``POST .../invites`` +
    ``POST /invites/{code}/accept``) — there is no account creation here.
    """

    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "member"] = "member"

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email address")
        return v


class UpdateOrgMemberRequest(BaseModel):
    """S8.2: ``PATCH /organizations/{id}/members/{member_id}`` (owner only).

    Changing the role to ``owner`` transfers ownership: the acting owner
    steps down to ``member`` (one ``owner`` per org, enforced at the DB
    level). Demoting/removing the org's sole ``owner`` is a 409.
    """

    role: Literal["owner", "member"]


class InviteRequest(BaseModel):
    """S8.2: ``POST /organizations/{id}/invites`` (owner only).

    A one-time, code-based invite for an email (§29 — local-first, no SMTP):
    the response carries the single-use ``code`` exactly once; only its
    SHA-256 hash is stored (§17). The code expires 7 days out.
    """

    email: str = Field(min_length=3, max_length=320)
    role: Literal["owner", "member"] = "member"

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("invalid email address")
        return v


class InviteOut(BaseModel):
    """S8.2: a created invite.

    ``code`` is the single-use accept code — returned **exactly once**
    (only its SHA-256 hash is persisted, §17). ``expires_at`` is 7 days
    out; the code is consumed on accept.
    """

    id: str
    email: str
    role: str
    code: str
    created_at: datetime
    expires_at: datetime


class InviteAcceptResult(BaseModel):
    """S8.2: ``POST /invites/{code}/accept`` — the org the caller joined."""

    organization: OrganizationRef
    role: str


class DeleteOrganizationRequest(BaseModel):
    """S8.3: ``DELETE /api/v1/organizations/{id}`` — owner re-authentication.

    Org deletion is the most destructive org operation (§19 S8.3): the
    owner's **current password** is re-verified in the body before anything
    is deleted (wrong password → 401, audited as
    ``org.delete.reauth_failure``). The password is only ever verified
    against the hash — never stored, logged or audited (§17).
    """

    current_password: str = Field(min_length=1)


class AuditEventOut(BaseModel):
    """S8.3: one row of the append-only security audit trail (§17).

    ``actor_id`` is ``null`` when the actor is unknown (login failure) or
    was deleted (``ON DELETE SET NULL`` — the row survives its author);
    ``target`` is an opaque org/project/user id or email — never a
    credential. Returned newest-first by ``GET /organizations/{id}/audit``.
    """

    actor_id: str | None
    action: str
    target: str | None
    outcome: str
    ip: str | None
    at: datetime


# --- S8.4: organization billing (plans, usage, plan assignment, §19) ---------


class PlanOut(BaseModel):
    """S8.4: the org's effective plan and its quota caps (build bible §19 S8.4).

    ``name`` is the *resolved* plan — the stored ``organizations.plan``
    value mapped through the catalog (``dev`` / NULL / unknown all fall
    back to ``free`` — a stale value never unlocks premium capacity).
    ``limits`` exposes the plan's caps so clients can show headroom:
    ``max_projects``, ``runs_per_month``, ``tokens_per_month`` and
    ``concurrent_jobs``. Returned by ``GET /organizations/{id}/plan`` and
    after ``PATCH /organizations/{id}``.
    """

    name: str
    limits: dict[str, int]


class UsageOut(BaseModel):
    """S8.4: the org's live usage metering (build bible §19 S8.4).

    Derived from the org's actual activity — never counters — so it cannot
    drift: ``runs`` = ``test_runs`` created in *month*, ``tokens`` = LLM
    tokens (in + out) of ``ai_actions`` in *month*, ``active_jobs`` =
    in-flight jobs (point-in-time), ``projects`` = org projects (point-in-time).
    ``month`` is the ``YYYY-MM`` window the monthly counters cover.
    """

    month: str
    runs: int
    tokens: int
    active_jobs: int
    projects: int


class UpdateOrganizationRequest(BaseModel):
    """S8.4: ``PATCH /organizations/{id}`` — assign the org's plan (owner only).

    Closed set: ``free`` / ``pro`` / ``enterprise`` (§19 S8.4 — local admins
    can set any plan; there is no external billing). Any other value is a
    422 (body validation). The assignment is audited as
    ``org.plan.updated`` (S8.3); a no-op PATCH (same plan) changes nothing
    and records no audit row.
    """

    plan: Literal["free", "pro", "enterprise"]
