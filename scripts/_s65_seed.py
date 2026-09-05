"""S6.5: seed the Demo App project with the regression-intelligence evidence.

Makes the DB state deterministic for the live S6.5 E2E (bible §19 S6.5:
"seeded run history with >= 1 flaky + >= 1 fail->pass test"):

1. one **applied** ``generated_tests`` row for ``e2e/demo.spec.js`` linked to
   the "Login succeeds with valid email and password" test case (this is what
   makes the S6.1 impact set rank the spec as ``direct`` + ``generated``);
2. a six-execution history for that test case — the pre-existing seed run
   (``started_at`` NULL; it sorts last via its ``created_at``) already records
   one ``passed``; this adds five runs in time order:
   ``failed, flaky, flaky, passed, passed``.

   Resulting S6.2 stats (min_sample=3, recent_window=5,
   flaky_threshold=0.25, failing_threshold=0.50):

   - executions=6, passed=3, failed=1, flaky=2
   - flakiness_rate=2/6≈33%  -> **is_flaky=True**  (flaky detection)
   - failure_rate=1/6≈17%, recent_failure_rate=1/5=20% -> is_failing=False
   - last_status=**passed** with a prior ``failed`` -> **fail->pass**
     (time order: failed, flaky, flaky, passed, passed, passed)

The failed run carries a ``failure`` row (``automation_defect`` — locator
drift after a test-id refactor, the S6.5 defect story) so the S4.1 diagnosis
shape is exercised too.

Idempotent: re-running skips rows that already exist (keyed on the
project + ``e2e/demo.spec.js`` and the per-test-case result count >= 6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from qa_copilot_api.config import get_settings
from qa_copilot_api.db import make_app_engine
from qa_copilot_domain.enums import (
    FailureCategory,
    GeneratedTestStatus,
    RunStatus,
    TestResultStatus,
)
from qa_copilot_repository import models
from sqlalchemy import func, select
from sqlalchemy.orm import Session

DEMO_PROJECT = "f500a3b2-04a3-4cd2-9eaf-87f7c39c98fe"
LOGIN_TC = "3cfe1127-ccf9-4dce-9fe5-c241193468e2"
LOGIN_REQ = "cf4b4237-bbba-4b93-9cf3-0420b3a3649f"
DEV_USER = "04ef22b0-d1c4-422c-b2b7-899776dc4efa"

REPO = Path(r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app")
SPEC_REL = "e2e/demo.spec.js"

#: (status, days_ago, duration_s) — time-ordered oldest -> newest.
HISTORY: list[tuple[TestResultStatus, int, float]] = [
    (TestResultStatus.FAILED, 6, 12.4),
    (TestResultStatus.FLAKY, 5, 18.9),
    (TestResultStatus.FLAKY, 4, 17.2),
    (TestResultStatus.PASSED, 2, 14.1),
    (TestResultStatus.PASSED, 1, 13.6),
]


def main() -> int:
    settings = get_settings()
    engine = make_app_engine(settings.database_url)
    now = datetime.now(UTC).replace(microsecond=0)

    with Session(engine) as db:
        project = db.get(models.Project, DEMO_PROJECT)
        tc = db.get(models.TestCase, LOGIN_TC)
        if project is None or tc is None:
            raise SystemExit(f"demo project/test-case not found: {project} / {tc}")

        # 1) requirement -> test case join (must exist for the S6.2 context).
        join = db.scalar(
            select(models.RequirementTestCase).where(
                models.RequirementTestCase.requirement_id == LOGIN_REQ,
                models.RequirementTestCase.test_case_id == LOGIN_TC,
            )
        )
        if join is None:
            db.add(models.RequirementTestCase(requirement_id=LOGIN_REQ, test_case_id=LOGIN_TC))
            print("joined requirement -> login test case")

        # 2) applied generated test for e2e/demo.spec.js (S2.4 terminal state).
        existing = db.scalar(
            select(models.GeneratedTest).where(
                models.GeneratedTest.project_id == DEMO_PROJECT,
                models.GeneratedTest.file_path == SPEC_REL,
            )
        )
        if existing is None:
            content = (REPO / SPEC_REL).read_text(encoding="utf-8")
            db.add(
                models.GeneratedTest(
                    id=str(uuid.uuid4()),
                    project_id=DEMO_PROJECT,
                    test_case_id=LOGIN_TC,
                    file_path=SPEC_REL,
                    language="javascript",
                    framework="playwright",
                    content=content,
                    notes=["S2.3 automation for 'Login succeeds with valid email and password'"],
                    repository_path=str(REPO),
                    status=GeneratedTestStatus.APPLIED,
                    reviewed_by=DEV_USER,
                    reviewed_at=now - timedelta(days=7),
                    review_note=(
                        "Approved: covers the login happy path with the current test-id contract."
                    ),
                )
            )
            print("created applied generated_tests row for e2e/demo.spec.js")
        else:
            print(f"generated test already present: {existing.id}")

        # 3) run history for the login test case (idempotent on count).
        linked = (
            db.scalar(
                select(func.count(models.TestResult.id)).where(
                    models.TestResult.test_case_id == LOGIN_TC
                )
            )
            or 0
        )
        if linked >= 6:
            print(f"run history already seeded ({linked} linked results) - skipping")
        else:
            for position, (status, days_ago, duration) in enumerate(HISTORY):
                started = now - timedelta(days=days_ago, minutes=17)
                run = models.TestRun(
                    id=str(uuid.uuid4()),
                    project_id=DEMO_PROJECT,
                    commit_sha=f"s65seed0{position + 1}",
                    status=RunStatus.COMPLETED,
                    started_at=started,
                    completed_at=started + timedelta(seconds=int(duration) + 2),
                    created_at=started,
                )
                db.add(run)
                result = models.TestResult(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    test_case_id=LOGIN_TC,
                    status=status,
                    duration=duration,
                )
                db.add(result)
                if status is TestResultStatus.FAILED:
                    failure = models.Failure(
                        id=str(uuid.uuid4()),
                        test_result_id=result.id,
                        category=FailureCategory.AUTOMATION_DEFECT,
                        root_cause=(
                            "Locator drift: the login submit locator "
                            "[data-testid=login-submit] resolved to 0 elements after the "
                            "test-id refactor renamed the attribute; the automation was "
                            "written against the pre-refactor contract."
                        ),
                        confidence=0.86,
                        evidence=[
                            (
                                "console.jsonl: locator '[data-testid=login-submit]' "
                                "resolved to 0 elements"
                            ),
                            (
                                "screenshot: login form rendered; submit button present "
                                "under the new test id"
                            ),
                        ],
                        suggested_fix=(
                            "Point the login submit locator at the current data-testid "
                            "contract (client/src/testids.js) and keep the spec in step "
                            "with the test-id rename."
                        ),
                        needs_human_approval=True,
                    )
                    db.add(failure)
                    result.failure_id = failure.id
                print(
                    f"seeded run: {status.value:8s} started={started.isoformat()} dur={duration}s"
                )

        db.commit()
        linked_after = db.scalar(
            select(func.count(models.TestResult.id)).where(
                models.TestResult.test_case_id == LOGIN_TC
            )
        )
        print(f"done: {linked_after} linked test_results for the login test case")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
