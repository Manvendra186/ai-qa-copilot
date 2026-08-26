"""Idempotent dev seed for the AI QA Copilot database (S0.5).

Creates a small, consistent fixture graph (one organization → project →
requirements ↔ test cases, plus a knowledge document with embedding, a
prompt version, an AI session/action, a job, and a run with one pass and
one failing result with diagnosis + artifact).

Idempotency: every row is looked up by its natural key first and left
untouched when it already exists — running the script twice changes
nothing (no duplicates, no rewrites).

Usage (repo root):

    uv run alembic upgrade head   # schema must exist first
    uv run python scripts/seed.py
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable

from qa_copilot_domain.enums import (
    ArtifactType,
    FailureCategory,
    JobStatus,
    JobType,
    Priority,
    RiskLevel,
    TestType,
)
from qa_copilot_repository import db, models
from sqlalchemy import and_, select
from sqlalchemy.sql import ColumnElement

# --- Natural-key fixtures (stable across runs) -----------------------------

ORG_NAME = "Acme Dev"
USER_EMAIL = "dev@local.dev"
REPO_URL = "https://github.com/example/app-under-test"
PROJECT_NAME = "Demo App"
REQ_TITLES = (
    "Login accepts valid credentials",
    "Checkout calculates correct total",
)
TC_TITLES = (
    "Login succeeds with valid email and password",
    "Checkout total reflects item prices and discount",
)
DOC_SOURCE = "https://example.com/checkout-spec"
PROMPT_NAME = "requirement-analyst"
PROMPT_VERSION = 1
PROMPT_BODY = (
    "You are a requirements analyst. Given a user story, list testable "
    "acceptance criteria. Respond with JSON matching the schema ref."
)


def _get_or_create(
    session: db.Session,
    model_cls: type[models.Base],
    where: ColumnElement[bool],
    factory: Callable[[], models.Base],
    label: str,
) -> models.Base:
    """Find the first row matching *where*; insert *factory()* if none."""
    existing = session.scalars(select(model_cls).where(where)).first()
    if existing is not None:
        return existing
    row = factory()
    session.add(row)
    session.flush()
    print(f"  + created {label}")
    return row


def seed() -> None:
    engine = db.make_engine()
    with db.session_scope(engine) as session:
        print("Seeding dev fixtures (existing rows are left untouched):")

        org = _get_or_create(
            session,
            models.Organization,
            models.Organization.name == ORG_NAME,
            lambda: models.Organization(name=ORG_NAME, plan="dev"),
            f"organization '{ORG_NAME}'",
        )

        user = _get_or_create(
            session,
            models.User,
            models.User.email == USER_EMAIL,
            lambda: models.User(email=USER_EMAIL, role="owner"),
            f"user '{USER_EMAIL}'",
        )

        repo = _get_or_create(
            session,
            models.Repository,
            models.Repository.url == REPO_URL,
            lambda: models.Repository(provider="github", url=REPO_URL, default_branch="main"),
            f"repository '{REPO_URL}'",
        )

        project = _get_or_create(
            session,
            models.Project,
            models.Project.name == PROJECT_NAME,
            lambda: models.Project(
                organization_id=org.id,
                name=PROJECT_NAME,
                repository_id=repo.id,
                settings={},
            ),
            f"project '{PROJECT_NAME}'",
        )

        requirements = []
        for i, title in enumerate(REQ_TITLES):
            requirements.append(
                _get_or_create(
                    session,
                    models.Requirement,
                    and_(
                        models.Requirement.title == title,
                        models.Requirement.project_id == project.id,
                    ),
                    lambda title=title, i=i: models.Requirement(
                        project_id=project.id,
                        title=title,
                        content=f"{title} — acceptance criteria to be refined.",
                        acceptance_criteria=[
                            f"{title} (happy path)",
                            f"{title} (invalid input is rejected)",
                        ],
                        risk=RiskLevel.HIGH if i == 0 else RiskLevel.MEDIUM,
                    ),
                    f"requirement '{title}'",
                )
            )

        test_cases = []
        for i, title in enumerate(TC_TITLES):
            test_cases.append(
                _get_or_create(
                    session,
                    models.TestCase,
                    models.TestCase.title == title,
                    lambda title=title, i=i: models.TestCase(
                        title=title,
                        type=TestType.FUNCTIONAL,
                        priority=Priority.HIGH if i == 0 else Priority.MEDIUM,
                        preconditions=["A user is logged in."],
                        steps=[f"Step {n}" for n in range(1, 4)],
                        expected_results=["The expected outcome occurs."],
                        risk=RiskLevel.HIGH if i == 0 else RiskLevel.MEDIUM,
                    ),
                    f"test case '{title}'",
                )
            )

            rtc = session.scalars(
                select(models.RequirementTestCase).where(
                    models.RequirementTestCase.requirement_id == requirements[i].id,
                    models.RequirementTestCase.test_case_id == test_cases[i].id,
                )
            ).first()
            if rtc is None:
                session.add(
                    models.RequirementTestCase(
                        requirement_id=requirements[i].id,
                        test_case_id=test_cases[i].id,
                    )
                )
                session.flush()
                print(f"  + linked requirement '{requirements[i].title}'")

        doc = _get_or_create(
            session,
            models.KnowledgeDocument,
            and_(
                models.KnowledgeDocument.source_ref == DOC_SOURCE,
                models.KnowledgeDocument.project_id == project.id,
            ),
            lambda: models.KnowledgeDocument(
                project_id=project.id,
                source_type="url",
                source_ref=DOC_SOURCE,
                content="Checkout spec (excerpt): totals include tax; discounts apply before tax.",
                metadata_={"origin": "seed"},
            ),
            f"knowledge document '{DOC_SOURCE}'",
        )

        if (
            session.scalars(
                select(models.Embedding).where(models.Embedding.knowledge_document_id == doc.id)
            ).first()
            is None
        ):
            session.add(
                models.Embedding(
                    knowledge_document_id=doc.id,
                    # Placeholder zero vector — a real embedding arrives with
                    # the S0.6/S2.x embedding step (dimension per VECTOR_DIM).
                    vector=[0.0] * models.VECTOR_DIM,
                )
            )
            session.flush()
            print("  + created placeholder embedding (zero vector)")

        prompt = _get_or_create(
            session,
            models.PromptVersion,
            and_(
                models.PromptVersion.name == PROMPT_NAME,
                models.PromptVersion.version == PROMPT_VERSION,
            ),
            lambda: models.PromptVersion(
                name=PROMPT_NAME,
                version=PROMPT_VERSION,
                model_class="reasoner",
                temperature=0.1,
                body=PROMPT_BODY,
            ),
            f"prompt '{PROMPT_NAME}@{PROMPT_VERSION}'",
        )

        session_row = _get_or_create(
            session,
            models.AISession,
            models.AISession.id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "seed-session")),
            lambda: models.AISession(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "seed-session")),
                project_id=project.id,
                user_id=user.id,
                task_type="requirement_analysis",
            ),
            "ai session (seed)",
        )

        if (
            session.scalars(
                select(models.AIAction).where(models.AIAction.session_id == session_row.id)
            ).first()
            is None
        ):
            session.add(
                models.AIAction(
                    session_id=session_row.id,
                    agent="requirement_analyst",
                    model="seed-model",
                    tokens_in=120,
                    tokens_out=48,
                    latency_ms=350,
                )
            )
            session.flush()
            print("  + created ai action (seed)")

        job = _get_or_create(
            session,
            models.Job,
            models.Job.id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "seed-job")),
            lambda: models.Job(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "seed-job")),
                project_id=project.id,
                type=JobType.REQUIREMENT_ANALYSIS,
                status=JobStatus.PENDING,
            ),
            "job (requirement_analysis, pending)",
        )

        run = _get_or_create(
            session,
            models.TestRun,
            models.TestRun.id == str(uuid.uuid5(uuid.NAMESPACE_DNS, "seed-run")),
            lambda: models.TestRun(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "seed-run")),
                project_id=project.id,
                commit_sha="seed" * 5,
                status="completed",
            ),
            "test run (seed)",
        )

        def _result(name: str, tc: models.TestCase, status: str) -> models.TestResult:
            row = _get_or_create(
                session,
                models.TestResult,
                models.TestResult.id == str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
                lambda: models.TestResult(
                    id=str(uuid.uuid5(uuid.NAMESPACE_DNS, name)),
                    run_id=run.id,
                    test_case_id=tc.id,
                    status=status,
                    duration=1.25,
                ),
                f"test result '{name}'",
            )
            assert isinstance(row, models.TestResult)
            return row

        _result("seed-result-pass", test_cases[0], "passed")

        failed = _result("seed-result-fail", test_cases[1], "failed")
        failure = _get_or_create(
            session,
            models.Failure,
            models.Failure.test_result_id == failed.id,
            lambda: models.Failure(
                test_result_id=failed.id,
                category=FailureCategory.PRODUCT_DEFECT,
                root_cause="Discount is applied after tax instead of before.",
                confidence=0.82,
                evidence=["console: total mismatch", "screenshot: totals panel"],
                suggested_fix="Apply discount to subtotal before adding tax.",
            ),
            "failure diagnosis (seed)",
        )
        failed.failure_id = failure.id
        session.add(failed)

        if (
            session.scalars(
                select(models.Artifact).where(
                    models.Artifact.test_result_id == failed.id,
                    models.Artifact.type == ArtifactType.SCREENSHOT,
                )
            ).first()
            is None
        ):
            session.add(
                models.Artifact(
                    test_result_id=failed.id,
                    type=ArtifactType.SCREENSHOT,
                    uri="file://artifacts/seed/failure.png",
                    metadata_={"width": 1280, "height": 720},
                )
            )
            session.flush()
            print("  + created artifact (screenshot, seed)")

        print(
            "Seed complete: "
            f"1 organization, 1 user, 1 repository, 1 project, "
            f"{len(requirements)} requirements, {len(test_cases)} test cases, "
            f"1 knowledge document, 1 prompt version ({prompt.name}@"
            f"{prompt.version}), 1 job ({job.type.value}, {job.status.value}), "
            "1 run (1 passed / 1 failed w/ diagnosis + artifact)."
        )


if __name__ == "__main__":
    seed()
    sys.exit(0)
