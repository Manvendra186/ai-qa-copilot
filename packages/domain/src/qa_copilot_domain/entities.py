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
    ImpactKind,
    JobStatus,
    JobType,
    Priority,
    ProjectRole,
    RiskLevel,
    TestResultStatus,
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


class ImpactedTest(DomainModel):
    """One test file in a change-impact set, with why (build bible §19 S6.1).

    ``kinds`` is the deterministic classification
    (:class:`~qa_copilot_domain.enums.ImpactKind`) — one or more of
    ``direct`` / ``generated`` / ``referenced``. ``changed_files`` are the
    changed files that pulled this test in; ``test_case_ids`` and
    ``requirement_ids`` are the S1.2 links (via the ``requirement_test_cases``
    join, §10) surfaced so the S6.3 recommendation can rank regressions by
    ``requirements.risk`` / ``test_cases.priority``. ``signals`` are the
    human-readable, deterministic reasons (evidence trail for the S6.3 UI).
    """

    path: NonBlankStr
    kinds: list[ImpactKind]
    changed_files: list[str] = Field(default_factory=list)
    test_case_ids: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)


class ImpactSet(DomainModel):
    """Result of change-impact analysis (build bible §19 S6.1).

    The deterministic, LLM-free mapping from a diff (``changed`` files) to
    the test files that should be re-run. Produced by
    :func:`qa_copilot_repository.impact.compute_impact` — ``impacted`` is
    sorted by ``path`` and every list is sorted and deduped, so equal inputs
    always produce equal JSON (the wall-clock ``computed_at`` excepted;
    golden tests in ``tests/unit/test_impact.py`` compare the full payload).
    """

    changed: list[str]
    impacted: list[ImpactedTest] = Field(default_factory=list)
    test_files_scanned: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    computed_at: datetime | None = None


# ---------------------------------------------------------------------------
# S6.2 — Regression Intelligence: flaky + risk core (build bible §7, §19 S6.2).
# ---------------------------------------------------------------------------

#: Policy defaults for the deterministic flaky/failing flags (build bible
#: §19 S6.2). Single source of truth — the repository core and the web/API
#: both read these so the flagging policy never drifts between layers.
DEFAULT_MIN_SAMPLE = 3
DEFAULT_RECENT_WINDOW = 5
DEFAULT_FLAKY_THRESHOLD = 0.25
DEFAULT_FAILING_THRESHOLD = 0.50


class TestHistoryStats(DomainModel):
    """Deterministic per-test history statistics (build bible §19 S6.2).

    Computed LLM-free from a test's ``test_results`` history (and the
    ``failures`` diagnoses linked to it). Every rate is a 0.0–1.0 fraction of
    *executed* runs (``passed`` / ``failed`` / ``flaky``); ``skipped`` is
    reported but excluded from the denominators.

    - ``flakiness_rate`` — the share of executions that were flaky (a ``flaky``
      outcome *or* a ``flaky_behavior`` diagnosis);
    - ``failure_rate`` — the share of executions that ended ``failed``;
    - ``recent_failure_rate`` — the share of the most recent ``recent_window``
      executions that ended ``failed`` (the "broke recently" signal).

    ``insufficient_samples`` is True when fewer than ``min_sample`` executions
    exist — then neither ``is_flaky`` nor ``is_failing`` may be raised (the
    build bible's "no flags from a single run"). Equal inputs always produce
    equal JSON (the S2.1/S3.3/S5.1 deterministic-core pattern).
    """

    test_key: NonBlankStr
    executions: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    flaky: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    flakiness_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    recent_failure_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    is_flaky: bool = False
    is_failing: bool = False
    insufficient_samples: bool = False
    last_status: TestResultStatus | None = None
    last_run_id: str | None = None


class TestRisk(DomainModel):
    """One ranked test in a regression-risk set (build bible §19 S6.2).

    ``risk_score`` is the deterministic
    ``f(impact kind, failure rate, flakiness rate, requirement risk,
    test-case priority)``; the S6.3 recommender orders the set by it (stable
    tie-break on ``test_key``). ``stats`` carries the full per-test history
    evidence; ``signals`` are the deterministic, human-readable reasons that
    fired (the S6.4 UI's per-test chips).
    """

    test_key: NonBlankStr
    risk_score: float = Field(default=0.0, ge=0.0)
    signals: list[str] = Field(default_factory=list)
    stats: TestHistoryStats
    impact_kind: ImpactKind | None = None
    requirement_risk: RiskLevel | None = None
    test_case_priority: Priority | None = None


