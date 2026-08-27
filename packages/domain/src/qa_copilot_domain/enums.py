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


class JobStatus(StrEnum):
    """Job lifecycle (build bible §10 ``jobs``, §11 202+SSE contract)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


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
