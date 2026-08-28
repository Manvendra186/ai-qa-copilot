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
"""

from .base import DomainModel
from .entities import (
    Artifact,
    Failure,
    Job,
    LocatorStyle,
    Project,
    ProjectMember,
    RepositoryProfile,
    Requirement,
    TestCase,
    TestConventions,
    TestScript,
    User,
)
from .enums import (
    ALLOWED_GENERATED_TEST_TRANSITIONS,
    ROLE_RANK,
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
    can_transition_generated_test,
    role_at_least,
)

__version__ = "0.1.0"

__all__ = [
    "ALLOWED_GENERATED_TEST_TRANSITIONS",
    "Artifact",
    "ArtifactType",
    "DomainModel",
    "Failure",
    "FailureCategory",
    "GeneratedTestStatus",
    "Job",
    "JobStatus",
    "JobType",
    "LocatorStyle",
    "Priority",
    "Project",
    "ProjectMember",
    "ProjectRole",
    "RepositoryProfile",
    "Requirement",
    "RiskLevel",
    "ROLE_RANK",
    "RunStatus",
    "TestCase",
    "TestConventions",
    "TestResultStatus",
    "TestScript",
    "TestType",
    "User",
    "__version__",
    "can_transition_generated_test",
    "role_at_least",
]
