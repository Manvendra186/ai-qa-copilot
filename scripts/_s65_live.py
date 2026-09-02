"""S6.5 live E2E: analyze (202 → SSE regression.set) → run (202 → SSE run.result).

Runs against the live API on :8000 (LM Studio :8080, demo server :4000, demo
client :5174) with the deterministic S6.5 DB state seeded by
``scripts/_s65_seed.py``. Flow (build bible §19 S6.5):

1. login → token (dev account, Demo App project membership);
2. ``POST /projects/{id}/regression/analyze`` with the change set
   ``[e2e/fixtures.js, e2e/demo.spec.js]`` → **202 + job_id** + ``Location``;
3. stream ``GET /events?job_id=...`` → ``regression.set`` payload with the
   S6.1 impact set (``direct`` + ``generated`` + ``referenced`` on
   ``e2e/demo.spec.js``), the S6.2 ranking (login test: **is_flaky=True**,
   **last_status=passed** after a prior ``failed`` → fail→pass), the
   S6.3 top-N recommendation, and the S6.5 advisor brief (LLM or stub);
4. the job row lands ``completed`` with output_ref ``regression://<project>``;
5. ``POST /projects/{id}/runs`` with the recommended test → **202 + job_id**;
6. stream SSE → ``run.result`` (total=1, passed=1, failed=0) + ``job.completed``;
7. ``GET /runs/{id}`` → status completed, the passed result and §15 artifacts;
8. write the full evidence to ``reports/regression_v1.json``.

Exits 0 when every assertion holds, non-zero otherwise.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
EMAIL = "dev@local.dev"
PASSWORD = "dev-password"
REPO = r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app"
CHANGED_FILES = ["e2e/fixtures.js", "e2e/demo.spec.js"]
TEST_FILE = "e2e/demo.spec.js"

LOGIN_TC = "3cfe1127-ccf9-4dce-9fe5-c241193468e2"
LOGIN_REQ = "cf4b4237-bbba-4b93-9cf3-0420b3a3649f"

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = REPO_ROOT / "reports" / "regression_v1.json"

TERMINAL_EVENTS = ("job.completed", "job.failed", "job.cancelled")


class Check:
    """One named assertion with its expected/actual evidence."""

    def __init__(self) -> None:
        self.items: list[dict[str, object]] = []

    def add(self, name: str, expected: object, actual: object, passed: bool) -> None:
        self.items.append(
            {"name": name, "expected": expected, "actual": actual, "passed": bool(passed)}
        )
        marker = "ok  " if passed else "FAIL"
        print(f"  [{marker}] {name}: expected={expected!r} actual={actual!r}")

    @property
    def failed(self) -> list[dict[str, object]]:
        return [item for item in self.items if not item["passed"]]


def run_analyze(
    client: httpx.Client, headers: dict[str, str], project_id: str, checks: Check
) -> dict[str, object]:
    """S6.4 analyze endpoint → SSE regression.set, with the S6.5 assertions."""
    body = {"repository_path": REPO, "files": CHANGED_FILES, "top_n": 10}
    res = client.post(f"/projects/{project_id}/regression/analyze", json=body, headers=headers)
    checks.add(
        "analyze.http_status", 202, res.status_code, res.status_code == 202
    )
    if res.status_code != 202:
        raise RuntimeError(f"analyze rejected: {res.text[:300]}")
    job = res.json()
    job_id: str = job["job_id"]
    location = res.headers.get("Location", "")
    checks.add("analyze.location", f"/jobs/{job_id}", location, f"/jobs/{job_id}" in location)
    print(f"analyze 202 OK: job_id={job_id} Location={location}")

    events = stream_job(headers, job_id, timeout_s=600)
    set_events = [d for n, d in events if n == "regression.set"]
    checks.add("analyze.regression_set_events", 1, len(set_events), len(set_events) == 1)
    reg = set_events[0] if set_events else {}
    recommendation = reg.get("recommendation") or {}
    impacted = next(
        (imp for imp in (reg.get("impact") or {}).get("impacted") or [] if imp.get("path") == TEST_FILE),
        {},
    )
    recs = recommendation.get("recommendations") or []
    top = recs[0] if recs else {}
    stats = top.get("stats") or {}

    # S6.3: the impacted test is recommended at rank 1.
    checks.add("rec.top_test_key", TEST_FILE, top.get("test_key"), top.get("test_key") == TEST_FILE)
    checks.add("rec.top_rank", 1, top.get("rank"), top.get("rank") == 1)
    # S6.1: all three impact kinds on the applied generated test file.
    kinds = sorted(impacted.get("kinds") or [])
    checks.add(
        "impact.kinds",
        ["direct", "generated", "referenced"],
        kinds,
        set(["direct", "generated", "referenced"]) <= set(kinds),
    )
    # S6.1 provenance: test case + requirement links ride along.
    checks.add(
        "impact.test_case_ids", LOGIN_TC, impacted.get("test_case_ids"),
        LOGIN_TC in (impacted.get("test_case_ids") or []),
    )
    checks.add(
        "impact.requirement_ids", LOGIN_REQ, impacted.get("requirement_ids"),
        LOGIN_REQ in (impacted.get("requirement_ids") or []),
    )
    # S6.2: flaky detection on the seeded history.
    checks.add("stats.is_flaky", True, stats.get("is_flaky"), stats.get("is_flaky") is True)
    checks.add(
        "stats.flakiness_rate>=0.25", ">= 0.25", stats.get("flakiness_rate"),
        float(stats.get("flakiness_rate") or 0) >= 0.25,
    )
    checks.add(
        "stats.executions>=min_sample(3)", ">= 3", stats.get("executions"),
        int(stats.get("executions") or 0) >= 3,
    )
    # S6.2: fail→pass — a prior failure, not currently failing (last run passed,
    # verified against the DB in main()).
    checks.add("stats.failed>=1 (prior failure)", ">= 1", stats.get("failed"),
               int(stats.get("failed") or 0) >= 1)
    checks.add("stats.is_failing", False, stats.get("is_failing"), stats.get("is_failing") is False)
    # Policy echo (defaults: 3 / 5 / 0.25 / 0.50).
    checks.add(
        "policy.echo",
        {"min_sample": 3, "recent_window": 5, "flaky_threshold": 0.25, "failing_threshold": 0.5},
        {k: recommendation.get(k) for k in ("min_sample", "recent_window", "flaky_threshold", "failing_threshold")},
        recommendation.get("min_sample") == 3 and recommendation.get("recent_window") == 5
        and recommendation.get("flaky_threshold") == 0.25
        and recommendation.get("failing_threshold") == 0.5,
    )
    # S6.5 advisor brief: present and sourced (LLM or safe stub).
    advice = reg.get("advice") or {}
    checks.add("advice.summary", "non-empty", (advice.get("summary") or "")[:80],
               bool(str(advice.get("summary") or "").strip()))
    checks.add("advice.source", ("llm", "stub"), advice.get("source"),
               advice.get("source") in ("llm", "stub"))
    # Deterministic rationale trail present on the top test.
    checks.add("rec.rationale", "non-empty list", top.get("rationale"),
               isinstance(top.get("rationale"), list) and len(top["rationale"]) > 0)

    # Job row: completed + stable regression:// output_ref.
    time.sleep(0.5)
    res = client.get(f"/jobs/{job_id}", headers=headers)
    res.raise_for_status()
    job_row = res.json()
    checks.add("job_row.status", "completed", job_row.get("status"), job_row.get("status") == "completed")
    expected_ref = f"regression://{project_id}"
    checks.add("job_row.output_ref", expected_ref, job_row.get("output_ref"),
               job_row.get("output_ref") == expected_ref)

    return {
        "request": {"method": "POST", "path": f"/projects/{project_id}/regression/analyze", "body": body},
        "http": {"status": 202, "headers": {"location": location}},
        "job_id": job_id,
        "job_row": job_row,
        "sse_events": events,
        "regression_set": reg,
    }


def login(client: httpx.Client, checks: Check) -> tuple[str, dict[str, str]]:
    """Login → (demo project id, auth headers)."""
    res = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    res.raise_for_status()
    login = res.json()
    projects = login.get("projects") or []
    if not projects:
        raise RuntimeError("logged in but no project membership — run scripts/seed.py")
    demo = next((p for p in projects if p.get("name") == "Demo App"), projects[0])
    project_id: str = demo["id"]
    checks.add("login.project", "Demo App membership", f"{demo['name']} {demo['role']}", True)
    print(f"login OK: {login['user']['email']} -> project {demo['name']} ({project_id})")
    return project_id, {"Authorization": f"Bearer {login['token']}"}


def stream_job(headers: dict[str, str], job_id: str, timeout_s: float) -> list[tuple[str, dict]]:
    """Consume the job's SSE feed until the terminal frame (replay-safe)."""
    events: list[tuple[str, dict]] = []
    with httpx.Client(base_url=BASE, timeout=timeout_s) as stream_client:
        with stream_client.stream("GET", f"/events?job_id={job_id}", headers=headers) as stream:
            event: str | None = None
            data_lines: list[str] = []
            for line in stream.iter_lines():
                if line == "":
                    if event is not None:
                        payload = json.loads("\n".join(data_lines)) if data_lines else {}
                        events.append((event, payload))
                        print(f"  sse {event}: {json.dumps(payload)[:160]}")
                        if event in TERMINAL_EVENTS:
                            break
                    event, data_lines = None, []
                elif line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").strip())
    terminal = events[-1][0] if events else "<none>"
    if terminal not in TERMINAL_EVENTS:
        raise RuntimeError(f"stream ended without a terminal event (last={terminal})")
    if terminal != "job.completed":
        raise RuntimeError(f"job ended with {terminal}: {events[-1][1].get('error')}")
    return events


