"""Domain enums (build bible §10, §11, §12, §16).

All values are snake_case wire strings so they round-trip unchanged through
JSON payloads, AI output schemas (§12) and structured logs.
"""

from enum import StrEnum


class TestType(StrEnum):
    """Test-case design types (build bible §3, §12)."""

    # Prevents pytest from trying to collect this non-test enum (name is Test*).
    __test__ = False

    FUNCTIONAL = "functional"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    RISK = "risk"
    ACCESSIBILITY = "accessibility"
    SECURITY = "security"


class Priority(StrEnum):
    """Test-case priority (build bible §10, §12)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskLevel(StrEnum):
    """Risk rating shared by requirements and test cases (build bible §10, §12)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JobType(StrEnum):
    """Async job kinds — one per AI-backed endpoint (build bible §11)."""

    REQUIREMENT_ANALYSIS = "requirement_analysis"
    TEST_CASE_GENERATION = "test_case_generation"
    AUTOMATION_GENERATION = "automation_generation"
    RUN_EXECUTION = "run_execution"
    FAILURE_ANALYSIS = "failure_analysis"
    FIX_PROPOSAL = "fix_proposal"
    REPOSITORY_INDEXING = "repository_indexing"
    KNOWLEDGE_INDEX = "knowledge_index"
    KNOWLEDGE_ASK = "knowledge_ask"
    REGRESSION_ANALYSIS = "regression_analysis"
    REGRESSION_PR_COMMENT = "regression_pr_comment"


class JobStatus(StrEnum):
    """Job lifecycle (build bible §10 ``jobs``, §11 202+SSE contract)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GeneratedTestStatus(StrEnum):
    """Generated-test review lifecycle (build bible §19 S2.4).

    ``pending`` is generated and awaiting human review; ``approved`` is
    approved but not yet applied; ``applied`` means the file was written to
    the workspace; ``rejected`` is the human decline. ``applied`` and
    ``rejected`` are terminal in V1 — re-generating a test creates a new row.
    """

    PENDING = "pending"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"


#: Allowed status transitions (S2.4 review flow, §19).
ALLOWED_GENERATED_TEST_TRANSITIONS: dict[GeneratedTestStatus, frozenset[GeneratedTestStatus]] = {
    GeneratedTestStatus.PENDING: frozenset(
        {
            GeneratedTestStatus.APPROVED,
            GeneratedTestStatus.APPLIED,
            GeneratedTestStatus.REJECTED,
        }
    ),
    GeneratedTestStatus.APPROVED: frozenset(
        {GeneratedTestStatus.APPLIED, GeneratedTestStatus.REJECTED}
    ),
    GeneratedTestStatus.APPLIED: frozenset(),
    GeneratedTestStatus.REJECTED: frozenset(),
}


def can_transition_generated_test(
    current: GeneratedTestStatus, target: GeneratedTestStatus
) -> bool:
    """True when *current* → *target* is a legal review transition (S2.4)."""
    return target in ALLOWED_GENERATED_TEST_TRANSITIONS[current]


class FailureCategory(StrEnum):
    """Failure taxonomy (build bible §16)."""

    PRODUCT_DEFECT = "product_defect"
    AUTOMATION_DEFECT = "automation_defect"
    ENVIRONMENT_DEFECT = "environment_defect"
    TEST_DATA_DEFECT = "test_data_defect"
    FLAKY_BEHAVIOR = "flaky_behavior"
    UNKNOWN = "unknown"


class ArtifactType(StrEnum):
    """Execution artifact kinds (build bible §15)."""

    TRACE = "trace"
    SCREENSHOT = "screenshot"
    VIDEO = "video"
    CONSOLE = "console"
    NETWORK = "network"
    DOM = "dom"
    LOG = "log"


class RunStatus(StrEnum):
    """Test-run lifecycle (build bible §10 ``test_runs``, §15).

    Values follow the job state machine (§31.2): ``pending`` →
    ``running`` → terminal ``completed`` / ``failed``. ``completed`` means
    the execution worker ran the suite and produced results — individual
    tests may have failed (that is per-test data, :class:`TestResultStatus`);
    ``failed`` means the worker itself failed (spawn error, timeout, no
    report).
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TestResultStatus(StrEnum):
    """Outcome of one test within a run (build bible §10 ``test_results``, §15).

    ``passed`` / ``failed`` are the terminal outcomes; ``flaky`` is a test
    that failed then passed on retry (Playwright ``flaky``); ``skipped``
    never ran; ``pending`` is the pre-execution placeholder (S0.5 default).
    """

    # Prevents pytest from trying to collect this non-test enum (name is Test*).
    __test__ = False

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    FLAKY = "flaky"
    SKIPPED = "skipped"


class ProjectRole(StrEnum):
    """Project-scoped role (build bible §31.3 — V1 auth baseline).

    RBAC is per project (a user can be ``owner`` of one project and
    ``viewer`` of another). Permission rule from §31.3: code
    apply/approve requires ``member`` or above; project deletion requires
    ``owner``.
    """

    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


#: Permission rank: higher means more authority (build bible §31.3).
ROLE_RANK: dict[ProjectRole, int] = {
    ProjectRole.VIEWER: 0,
    ProjectRole.MEMBER: 1,
    ProjectRole.OWNER: 2,
}


def role_at_least(role: ProjectRole, minimum: ProjectRole) -> bool:
    """True when *role* grants at least the *minimum* permission (§31.3)."""
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


class ImpactKind(StrEnum):
    """Why a test file is in a change-impact set (build bible §19 S6.1).

    - ``direct`` — the changed file is itself a test file (S2.1 heuristics);
    - ``generated`` — the changed file is an *applied* generated test
      (``generated_tests.file_path``, S2.4);
    - ``referenced`` — a test file imports a changed source file or uses one
      of its ``data-testid`` values (the S2.2 vocabulary).

    One test file can carry several kinds in the same impact set.
    """

    DIRECT = "direct"
    GENERATED = "generated"
    REFERENCED = "referenced"
