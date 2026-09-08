"""S7.5 live baseline — idempotent DB seed (mirrors the S6.5 evidence pair).

S7.5 (build bible §19) is the live E2E baseline for the **webhook → regression
→ run → Jira-link** legs. This seed prepares the demo project's S7.3 / S7.2 /
S3 / S7.4 dependencies (the S7.4 Jira leg was completed 2026-09-08 and is no
longer deferred):

  * ``project.settings.repository_path`` = the demo-app checkout. Both the
    S6.1 impact analysis and the S3 Playwright execution read this path, so it
    must be set for the regression job to work.
  * ``integration_configs(provider='github')`` — the S7.2 PR-fetch integration.
    ``token_ref`` names the env var holding the PAT (the secret itself is never
    stored — S7.1); ``base_url`` is left for the live driver to point at its
    in-process fake GitHub server.
  * ``integration_configs(provider='github_webhook')`` — the S7.3 webhook
    secret. ``token_ref`` names the env var holding the ``whsec_`` secret.
  * ``integration_configs(provider='jira')`` — the S7.4 Jira-link integration.
    ``token_ref`` names the env var holding the Jira API token; ``base_url`` is
    left for the live driver to point at its in-process fake Jira server.
  * the S6.5 applied generated test + seeded run history (verified present).

DB-only (no GitHub / no LLM), idempotent (safe to re-run), like the S6.5 seed.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(
    0,
    os.pathsep.join(
        str(p)
        for p in (
            ROOT / "apps" / "api" / "src",
            ROOT / "packages" / "domain" / "src",
            ROOT / "packages" / "prompt" / "src",
            ROOT / "packages" / "ai" / "src",
            ROOT / "packages" / "repository" / "src",
            ROOT / "packages" / "execution" / "src",
            ROOT / "packages" / "knowledge" / "src",
            ROOT / "packages" / "scheduler" / "src",
            ROOT / "packages" / "integrations" / "src",
        )
    ),
)

from qa_copilot_api.config import get_settings  # noqa: E402
from qa_copilot_api.db import make_app_engine  # noqa: E402
from qa_copilot_domain.enums import (  # noqa: E402
    FailureCategory,
    GeneratedTestStatus,
    Priority,
    RiskLevel,
    RunStatus,
    TestResultStatus,
    TestType,
)
from qa_copilot_repository import models  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

# Demo project ("Demo App") — the S6.5 evidence target. The generated test +
# run history below match the S6.5 seed so the S7.5 ranked set + run leg share
# the same inputs.
PROJECT_ID = "f500a3b2-04a3-4cd2-9eaf-87f7c39c98fe"
DEMO_APP = r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app"
TC_ID = "3cfe1127-ccf9-4dce-9fe5-c241193468e2"
REQ_ID = "cf4b4237-bbba-4b93-9cf3-0420b3a3649f"
TEST_FILE = "e2e/demo.spec.js"

# Integration token_refs (env-var NAMES — the secrets live in env, never the DB).
GH_TOKEN_REF = "S75_FAKE_GH_TOKEN"
WHSEC_REF = "S75_WEBHOOK_SECRET"
JIRA_TOKEN_REF = "S75_FAKE_JIRA_TOKEN"


def _ensure_requirement(session: Session) -> str:
    """The single seeded requirement for the demo project (idempotent by project)."""
    req = session.scalar(
        select(models.Requirement).where(models.Requirement.project_id == PROJECT_ID)
    )
    if req is None:
        req = models.Requirement(
            id=REQ_ID,
            project_id=PROJECT_ID,
            title="Login accepts valid credentials",
            content="A user with valid credentials signs in and reaches the product catalog.",
            acceptance_criteria=["Valid credentials sign in", "The product catalog is shown"],
            risk=RiskLevel.MEDIUM,
        )
        session.add(req)
        session.flush()
    return req.id


def _ensure_testcase(session: Session, requirement_id: str) -> str:
    """The login test case + its requirement link (idempotent by test-case id)."""
    if session.get(models.TestCase, TC_ID) is None:
        tc = models.TestCase(
            id=TC_ID,
            title="Login accepts valid credentials",
            type=TestType.FUNCTIONAL,
            priority=Priority.MEDIUM,
            preconditions=["A valid demo account (qa / qa1234) exists"],
            steps=["Fill the username and password", "Submit the login form"],
            expected_results=["Signed in; the product catalog is shown"],
            risk=RiskLevel.MEDIUM,
        )
        session.add(tc)
        session.flush()
        session.add(models.RequirementTestCase(requirement_id=requirement_id, test_case_id=TC_ID))
    return TC_ID


def _ensure_generated_test(session: Session, test_case_id: str) -> None:
    """Applied generated test pointing at the real Playwright spec (idempotent)."""
    exists = session.scalar(
        select(models.GeneratedTest).where(models.GeneratedTest.project_id == PROJECT_ID)
    )
    if exists is None:
        session.add(
            models.GeneratedTest(
                id="e3f8c1d0-9a2b-4c5d-8e7f-0a1b2c3d4e5f",
                project_id=PROJECT_ID,
                test_case_id=test_case_id,
                file_path=TEST_FILE,
                language="javascript",
                framework="playwright",
                content="import { expect, test } from './fixtures.js';\n",
                repository_path=DEMO_APP,
                status=GeneratedTestStatus.APPLIED,
            )
        )


def _ensure_runs(session: Session, test_case_id: str) -> None:
    """Six seeded executions (the S6.5 fail->pass shape) for the login test case."""
    n = session.scalar(
        select(func.count())
        .select_from(models.TestResult)
        .where(models.TestResult.test_case_id == test_case_id)
    )
    if n:
        return
    statuses = (
        TestResultStatus.FAILED,
        TestResultStatus.FLAKY,
        TestResultStatus.FLAKY,
        TestResultStatus.PASSED,
        TestResultStatus.PASSED,
        TestResultStatus.PASSED,
    )
    base = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    for i, status in enumerate(statuses):
        run = models.TestRun(
            id=f"seed-run-s75-{i:02d}",
            project_id=PROJECT_ID,
            status=RunStatus.COMPLETED,
            started_at=base + timedelta(minutes=i),
            completed_at=base + timedelta(minutes=i, seconds=3),
        )
        session.add(run)
        session.flush()
        tr = models.TestResult(
            id=f"seed-result-s75-{i:02d}",
            run_id=run.id,
            test_case_id=test_case_id,
            status=status,
            duration=3.2 + i * 0.05,
        )
        session.add(tr)
        session.flush()
        if status is TestResultStatus.FAILED:
            session.add(
                models.Failure(
                    id=f"seed-failure-s75-{i:02d}",
                    test_result_id=tr.id,
                    category=FailureCategory.AUTOMATION_DEFECT,
                    root_cause="selector drift in the login form",
                    confidence=0.8,
                )
            )


def _upsert_integration(
    session: Session, provider: str, *, token_ref: str, base_url: str | None = None
) -> models.IntegrationConfig:
    row = session.scalar(
        select(models.IntegrationConfig).where(
            models.IntegrationConfig.project_id == PROJECT_ID,
            models.IntegrationConfig.provider == provider,
        )
    )
    if row is None:
        row = models.IntegrationConfig(
            project_id=PROJECT_ID,
            provider=provider,
            base_url=base_url,
            token_ref=token_ref,
            enabled=True,
        )
        session.add(row)
    else:
        row.token_ref = token_ref
        row.enabled = True
        if base_url is not None:
            row.base_url = base_url
    return row


def main() -> None:
    engine = make_app_engine(get_settings().database_url)
    with Session(engine) as session:
        project = session.get(models.Project, PROJECT_ID)
        if project is None:
            raise SystemExit(f"demo project not found: {PROJECT_ID}")

        settings_dict = dict(project.settings or {})
        if settings_dict.get("repository_path") != DEMO_APP:
            settings_dict["repository_path"] = DEMO_APP
            project.settings = settings_dict

        requirement_id = _ensure_requirement(session)
        test_case_id = _ensure_testcase(session, requirement_id)
        _ensure_generated_test(session, test_case_id)
        _ensure_runs(session, test_case_id)
        _upsert_integration(session, "github", token_ref=GH_TOKEN_REF)
        _upsert_integration(session, "github_webhook", token_ref=WHSEC_REF)
        _upsert_integration(session, "jira", token_ref=JIRA_TOKEN_REF)

        session.commit()

        gen = session.scalar(
            select(models.GeneratedTest).where(models.GeneratedTest.project_id == PROJECT_ID)
        )
        n_results = (
            session.scalar(
                select(func.count())
                .select_from(models.TestResult)
                .where(models.TestResult.test_case_id == test_case_id)
            )
            or 0
        )
        configs = session.scalars(
            select(models.IntegrationConfig).where(
                models.IntegrationConfig.project_id == PROJECT_ID
            )
        ).all()
        req_ok = session.get(models.Requirement, requirement_id) is not None
        tc_ok = session.get(models.TestCase, test_case_id) is not None
        print(f"seed OK: project={PROJECT_ID}")
        print(f"  repository_path={settings_dict.get('repository_path')!r}")
        print(f"  requirement={'present' if req_ok else 'MISSING'}")
        print(f"  test_case={'present' if tc_ok else 'MISSING'}")
        print(f"  generated_test={'present' if gen else 'MISSING'} ({TEST_FILE})")
        print(f"  test_results={n_results}")
        for c in configs:
            print(
                f"  integration_configs: provider={c.provider!r} "
                f"token_ref={c.token_ref!r} enabled={c.enabled!r}"
            )


if __name__ == "__main__":
    main()
