"""Core domain models and rules (build bible §7).

S0.4: pydantic entities + enums for the §10 core data model — project,
requirement, test_case, failure, artifact, job.
"""

from .base import DomainModel
from .entities import (
    Artifact,
    Failure,
    Job,
    Project,
    Requirement,
    TestCase,
)
from .enums import (
    ArtifactType,
    FailureCategory,
    JobStatus,
    JobType,
    Priority,
    RiskLevel,
    TestType,
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
    "Requirement",
    "RiskLevel",
    "TestCase",
    "TestType",
    "__version__",
]