def run_regression(
    client: httpx.Client, headers: dict[str, str], project_id: str, checks: Check
) -> dict[str, object]:
    """POST /projects/{id}/runs → SSE run.result → GET /runs/{id}, asserted."""
    body = {"repository_path": REPO, "tests": [TEST_FILE], "timeout_s": 600}
    res = client.post(f"/projects/{project_id}/runs", json=body, headers=headers)
    checks.add("run.http_status", 202, res.status_code, res.status_code == 202)
    if res.status_code != 202:
        raise RuntimeError(f"run rejected: {res.text[:300]}")
    job = res.json()
    job_id: str = job["job_id"]
    print(f"run 202 OK: job_id={job_id}")

    events = stream_job(headers, job_id, timeout_s=900)
    result_events = [d for n, d in events if n == "run.result"]
    checks.add("run.run_result_events", 1, len(result_events), len(result_events) == 1)
    result = result_events[0] if result_events else {}
    totals = result.get("totals") or {}
    checks.add("run.result.status", "completed", result.get("status"), result.get("status") == "completed")
    checks.add("totals.total", 1, totals.get("total"), totals.get("total") == 1)
    checks.add("totals.passed", 1, totals.get("passed"), totals.get("passed") == 1)
    checks.add("totals.failed", 0, totals.get("failed"), totals.get("failed") == 0)

    run_id: str = result.get("run_id") or ""
    checks.add("run.run_id", "non-empty", run_id, bool(run_id))

    time.sleep(0.5)
    res = client.get(f"/jobs/{job_id}", headers=headers)
    res.raise_for_status()
    job_row = res.json()
    checks.add("run.job_row.status", "completed", job_row.get("status"), job_row.get("status") == "completed")
    checks.add("run.job_row.output_ref", run_id, job_row.get("output_ref"),
               job_row.get("output_ref") == run_id)

    res = client.get(f"/runs/{run_id}", headers=headers)
    res.raise_for_status()
    detail = res.json()
    checks.add("run_detail.status", "completed", detail.get("status"), detail.get("status") == "completed")
    checks.add("run_detail.totals", {"total": 1, "passed": 1, "failed": 0}, detail.get("totals"),
               (detail.get("totals") or {}).get("total") == 1
               and (detail.get("totals") or {}).get("passed") == 1
               and (detail.get("totals") or {}).get("failed") == 0)
    results = detail.get("results") or []
    checks.add("run_detail.results", 1, len(results), len(results) == 1)
    if results:
        checks.add("run_detail.result.status", "passed", results[0].get("status"),
                   results[0].get("status") == "passed")
    artifacts = detail.get("artifacts") or []
    checks.add("run_detail.artifacts", ">= 1", len(artifacts), len(artifacts) >= 1)
    kinds = sorted({a.get("type") for a in artifacts if a.get("type")})
    print(f"  artifacts: {kinds}")

    return {
        "request": {"method": "POST", "path": f"/projects/{project_id}/runs", "body": body},
        "job_id": job_id,
        "job_row": job_row,
        "sse_events": events,
        "run_result": result,
        "run_detail": detail,
    }