class RiskRanking(DomainModel):
    """Deterministic flaky + risk ranking over a project's run history (§19 S6.2).

    The LLM-free core output: per-test :class:`TestHistoryStats` plus the
    deterministic :class:`TestRisk` score. ``ranked`` is ordered by
    ``risk_score`` descending, then ``test_key`` ascending — stable and
    reproducible (equal inputs ⇒ equal JSON, ``computed_at`` excepted). The
    flagging policy (``min_sample`` / window / thresholds) is echoed so the
    S6.4 UI can explain *why* a flag fired.
    """

    project_id: NonBlankStr
    ranked: list[TestRisk] = Field(default_factory=list)
    min_sample: int = Field(default=DEFAULT_MIN_SAMPLE, ge=1)
    recent_window: int = Field(default=DEFAULT_RECENT_WINDOW, ge=1)
    flaky_threshold: float = Field(default=DEFAULT_FLAKY_THRESHOLD, ge=0.0, le=1.0)
    failing_threshold: float = Field(default=DEFAULT_FAILING_THRESHOLD, ge=0.0, le=1.0)
    computed_at: datetime | None = None


# ---------------------------------------------------------------------------
# S6.3 — Deterministic regression recommender (build bible §19 S6.3).
# ---------------------------------------------------------------------------


class RecommenderItem(DomainModel):
    """One ranked regression recommendation (build bible §19 S6.3).

    The S6.3 join of the S6.1 change-impact set (which tests to re-run, and
    why) with the S6.2 flaky/risk ranking (how risky each is). ``rank`` is the
    1-based position in the top-N set — the set is ordered by ``risk_score``
    descending, then ``test_key`` ascending (the stable tie-break, so equal
    inputs always yield the same order). ``impact_kind`` is the strongest
    S6.1 impact kind; ``changed_files`` are the changed files that pulled the
    test in; ``stats`` carries the full S6.2 per-test history evidence;
    ``rationale`` is the deterministic, human-readable evidence trail (impact
    kind, failure rate, flakiness rate, requirement risk, test-case priority,
    changed files) shown next to a ranked test (the S6.4 UI's per-test chips).
    """

    test_key: NonBlankStr
    stats: TestHistoryStats
    rank: int = Field(default=1, ge=1)
    risk_score: float = Field(default=0.0, ge=0.0)
    impact_kind: ImpactKind | None = None
    changed_files: list[str] = Field(default_factory=list)
    requirement_risk: RiskLevel | None = None
    test_case_priority: Priority | None = None
    rationale: list[str] = Field(default_factory=list)


class RecommendationSet(DomainModel):
    """Deterministic top-N regression recommendation set (build bible §19 S6.3).

    The LLM-free core output: the S6.1 impact set joined with the S6.2 risk
    ranking, ordered by ``risk_score`` descending then ``test_key`` ascending
    (stable and reproducible) and truncated to ``top_n``. ``recommendations``
    carries the per-test evidence (impact, history stats, rationale); the
    flagging policy (``min_sample`` / window / thresholds) is echoed from the
    S6.2 ranking so the S6.4 UI can explain *why* a flag fired. Equal inputs
    always produce equal JSON (the wall-clock ``computed_at`` excepted; golden
    tests drop it before comparing — the S2.1/S3.3/S5.1 deterministic-core
    pattern).
    """

    project_id: NonBlankStr
    changed: list[str]
    recommendations: list[RecommenderItem] = Field(default_factory=list)
    top_n: int = Field(default=10, ge=1)
    min_sample: int = Field(default=DEFAULT_MIN_SAMPLE, ge=1)
    recent_window: int = Field(default=DEFAULT_RECENT_WINDOW, ge=1)
    flaky_threshold: float = Field(default=DEFAULT_FLAKY_THRESHOLD, ge=0.0, le=1.0)
    failing_threshold: float = Field(default=DEFAULT_FAILING_THRESHOLD, ge=0.0, le=1.0)
    computed_at: datetime | None = None
