"""Core domain models and rules (build bible §7).

S0.4: pydantic entities + enums for the §10 core data model — project,
requirement, test_case, failure, artifact, job.
S0.8: auth baseline (§31.3) — ``User``, ``ProjectMember``, ``ProjectRole``
and the ``role_at_least`` permission rule.
"""

from .base import DomainModel
from .entities import (
    Artifact,
    Failure,
    Job,
    Project,
    ProjectMember,
    Requirement,
    TestCase,
    User,
)
from .enums import (
    ArtifactType,
    FailureCategory,
    JobStatus,
    JobType,
    Priority,
    ProjectRole,
    RiskLevel,
    ROLE_RANK,
    TestType,
    role_at_least,
)

__version__ = "0.1.0"

__all__ = [
    "Artifact",
    "ArtifactType",
    "DomainModel",
    "Failure",
    "FailureCategory",
    "Job",
    "JobStatus",
    "JobType",
    "Priority",
    "Project",
    "ProjectMember",
    "ProjectRole",
    "Requirement",
    "RiskLevel",
    "ROLE_RANK",
    "TestCase",
    "TestType",
    "User",
    "__version__",
    "role_at_least",
]