def db_history_snapshot(checks: Check, run_id: str) -> dict[str, object]:
    """S6.5 fail→pass evidence straight from the persisted history (§10 rows).

    The S6.2 stats in ``regression.set`` already prove ``failed >= 1`` and
    ``is_failing == false``; this snapshot closes the loop on *last status*:
    the login test case's newest execution is ``passed`` and at least one
    ``failed`` precedes it — the seeded fail→pass story. Also confirms the
    live run row persisted as ``completed``.
    """
    from qa_copilot_api.config import get_settings
    from qa_copilot_api.db import make_app_engine
    from qa_copilot_repository import models
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    settings = get_settings()
    engine = make_app_engine(settings.database_url)
    with Session(engine) as db:
        rows = db.execute(
            select(models.TestResult.status)
            .join(models.TestRun, models.TestRun.id == models.TestResult.run_id)
            .where(models.TestResult.test_case_id == LOGIN_TC)
            .order_by(func.coalesce(models.TestRun.started_at, models.TestRun.created_at).asc())
        ).scalars()
        statuses = [s.value if hasattr(s, "value") else str(s) for s in rows]
        failed_before_last = statuses.count("failed") if statuses else 0
        checks.add(
            "db.login_tc.statuses_in_time_order",
            "failed, flaky, flaky, passed, passed, passed",
            statuses,
            statuses == ["failed", "flaky", "flaky", "passed", "passed", "passed"],
        )
        checks.add(
            "db.login_tc.last_status", "passed", statuses[-1] if statuses else None,
            bool(statuses) and statuses[-1] == "passed",
        )
        checks.add(
            "db.login_tc.prior_failed>=1", ">= 1", failed_before_last,
            failed_before_last >= 1,
        )
        live = db.get(models.TestRun, run_id)
        live_status = live.status if live is None else live.status
        checks.add(
            "db.live_run.status", "completed",
            live_status.value if live is not None and hasattr(live_status, "value") else live_status,
            live is not None and str(live_status) == "completed",
        )
    return {
        "login_test_case_id": LOGIN_TC,
        "login_requirement_id": LOGIN_REQ,
        "statuses_in_time_order": statuses,
        "note": (
            "fail→pass: a prior 'failed' execution (with an S4.1 automation-defect "
            "diagnosis) precedes the last 'passed' — and the live S6.4 re-run also passed."
        ),
        "live_run_id": run_id,
    }


