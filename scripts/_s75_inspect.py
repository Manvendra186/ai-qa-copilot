"""S7.5: inspect the demo project's webhook-relevant config (read-only)."""

from __future__ import annotations

import os
import sys
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
from qa_copilot_repository.history import (  # noqa: E402
    TestRiskInput,
    compute_test_stats,
    project_test_history,
    rank_tests,
    strongest_impact_kind,
)
from qa_copilot_repository.impact import applied_generated_refs, impact_from_session  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

PROJECT_ID = "f500a3b2-04a3-4cd2-9eaf-87f7c39c98fe"
DEMO_APP = r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app"
CHANGED = ["e2e/demo.spec.js", "e2e/fixtures.js"]


def main() -> None:
    engine = make_app_engine(get_settings().database_url)
    with Session(engine) as db:
        print("=== applied_generated_refs ===")
        refs = applied_generated_refs(db, PROJECT_ID)
        for r in refs:
            print(
                f"  file_path={r.file_path!r} test_case_id={r.test_case_id!r} "
                f"requirement_ids={r.requirement_ids!r}"
            )

        print(f"=== impact (changed={CHANGED!r}) ===")
        impact = impact_from_session(db, PROJECT_ID, DEMO_APP, CHANGED)
        print(f"  changed={impact.changed!r}")
        for imp in impact.impacted:
            kinds = sorted(k.value for k in imp.kinds)
            tcs = sorted(imp.test_case_ids)
            reqs = sorted(imp.requirement_ids)
            print(
                f"  impacted: path={imp.path!r} kinds={kinds!r} "
                f"test_case_ids={tcs!r} requirement_ids={reqs!r}"
            )

        print("=== project_test_history ===")
        history = project_test_history(db, PROJECT_ID)
        for test_case_id, outcomes in history.items():
            stats = compute_test_stats(test_case_id, outcomes)
            print(
                f"  test_case={test_case_id!r} outcomes={len(outcomes)!r} "
                f"last={stats.last_status.value if stats.last_status else None!r} "
                f"is_flaky={stats.is_flaky!r} is_failing={stats.is_failing!r} "
                f"flakiness={round(stats.flakiness_rate, 3)!r}"
            )

        print("=== rank_tests (S6.2) ===")
        inputs: list[TestRiskInput] = []
        for imp in impact.impacted:
            test_case_ids = sorted(imp.test_case_ids)
            outcomes = tuple(o for tcid in test_case_ids for o in history.get(tcid, ()))
            inputs.append(
                TestRiskInput(
                    test_key=imp.path,
                    outcomes=outcomes,
                    impact_kind=strongest_impact_kind(imp.kinds) if imp.kinds else None,
                )
            )
        ranked = rank_tests(inputs)
        for r in ranked:
            print(f"  {r.test_key!r} score={r.risk_score:.3f} signals={r.signals!r}")


if __name__ == "__main__":
    main()
