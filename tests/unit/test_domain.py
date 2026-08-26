"""Domain entity + enum schema tests (build bible §10/§12/§16 — S0.4 exit criterion).

Wire-format inputs (raw strings, ISO timestamps, unknown fields) are exercised
through ``model_validate`` — the JSON entry point — while typed constructors
are used for in-process construction.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError
from qa_copilot_domain import (
    Artifact,
    ArtifactType,
    Failure,
    FailureCategory,
    Job,
    JobStatus,
    JobType,
    Priority,
    Project,
    Requirement,
    RiskLevel,
    TestCase,
    TestType,
)


def test_test_type_covers_bible_design_types() -> None:
    assert {member.value for member in TestType} == {
        "functional",
        "negative",
        "boundary",
        "risk",
        "accessibility",
        "security",
    }


def test_failure_category_matches_taxonomy() -> None:
    assert {member.value for member in FailureCategory} == {
        "product_defect",
        "automation_defect",
        "environment_defect",
        "test_data_defect",
        "flaky_behavior",
        "unknown",
    }


def test_project_minimal_and_json_round_trip() -> None:
    project = Project(name="Checkout service")
    assert project.id is None
    assert Project.model_validate_json(project.model_dump_json()) == project


def test_project_requires_name() -> None:
    with pytest.raises(ValidationError, match="name"):
        Project.model_validate({})
    with pytest.raises(ValidationError):
        Project(name="   ")


def test_project_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Project.model_validate({"name": "x", "unknown_field": 1})


def test_requirement_defaults_and_criteria() -> None:
    requirement = Requirement(
        title="Cart totals",
        content="The cart shows itemized totals before tax.",
        acceptance_criteria=["Totals update on quantity change"],
    )
    assert requirement.risk is RiskLevel.MEDIUM
    assert requirement.acceptance_criteria == ["Totals update on quantity change"]


def test_requirement_content_required() -> None:
    with pytest.raises(ValidationError):
        Requirement(title="Cart totals", content="")


def test_test_case_from_bible_example() -> None:
    example = {
        "id": "TC-001",
        "title": "Reset password with registered email",
        "type": "functional",
        "priority": "high",
        "preconditions": ["User account exists"],
        "steps": ["Open login page", "Click reset password", "Submit email"],
        "expected_results": ["Reset link is delivered to the registered address"],
        "risk": "medium",
        "requirement_refs": ["REQ-001"],
    }
    tc = TestCase.model_validate(example)
    assert tc.type is TestType.FUNCTIONAL
    assert tc.priority is Priority.HIGH
    assert tc.requirement_refs == ["REQ-001"]
    dumped = tc.model_dump(mode="json")
    assert dumped["type"] == "functional"
    assert dumped["priority"] == "high"


def test_test_case_rejects_invalid_type() -> None:
    with pytest.raises(ValidationError):
        TestCase.model_validate({"title": "t", "type": "chaotic"})


def test_failure_defaults_to_unknown() -> None:
    failure = Failure(test_result_id="TR-9")
    assert failure.category is FailureCategory.UNKNOWN
    assert failure.confidence is None
    assert failure.needs_human_approval is True
    assert failure.evidence == []


def test_failure_confidence_bounds() -> None:
    assert Failure(confidence=0.92).confidence == 0.92
    with pytest.raises(ValidationError):
        Failure(confidence=1.5)
    with pytest.raises(ValidationError):
        Failure(confidence=-0.1)


def test_failure_analysis_output_shape() -> None:
    analysis = {
        "category": "automation_defect",
        "root_cause": "obsolete_locator",
        "confidence": 0.92,
        "evidence": ["DOM snapshot contains data-testid=submit-order"],
        "suggested_fix": "Update locator in checkout_page.py",
        "needs_human_approval": True,
    }
    failure = Failure.model_validate(analysis)
    assert failure.category is FailureCategory.AUTOMATION_DEFECT
    assert failure.root_cause == "obsolete_locator"
    assert failure.confidence == 0.92


def test_artifact_fields() -> None:
    artifact = Artifact(test_result_id="TR-1", type=ArtifactType.TRACE, uri="runs/r1/t1/trace.zip")
    assert artifact.type is ArtifactType.TRACE
    assert artifact.metadata == {}
    with pytest.raises(ValidationError):
        Artifact.model_validate({"type": "hologram", "uri": "runs/r1/t1/x"})


def test_job_defaults_to_pending() -> None:
    job = Job(type=JobType.REQUIREMENT_ANALYSIS, project_id="PRJ-1")
    assert job.status is JobStatus.PENDING
    assert job.progress == 0.0
    assert job.error is None


def test_job_progress_bounds() -> None:
    with pytest.raises(ValidationError):
        Job(type=JobType.RUN_EXECUTION, progress=1.5)


def test_job_lifecycle_from_wire_payload() -> None:
    job = Job.model_validate(
        {
            "type": "failure_analysis",
            "status": "completed",
            "progress": 1.0,
            "output_ref": "ai_actions/42",
            "completed_at": "2026-08-26T12:00:00Z",
        }
    )
    assert job.status is JobStatus.COMPLETED
    assert isinstance(job.completed_at, datetime)
    assert job.model_dump(mode="json")["status"] == "completed"


def test_enum_values_are_wire_strings() -> None:
    assert TestType.SECURITY.value == "security"
    assert JobStatus.CANCELLED.value == "cancelled"
    assert TestType("functional") is TestType.FUNCTIONAL