def _sse_evidence(events: list[tuple[str, dict]]) -> list[dict[str, object]]:
    """SSE events for the report: full payloads for the contract events,
    truncated otherwise (progress ticks are noise)."""
    out: list[dict[str, object]] = []
    for name, payload in events:
        if name in ("regression.set", "run.result", "job.completed", "job.failed", "job.cancelled"):
            out.append({"event": name, "payload": payload})
        else:
            text = json.dumps(payload)
            out.append({"event": name, "payload": payload if len(text) < 240 else text[:240] + "…"})
    return out


def write_report(
    project_id: str,
    checks: Check,
    analyze: dict[str, object],
    run: dict[str, object],
    precondition: dict[str, object],
    db_evidence: dict[str, object],
) -> dict[str, object]:
    """Assemble + persist reports/regression_v1.json (the S6.5 live evidence)."""
    analyze_clean = {
        "request": analyze["request"],
        "http": analyze["http"],
        "job_id": analyze["job_id"],
        "job_row": analyze["job_row"],
        "sse_events": _sse_evidence(analyze["sse_events"]),
        "regression_set": analyze["regression_set"],
    }
    run_clean = {
        "request": run["request"],
        "job_id": run["job_id"],
        "job_row": run["job_row"],
        "sse_events": _sse_evidence(run["sse_events"]),
        "run_result": run["run_result"],
        "run_detail": run["run_detail"],
    }
    passed = not checks.failed
    report = {
        "schema_version": "s6.5-live-evidence/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": project_id,
        "environment": {
            "api": "http://127.0.0.1:8000/api/v1",
            "lm_studio": "http://127.0.0.1:8080 (OpenAI-compatible)",
            "demo_server": "http://localhost:4000",
            "demo_client": "http://localhost:5174",
            "app_under_test": "http://localhost:5174",
            "repository": REPO,
        },
        "precondition": precondition,
        "fail_to_pass": db_evidence,
        "analyze": analyze_clean,
        "run": run_clean,
        "assertions": checks.items,
        "failed_assertions": checks.failed,
        "result": {
            "passed": passed,
            "evidence": [
                "analyze → 202 → SSE regression.set with S6.1 impact (direct+generated+referenced)",
                "S6.2 flaky detection: is_flaky=true (flakiness_rate >= 0.25, >= min_sample)",
                "S6.2 fail->pass: prior failed execution, last_status=passed, is_failing=false",
                "S6.3 top-1 recommendation = the impacted generated test",
                "S6.5 advisor brief present (LLM or safe stub)",
                "run → 202 → SSE run.result → run completed: 1/1 passed, artifacts stored",
            ]
            if passed
            else [item["name"] for item in checks.failed],
        },
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nreport written: {REPORT_PATH}")
    return report


def main() -> int:
    checks = Check()
    client = httpx.Client(base_url=BASE, timeout=120)

    # Health pre-flight (fast, explicit).
    for name, url in (
        ("api", "http://127.0.0.1:8000/health"),
        ("demo_server", "http://localhost:4000/health"),
        ("demo_client", "http://localhost:5174/"),
    ):
        try:
            code = client.get(url, timeout=10).status_code
            print(f"health {name}: {code}")
        except Exception as exc:  # noqa: BLE001
            print(f"health {name}: ERROR {exc}")
            raise

    project_id, headers = login(client, checks)

    # Deterministic S6.5 precondition snapshot (the seeded history the
    # S6.2 ranking will read).
    precondition = {
        "seeded_history_note": (
            "6 linked executions for the login test case (1 pre-existing "
            "seeded passed run + 5 seeded: failed, flaky, flaky, passed, passed); "
            "one applied generated_tests row for e2e/demo.spec.js linked to the "
            "login test case and 'Login accepts valid credentials' requirement."
        ),
        "expected_s62_stats": {
            "executions": 6,
            "passed": 3,
            "failed": 1,
            "flaky": 2,
            "flakiness_rate": "2/6 ~ 0.333",
            "failure_rate": "1/6 ~ 0.167",
            "recent_failure_rate": "1/5 = 0.2",
            "is_flaky": True,
            "is_failing": False,
            "last_status": "passed",
        },
        "changed_files": CHANGED_FILES,
    }

    print("\n--- S6.5 analyze ---")
    analyze = run_analyze(client, headers, project_id, checks)

    print("\n--- S6.5 run (recommended regression test) ---")
    run = run_regression(client, headers, project_id, checks)

    print("\n--- S6.5 fail→pass DB evidence ---")
    db_evidence = db_history_snapshot(checks, str(run["run_result"].get("run_id") or ""))

    report = write_report(project_id, checks, analyze, run, precondition, db_evidence)

    print(f"\nassertions: {len(checks.items)} total, {len(checks.failed)} failed")
    if checks.failed:
        for item in checks.failed:
            print(f"  FAIL {item['name']}: expected={item['expected']!r} actual={item['actual']!r}")
        return 1
    print("\nS6.5 LIVE E2E OK — analyze + run evidence captured in reports/regression_v1.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

