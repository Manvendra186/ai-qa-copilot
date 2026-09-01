"""Core domain models and rules (build bible §7).

S0.4: pydantic entities + enums for the §10 core data model — project,
requirement, test_case, failure, artifact, job.
S0.8: auth baseline (§31.3) — ``User``, ``ProjectMember``, ``ProjectRole``
and the ``role_at_least`` permission rule.
S2.1: ``RepositoryProfile`` — repository scanner output (build bible §7 / §19).
S2.2: ``TestConventions`` (+ ``LocatorStyle``, ``TestScript``) — convention
extractor output, the shared contract for the S2.3 automation agent (§19).
S2.4: ``GeneratedTestStatus`` + transition rule — generated-test review
lifecycle (diff review, approve/apply, reject; build bible §19 S2.4).
S3.3: ``NormalizedFailure`` — raw failure text normalized onto the §16
taxonomy (build bible §15, §19 S3.3).
S6.1: ``ImpactKind`` + ``ImpactedTest`` + ``ImpactSet`` — change-impact
core output (direct/generated/referenced; build bible §19 S6.1).
"""

from .base import DomainModel
from .entities import (
    DEFAULT_FAILING_THRESHOLD,
    DEFAULT_FLAKY_THRESHOLD,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_RECENT_WINDOW,
    Artifact,
    Failure,
    ImpactedTest,
    ImpactSet,
    Job,
    LocatorStyle,
    NormalizedFailure,
    Project,
    ProjectMember,
    RecommendationSet,
    RecommenderItem,
    RepositoryProfile,
    Requirement,
    RiskRanking,
    TestCase,
    TestConventions,
    TestHistoryStats,
    TestRisk,
    TestScript,
    User,
)
from .enums import (
    ALLOWED_GENERATED_TEST_TRANSITIONS,
    ROLE_RANK,
    ArtifactType,
    FailureCategory,
    GeneratedTestStatus,
    ImpactKind,
    JobStatus,
    JobType,
    Priority,
    ProjectRole,
    RiskLevel,
    RunStatus,
    TestResultStatus,
    TestType,
    can_transition_generated_test,
    role_at_least,
)

__version__ = "0.1.0"

__all__ = [
    "ALLOWED_GENERATED_TEST_TRANSITIONS",
    "Artifact",
    "ArtifactType",
    "DEFAULT_FAILING_THRESHOLD",
    "DEFAULT_FLAKY_THRESHOLD",
    "DEFAULT_MIN_SAMPLE",
    "DEFAULT_RECENT_WINDOW",
    "DomainModel",
    "Failure",
    "FailureCategory",
    "GeneratedTestStatus",
    "ImpactedTest",
    "ImpactKind",
    "ImpactSet",
    "Job",
    "JobStatus",
    "JobType",
    "LocatorStyle",
    "NormalizedFailure",
    "Priority",
    "Project",
    "ProjectMember",
    "ProjectRole",
    "RecommendationSet",
    "RecommenderItem",
    "RepositoryProfile",
    "Requirement",
    "RiskLevel",
    "RiskRanking",
    "ROLE_RANK",
    "RunStatus",
    "TestCase",
    "TestConventions",
    "TestHistoryStats",
    "TestResultStatus",
    "TestRisk",
    "TestScript",
    "TestType",
    "User",
    "__version__",
    "can_transition_generated_test",
    "role_at_least",
]
