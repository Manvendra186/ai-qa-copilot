#!/usr/bin/env python3
"""S8.6 LIVE commercialization pilot (build bible §S8.6) — RBAC + member pipeline
execution + owner-only Jira linking + S8.4 plan quotas + S8.3 audit trail.

A live end-to-end pilot against the REAL API (real Postgres + Redis + LM Studio),
not a mocked unit test. It proves the commercialization surface end-to-end:

  * **RBAC / membership** — a deterministic owner and a member (added to the
    owner's org) both authenticate; the member can drive the pipeline but is
    refused (403) on the owner-only Jira-link and plan-update routes.
  * **Member pipeline execution** — the member dispatches a real
    ``POST /requirements/analyze`` (Requirement Job Agent, LM Studio Qwen3-27B)
    and streams the job to a terminal state (202 + ``job_id`` + ``Location``).
  * **Owner-only Jira linking** — the owner links a seeded failure to a (fake)
    Jira issue (202 + ``job_id``), the ``jira.issue`` SSE event carries the key,
    and ``failures.jira_issue_key`` is persisted; the member's identical request
    is refused (403).
  * **S8.4 plan quotas** — on the ``free`` plan the member is denied, in the
    locked order (``concurrent_jobs`` → ``runs_per_month`` →
    ``tokens_per_month``), each with the structured ``409 plan_limit`` body
    (``plan``/``limit``/``used``/``allowed`` + ``org_id``/``project_id``/
    ``job_id``), exactly one ``org.quota.denied`` audit row, and no completed
    job / no new ``ai_sessions``.
  * **Owner-only plan bumps** — the owner moves ``free -> pro -> enterprise``
    (``PATCH /organizations/{id}``), each reflected on ``GET .../plan`` +
    ``GET .../usage`` with an ``org.plan.updated`` audit row; the member is
    refused (403).
  * **S8.3 audit trail** — the exported ``GET .../audit`` carries the
    ``org.quota.denied`` / ``org.plan.updated`` rows for the pilot.

The pilot world is isolated: a per-run owner + member, a dedicated org +
project, a seeded failure/diagnosis (for the Jira leg), and a fake Jira server
(the real ``JiraClient`` routes at it, S6.5/S7.5 pattern). Quota usage is
derived from live DB state (S8.4), so each denial leg seeds exactly the
violated limit and dispatches a real request.

Run (from the repo root, Postgres + Redis + LM Studio up):

    python scripts/_s86_live.py

Exit code is 0 on green, 1 on any failed check. The report is written to
``reports/commercialization_v1.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

# --- Paths / ports -------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

API_HOST = "127.0.0.1"
API_PORT = 8000
API_BASE = f"http://{API_HOST}:{API_PORT}/api/v1"

# --- Isolated pilot world (deterministic within the run) -----------------------
# Per-run unique identities keep re-runs clean (no "email already exists" 400)
# while the report captures the exact identities used.
RUN_ID = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
OWNER_EMAIL = f"s86.owner.{RUN_ID}@pilot.local"
MEMBER_EMAIL = f"s86.member.{RUN_ID}@pilot.local"
PASSWORD = "S86-Pilot-Passw0rd!"
ORG_NAME = f"S8.6 Pilot {RUN_ID}"
PROJECT_NAME = f"S8.6 Pilot Project {RUN_ID}"

# --- Fake Jira (owner-only Jira leg; the real JiraClient routes at it) ---------
FAKE_JIRA_HOST = "127.0.0.1"
FAKE_JIRA_PORT = 8346
FAKE_JIRA_BASE = f"http://{FAKE_JIRA_HOST}:{FAKE_JIRA_PORT}"
JIRA_TOKEN = "s86-fake-jira-token"
JIRA_TOKEN_REF = "S86_JIRA_TOKEN"
JIRA_PROJECT_KEY = "QA"
JIRA_FIRST_KEY = "QA-1"

# --- Locked S8.4 plan catalog (free / pro) ------------------------------------
FREE = {
    "name": "free",
    "max_projects": 1,
    "runs_per_month": 25,
    "tokens_per_month": 100_000,
    "concurrent_jobs": 1,
}
PRO = {
    "name": "pro",
    "max_projects": 10,
    "runs_per_month": 500,
    "tokens_per_month": 2_000_000,
    "concurrent_jobs": 5,
}

REPORT_PATH = REPO_ROOT / "reports" / "commercialization_v1.json"
API_LOG_PATH = REPO_ROOT / "logs" / "api_s86.log"

# Terminal SSE events (build bible §11): domain results + the universal job end.
TERMINAL_EVENTS = {
    "job.completed",
    "job.failed",
    "job.cancelled",
    "run.result",
    "regression.set",
    "jira.issue",
}
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}

ANALYZE_CONTENT = (
    "The login form must validate the email format, rate-limit repeated attempts, "
    "and lock the account for 15 minutes after five consecutive failures."
)


class _FakeJiraState:
    """In-memory issue store + counter shared by the fake Jira handler."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.issues: dict[str, dict[str, Any]] = {}
        self.counter: int = 0

    def reset(self) -> None:
        with self.lock:
            self.issues.clear()
            self.counter = 0


_fake_jira_state = _FakeJiraState()


class _FakeJiraHandler(BaseHTTPRequestHandler):
    """Serves exactly the S7.4 ``JiraClient`` calls the ``jira_link`` job makes.

    Implements the Jira REST v2 issue surface over an in-memory store:
    ``POST /rest/api/2/issue`` (create, new key ``QA-<n>``),
    ``PUT /rest/api/2/issue/{key}`` (update in place, 404 if unknown) and
    ``GET /rest/api/2/issue/{key}`` (fetch, 404 if unknown). State is reset by
    :meth:`FakeJiraServer.start`, so a fresh server hands out ``QA-1`` first.
    Bearer auth is checked (``JIRA_TOKEN``) so the auth/redaction path is
    exercised too.
    """

    def _send_json(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {JIRA_TOKEN}"

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _issue_body(key: str, fields: dict[str, Any]) -> dict[str, Any]:
        return {
            "key": key,
            "id": f"10000{key.rsplit('-', 1)[-1]}",
            "self": f"{FAKE_JIRA_BASE}/rest/api/2/issue/{key}",
            "fields": {
                "summary": fields.get("summary"),
                "status": {"name": "To Do"},
                "project": {"key": JIRA_PROJECT_KEY},
                "labels": fields.get("labels"),
            },
        }

    @staticmethod
    def _issue_key(path: str) -> str | None:
        prefix = "/rest/api/2/issue/"
        if not path.startswith(prefix):
            return None
        key = path[len(prefix) :]
        return key or None

    def do_POST(self) -> None:  # noqa: N802 (http.server contract)
        if not self._auth_ok():
            self._send_json(401, {"errorMessages": ["Bad credentials"]})
            return
        path = self.path.split("?", 1)[0]
        if path != "/rest/api/2/issue":
            self._send_json(404, {"errorMessages": ["Not Found"]})
            return
        fields = self._read_body()
        with _fake_jira_state.lock:
            _fake_jira_state.counter += 1
            key = f"QA-{_fake_jira_state.counter}"
            _fake_jira_state.issues[key] = dict(fields)
        self._send_json(201, self._issue_body(key, fields))

    def do_PUT(self) -> None:  # noqa: N802 (http.server contract)
        if not self._auth_ok():
            self._send_json(401, {"errorMessages": ["Bad credentials"]})
            return
        key = self._issue_key(self.path.split("?", 1)[0])
        if key is None or key not in _fake_jira_state.issues:
            self._send_json(404, {"errorMessages": ["Issue Does Not Exist"]})
            return
        fields = self._read_body()
        with _fake_jira_state.lock:
            _fake_jira_state.issues[key] = dict(fields)
        self._send_json(200, self._issue_body(key, fields))

    def do_GET(self) -> None:  # noqa: N802 (http.server contract)
        if not self._auth_ok():
            self._send_json(401, {"errorMessages": ["Bad credentials"]})
            return
        key = self._issue_key(self.path.split("?", 1)[0])
        if key is None or key not in _fake_jira_state.issues:
            self._send_json(404, {"errorMessages": ["Issue Does Not Exist"]})
            return
        with _fake_jira_state.lock:
            fields = dict(_fake_jira_state.issues[key])
        self._send_json(200, self._issue_body(key, fields))

    def log_message(self, *args: object) -> None:  # noqa: ARG002 (quiet)
        pass


class FakeJiraServer:
    """A :class:`ThreadingHTTPServer` on 127.0.0.1 serving the fake Jira."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer((FAKE_JIRA_HOST, FAKE_JIRA_PORT), _FakeJiraHandler)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        _fake_jira_state.reset()  # first create is deterministically QA-1
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        finally:
            self._server = None  # type: ignore[assignment]


class Check:
    """One named assertion with its expected/actual evidence (S6.5 pattern)."""

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


def stream_job(
    headers: dict[str, str], job_id: str, *, timeout_s: float = 300.0
) -> list[tuple[str, dict[str, Any]]]:
    """Read a job's SSE event stream (``GET /events?job_id=...``) to a terminal event.

    A *fresh* client is used for the streaming read: the shared client's
    connection pool can keep a keep-alive socket whose buffered bytes interleave
    with the SSE frames, and the S7.5 driver hit exactly that. The stream is read
    until a terminal event (build bible §11) or the overall timeout.
    """
    url = f"{API_BASE}/events?job_id={job_id}"
    events: list[tuple[str, dict[str, Any]]] = []
    with httpx.Client(timeout=timeout_s) as stream_client:
        with stream_client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            name: str | None = None
            data_lines: list[str] = []
            for line in response.iter_lines():
                if not line:
                    if name is not None:
                        raw = "\n".join(data_lines)
                        try:
                            payload: dict[str, Any] = json.loads(raw) if raw else {}
                        except ValueError:
                            payload = {"raw": raw}
                        events.append((name, payload))
                        if name in TERMINAL_EVENTS:
                            break
                    name, data_lines = None, []
                    continue
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
    return events


def wait_terminal(
    client: httpx.Client, headers: dict[str, str], job_id: str, *, timeout_s: float = 300.0
) -> dict[str, Any]:
    """Poll ``GET /jobs/{id}`` until the job reaches a terminal status (or timeout).

    The source of truth for the job outcome — independent of the exact terminal
    SSE event name — so a member-pipeline leg is robust even if the LLM agent's
    result event name differs from the run/regression ones.
    """
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get(f"/jobs/{job_id}", headers=headers).json()
        if last.get("status") in TERMINAL_JOB_STATUSES:
            return last
        time.sleep(1.0)
    return last


# --- Auth (register / login / add member) -------------------------------------
def register(
    client: httpx.Client, checks: Check, email: str, *, org_name: str | None = None
) -> dict[str, Any]:
    """``POST /auth/register`` — account + owned workspace (S8.1)."""
    body: dict[str, Any] = {"email": email, "password": PASSWORD}
    if org_name is not None:
        body["organization_name"] = org_name
    resp = client.post("/auth/register", json=body)
    checks.add(f"register {email}", 201, resp.status_code, resp.status_code == 201)
    if resp.status_code != 201:
        return {}
    data = resp.json()
    checks.add(
        f"register {email} -> user + owned org",
        "user.email + organization.id",
        (data.get("user", {}).get("email"), bool(data.get("organization", {}).get("id"))),
        data.get("user", {}).get("email") == email and bool(data.get("organization", {}).get("id")),
    )
    return data


def login(
    client: httpx.Client,
    checks: Check,
    email: str,
    *,
    password: str = PASSWORD,
    expect_status: int = 200,
) -> tuple[int, dict[str, Any]]:
    """``POST /auth/login`` — Bearer token + org memberships (S8.1)."""
    resp = client.post("/auth/login", json={"email": email, "password": password})
    checks.add(f"login {email}", expect_status, resp.status_code, resp.status_code == expect_status)
    data: dict[str, Any] = {}
    if resp.status_code in (200, 401):
        try:
            data = resp.json()
        except ValueError:
            data = {}
    if expect_status == 200 and resp.status_code == 200:
        checks.add(
            f"login {email} -> token + organizations",
            "token + 1 organization",
            (bool(data.get("token")), len(data.get("organizations", []))),
            bool(data.get("token")) and len(data.get("organizations", [])) >= 1,
        )
    return resp.status_code, data


def me(client: httpx.Client, headers: dict[str, str], checks: Check, who: str) -> dict[str, Any]:
    """``GET /auth/me`` — who I am + where I have roles (S8.1)."""
    resp = client.get("/auth/me", headers=headers)
    checks.add(f"me ({who})", 200, resp.status_code, resp.status_code == 200)
    data: dict[str, Any] = {}
    if resp.status_code == 200:
        data = resp.json()
        checks.add(
            f"me ({who}) -> user + organizations",
            "user.email + organizations list",
            (data.get("user", {}).get("email"), isinstance(data.get("organizations"), list)),
            data.get("user", {}).get("email") is not None
            and isinstance(data.get("organizations"), list),
        )
    return data


def add_member(
    client: httpx.Client, headers: dict[str, str], org_id: str, checks: Check
) -> dict[str, Any]:
    """``POST /organizations/{id}/members`` — owner promotes an existing account."""
    resp = client.post(
        f"/organizations/{org_id}/members",
        json={"email": MEMBER_EMAIL, "role": "member"},
        headers=headers,
    )
    checks.add("owner adds member", 201, resp.status_code, resp.status_code == 201)
    data: dict[str, Any] = {}
    if resp.status_code == 201:
        data = resp.json()
        checks.add(
            "owner adds member -> member row",
            (MEMBER_EMAIL, "member"),
            (data.get("email"), data.get("role")),
            data.get("email") == MEMBER_EMAIL and data.get("role") == "member",
        )
    return data


def roster(
    client: httpx.Client, headers: dict[str, str], org_id: str, checks: Check
) -> list[dict[str, Any]]:
    """``GET /organizations/{id}/members`` — the member roster (any member)."""
    resp = client.get(f"/organizations/{org_id}/members", headers=headers)
    checks.add("member reads roster", 200, resp.status_code, resp.status_code == 200)
    rows = resp.json() if resp.status_code == 200 else []
    if resp.status_code == 200:
        roles = sorted(row.get("role", "") for row in rows)
        checks.add(
            "roster has owner + member",
            ["member", "owner"],
            roles,
            roles == ["member", "owner"],
        )
    return rows


# --- World seeding (DB, S7.5 pattern) ------------------------------------------
def _repo_src_paths() -> list[str]:
    return [
        str(REPO_ROOT / "apps" / "api" / "src"),
        str(REPO_ROOT / "packages" / "domain" / "src"),
        str(REPO_ROOT / "packages" / "repository" / "src"),
    ]


def _db_session():
    """A fresh ``Session`` bound to the pilot's Postgres (the S7.5 pattern)."""
    sys.path.insert(0, os.pathsep.join(_repo_src_paths()))
    from qa_copilot_api.config import get_settings  # noqa: PLC0415
    from qa_copilot_api.db import make_app_engine  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    engine = make_app_engine(get_settings().database_url)
    return Session(engine)


def _seed_world(owner_email: str, member_email: str) -> dict[str, str]:
    """Seed the pilot project + memberships + failure chain + Jira integration.

    Registration creates users + orgs but no project (there is no public
    ``POST /projects``), so the pilot project is seeded directly in the DB —
    the S7.5 world pattern. Also pins ``organizations.plan = 'free'`` so the
    quota legs start from the locked S8.4 free caps.
    """
    from qa_copilot_domain.enums import (  # noqa: PLC0415
        FailureCategory,
        OrgRole,
        ProjectRole,
        RunStatus,
        TestResultStatus,
        TestType,
    )
    from qa_copilot_repository import models  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    session = _db_session()
    try:
        now = datetime.now(UTC)
        owner = session.scalar(select(models.User).where(models.User.email == owner_email))
        member = session.scalar(select(models.User).where(models.User.email == member_email))
        if owner is None or member is None:
            raise RuntimeError(
                f"registered users missing (owner={owner_email}, member={member_email})"
            )
        org = session.scalar(
            select(models.Organization)
            .join(
                models.OrganizationMember,
                models.OrganizationMember.organization_id == models.Organization.id,
            )
            .where(
                models.OrganizationMember.user_id == owner.id,
                models.OrganizationMember.role == OrgRole.OWNER,
            )
        )
        if org is None:
            raise RuntimeError("owner's organization not found")
        org.plan = "free"  # the locked S8.4 quota legs start at the free caps

        project = models.Project(organization_id=org.id, name=PROJECT_NAME)
        session.add(project)
        session.flush()
        session.add(
            models.ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.OWNER)
        )
        session.add(
            models.ProjectMember(project_id=project.id, user_id=member.id, role=ProjectRole.MEMBER)
        )

        # Failure chain for the Jira leg: test case -> completed run -> failed
        # result -> S4.1 diagnosis (category / root cause / suggested fix).
        test_case = models.TestCase(
            title="Login rate-limit lockout",
            type=TestType.FUNCTIONAL,
            preconditions=["A registered account exists."],
            steps=["Enter a wrong password five consecutive times."],
            expected_results=["The account is locked for 15 minutes."],
        )
        run = models.TestRun(
            project_id=project.id,
            status=RunStatus.COMPLETED,
            started_at=now,
            completed_at=now,
        )
        session.add_all([test_case, run])
        session.flush()
        result = models.TestResult(
            run_id=run.id,
            test_case_id=test_case.id,
            status=TestResultStatus.FAILED,
            duration=1.25,
        )
        session.add(result)
        # ``result.id`` is a flush-time Python default (``_new_id``); the S7.5
        # seed pattern flushes the result first so the failure's ``test_result_id``
        # links to a real id (otherwise it is stored NULL and the failure is
        # unreachable from the project — the Jira 404).
        session.flush()
        failure = models.Failure(
            test_result_id=result.id,
            category=FailureCategory.PRODUCT_DEFECT,
            root_cause=(
                "The login endpoint does not lock the account after five consecutive failures."
            ),
            confidence=0.87,
            evidence=[
                "5 failed attempts observed in the same minute",
                "account remained usable afterwards",
            ],
            suggested_fix="Enforce a 15-minute lockout after five consecutive failed logins.",
            needs_human_approval=True,
            jira_issue_key=None,
        )
        session.add_all([result, failure])
        # The real JiraClient routes at the fake server (S7.5 pattern):
        # ``token_ref`` names the env var the API process must export.
        session.add(
            models.IntegrationConfig(
                project_id=project.id,
                provider="jira",
                base_url=FAKE_JIRA_BASE,
                token_ref=JIRA_TOKEN_REF,
                enabled=True,
            )
        )
        session.commit()
        return {"org_id": org.id, "project_id": project.id, "failure_id": failure.id}
    finally:
        session.close()


def _seed_active_job(project_id: str) -> str:
    """One in-flight job row — the free plan allows 1 concurrent job."""
    from qa_copilot_domain.enums import JobStatus, JobType  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415

    session = _db_session()
    try:
        job = models.Job(
            project_id=project_id,
            type=JobType.REQUIREMENT_ANALYSIS,
            status=JobStatus.RUNNING,
            progress=0.42,
        )
        session.add(job)
        session.commit()
        return job.id
    finally:
        session.close()


def _clear_active_jobs(project_id: str) -> None:
    """Park any in-flight seeded jobs (the concurrent slot is released)."""
    from qa_copilot_domain.enums import JobStatus  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415
    from sqlalchemy import update  # noqa: PLC0415

    session = _db_session()
    try:
        session.execute(
            update(models.Job)
            .where(
                models.Job.project_id == project_id,
                models.Job.status.in_((JobStatus.PENDING, JobStatus.RUNNING)),
            )
            .values(status=JobStatus.FAILED)
        )
        session.commit()
    finally:
        session.close()


def _seed_runs(project_id: str, count: int) -> None:
    """``count`` executed runs this month — the free plan allows 25."""
    from qa_copilot_domain.enums import RunStatus  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415

    session = _db_session()
    try:
        now = datetime.now(UTC)
        for _ in range(count):
            session.add(
                models.TestRun(
                    project_id=project_id,
                    status=RunStatus.COMPLETED,
                    started_at=now,
                    completed_at=now,
                )
            )
        session.commit()
    finally:
        session.close()


def _seed_tokens(project_id: str, total_tokens: int) -> None:
    """A session + action carrying ``total_tokens`` this month (free: 100k)."""
    from uuid import uuid4  # noqa: PLC0415

    from qa_copilot_repository import models  # noqa: PLC0415

    session = _db_session()
    try:
        session_id = str(uuid4())
        session.add(
            models.AISession(
                id=session_id,
                project_id=project_id,
                task_type="requirement_analysis",
                status="completed",
            )
        )
        session.add(
            models.AIAction(
                session_id=session_id,
                agent="requirement_job_agent",
                model="Qwen3-27B",
                tokens_in=total_tokens // 2,
                tokens_out=total_tokens - total_tokens // 2,
                latency_ms=4200,
            )
        )
        session.commit()
    finally:
        session.close()


def _count_ai_actions(org_id: str) -> int:
    """The org's ``ai_actions`` rows this month (the token meter's source)."""
    from qa_copilot_repository import billing, models  # noqa: PLC0415
    from sqlalchemy import func, select  # noqa: PLC0415

    session = _db_session()
    try:
        n = session.scalar(
            select(func.count())
            .select_from(models.AIAction)
            .join(models.AISession, models.AIAction.session_id == models.AISession.id)
            .join(models.Project, models.AISession.project_id == models.Project.id)
            .where(
                models.Project.organization_id == org_id,
                models.AIAction.created_at >= billing.month_start(),
            )
        )
        return int(n or 0)
    finally:
        session.close()


def _readback_jira_key(failure_id: str) -> str | None:
    """The failure's persisted ``jira_issue_key`` (the S7.4 read-back)."""
    from qa_copilot_repository import models  # noqa: PLC0415

    session = _db_session()
    try:
        failure = session.get(models.Failure, failure_id)
        return failure.jira_issue_key if failure is not None else None
    finally:
        session.close()


# --- Pilot legs ----------------------------------------------------------------
def _analyze_body(project_id: str) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "title": "Login rate-limit lockout",
        "content": ANALYZE_CONTENT,
        "acceptance_criteria": [
            "The account is locked for 15 minutes after five consecutive failed logins.",
            "A successful login before the fifth failure does not start a lockout.",
        ],
    }


def dispatch_analyze(
    client: httpx.Client,
    headers: dict[str, str],
    project_id: str,
    checks: Check,
    who: str,
    expect_status: int = 202,
) -> tuple[int, dict[str, Any]]:
    """``POST /requirements/analyze`` — the analyze dispatch (202 + job)."""
    resp = client.post("/requirements/analyze", json=_analyze_body(project_id), headers=headers)
    checks.add(
        f"analyze dispatch ({who})",
        expect_status,
        resp.status_code,
        resp.status_code == expect_status,
    )
    data: dict[str, Any] = {}
    if resp.status_code in (202, 409):
        try:
            data = resp.json()
        except ValueError:
            data = {}
    if expect_status == 202 and resp.status_code == 202:
        job_id = str(data.get("job_id") or data.get("id") or "")
        checks.add(
            "analyze dispatch -> job",
            "job_id + pending status",
            (job_id[:8] if job_id else None, data.get("status")),
            bool(job_id) and data.get("status") in ("pending", "running"),
        )
    return resp.status_code, data


def run_member_pipeline(
    client: httpx.Client, member_headers: dict[str, str], project_id: str, checks: Check
) -> None:
    """Member analyze leg: dispatch -> terminal -> completed + artifact ref."""
    status, body = dispatch_analyze(client, member_headers, project_id, checks, "member")
    if status != 202:
        return
    job_id = str(body.get("job_id") or body.get("id") or "")
    final = wait_terminal(client, member_headers, job_id)
    checks.add(
        "member pipeline reaches terminal",
        "completed",
        final.get("status"),
        final.get("status") == "completed",
    )
    checks.add(
        "member pipeline -> output_ref (artifact)",
        "output_ref present",
        bool(final.get("output_ref")),
        bool(final.get("output_ref")),
    )
    events = stream_job(member_headers, job_id)
    names = [name for name, _ in events]
    checks.add(
        "SSE stream replays to terminal (job.completed)",
        "job.completed in stream",
        names[-5:],
        "job.completed" in names,
    )


def run_jira_leg(
    client: httpx.Client,
    owner_headers: dict[str, str],
    member_headers: dict[str, str],
    project_id: str,
    failure_id: str,
    checks: Check,
) -> dict[str, Any]:
    """Jira linkage leg: member denied (403), owner links (QA-1 at FakeJira)."""
    info: dict[str, Any] = {}
    url = f"/projects/{project_id}/failures/{failure_id}/jira"
    body = {"project_key": "QA"}

    resp = client.post(url, headers=member_headers, json=body)
    checks.add("jira link as member", 403, resp.status_code, resp.status_code == 403)
    if resp.status_code == 403:
        info["member_denied"] = True

    resp = client.post(url, headers=owner_headers, json=body)
    checks.add("jira link as owner", 202, resp.status_code, resp.status_code == 202)
    job_id = ""
    if resp.status_code == 202:
        body = resp.json()
        job_id = str(body.get("job_id") or body.get("id") or "")
        checks.add("jira link -> job", "job_id present", bool(job_id), bool(job_id))
    if not job_id:
        return info
    info["job_id"] = job_id

    final = wait_terminal(client, owner_headers, job_id)
    checks.add(
        "jira job completes", "completed", final.get("status"), final.get("status") == "completed"
    )
    if final.get("status") != "completed":
        return info

    events = stream_job(owner_headers, job_id)
    jira_events = [
        payload
        for name, payload in events
        if name == "jira.issue" or payload.get("action") in ("created", "linked")
    ]
    # The ``jira.issue`` payload is flat (``action`` / ``key`` / ``url`` /
    # ``project_key``, §19 S7.4) — the key is ``payload["key"]``.
    issue_key = next(
        (payload.get("key") for payload in jira_events if payload.get("key")),
        None,
    )
    checks.add(
        "SSE stream carries the jira.issue event",
        "jira.issue with issue.key",
        [name for name, _ in events][-4:],
        issue_key is not None,
    )
    checks.add("jira.issue key == QA-1 (FakeJira)", "QA-1", issue_key, issue_key == "QA-1")

    persisted = _readback_jira_key(failure_id)
    checks.add("failure.jira_issue_key persisted", "QA-1", persisted, persisted == "QA-1")
    # ``_fake_jira_state.issues`` is keyed by the issue key (``{"QA-1": fields}``);
    # the stored value is the raw Jira ``fields`` body (``project``/``description``
    # live directly in it — there is no nested ``key`` field).
    with _fake_jira_state.lock:
        fake_keys = sorted(_fake_jira_state.issues)
        stored_issue = dict(_fake_jira_state.issues.get("QA-1", {}))
    checks.add(
        "FakeJira stored the issue",
        "QA-1 in fake state",
        fake_keys,
        "QA-1" in fake_keys,
    )
    if "QA-1" in fake_keys:
        project_ref = stored_issue.get("project")
        project_key = project_ref.get("key") if isinstance(project_ref, dict) else project_ref
        checks.add(
            "issue body carries the diagnosis + project key",
            "description + project=QA",
            (bool(stored_issue.get("description")), project_key),
            bool(stored_issue.get("description")) and project_key == "QA",
        )
    info["issue_key"] = issue_key
    return info


def _assert_plan_limit(
    checks: Check,
    body: dict[str, Any],
    who: str,
    *,
    limit: str,
    used: int,
    allowed: int,
    org_id: str,
    project_id: str,
    expect_job: bool,
) -> None:
    """The locked S8.4 wire contract on every 409 ``plan_limit`` response."""
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    checks.add(
        f"409 detail.code ({who})",
        "plan_limit",
        detail.get("code"),
        detail.get("code") == "plan_limit",
    )
    checks.add(f"409 detail.plan ({who})", "free", detail.get("plan"), detail.get("plan") == "free")
    checks.add(
        f"409 detail.limit ({who})", limit, detail.get("limit"), detail.get("limit") == limit
    )
    used_actual = detail.get("used")
    checks.add(
        f"409 detail.used ({who})",
        f">= {used}",
        used_actual,
        isinstance(used_actual, int) and used_actual >= used,
    )
    checks.add(
        f"409 detail.allowed ({who})",
        allowed,
        detail.get("allowed"),
        detail.get("allowed") == allowed,
    )
    checks.add(
        f"409 detail.org/project ({who})",
        (org_id, project_id),
        (detail.get("org_id"), detail.get("project_id")),
        detail.get("org_id") == org_id and detail.get("project_id") == project_id,
    )
    checks.add(
        f"409 detail.job_id ({who})",
        "job_id present (denied job was registered)" if expect_job else "no job_id",
        bool(detail.get("job_id")),
        bool(detail.get("job_id")) == expect_job,
    )


def quota_legs(
    client: httpx.Client,
    member_headers: dict[str, str],
    org_id: str,
    project_id: str,
    checks: Check,
) -> dict[str, Any]:
    """The three locked free-plan caps: concurrent jobs, runs, tokens (S8.4)."""
    info: dict[str, Any] = {}
    baseline_actions = _count_ai_actions(org_id)

    # 1. Concurrent jobs: one in-flight seeded job fills the free slot.
    _seed_active_job(project_id)
    status, body = dispatch_analyze(
        client, member_headers, project_id, checks, "member (concurrent cap)", expect_status=409
    )
    if status == 409:
        _assert_plan_limit(
            checks,
            body,
            "concurrent",
            limit="concurrent_jobs",
            used=1,
            allowed=1,
            org_id=org_id,
            project_id=project_id,
            expect_job=True,
        )
        info["concurrent_denied"] = True
        denied_job = str(body.get("detail", {}).get("job_id") or "")
        probe = client.get(f"/jobs/{denied_job}", headers=member_headers) if denied_job else None
        checks.add(
            "denied job was not persisted (no 202 job row)",
            404,
            probe.status_code if probe is not None else "n/a",
            probe is None or probe.status_code == 404,
        )
    _clear_active_jobs(project_id)

    # 2. Runs per month: 25 seeded runs fill the free cap.
    _seed_runs(project_id, 25)
    resp = client.post(
        f"/projects/{project_id}/runs",
        json={"repository_path": "/tmp/s86-pilot", "tests": ["tests/example.spec.ts"]},
        headers=member_headers,
    )
    checks.add(
        "runs dispatch (member, runs cap)",
        409,
        resp.status_code,
        resp.status_code == 409,
    )
    if resp.status_code == 409:
        body = resp.json()
        _assert_plan_limit(
            checks,
            body,
            "runs",
            limit="runs_per_month",
            used=25,
            allowed=25,
            org_id=org_id,
            project_id=project_id,
            expect_job=True,
        )
        info["runs_denied"] = True
        denied_job = str(body.get("detail", {}).get("job_id") or "")
        if denied_job:
            probe = client.get(f"/jobs/{denied_job}", headers=member_headers)
            checks.add(
                "denied run job was deleted at the gate",
                404,
                probe.status_code,
                probe.status_code == 404,
            )

    # 3. Tokens per month: 100k seeded tokens fill the free cap.
    _seed_tokens(project_id, 100_000)
    status, body = dispatch_analyze(
        client, member_headers, project_id, checks, "member (token cap)", expect_status=409
    )
    if status == 409:
        _assert_plan_limit(
            checks,
            body,
            "tokens",
            limit="tokens_per_month",
            used=100_000,
            allowed=100_000,
            org_id=org_id,
            project_id=project_id,
            expect_job=True,
        )
        info["tokens_denied"] = True
        denied_job = str(body.get("detail", {}).get("job_id") or "")
        if denied_job:
            probe = client.get(f"/jobs/{denied_job}", headers=member_headers)
            checks.add(
                "denied token job was deleted at the gate",
                404,
                probe.status_code,
                probe.status_code == 404,
            )

    # Quota denial is free and side-effect-free: the only ai_action added
    # across all three legs is the single token seed; the two rejected
    # analyze dispatches (concurrent + tokens) are deleted at the gate before
    # any AI work runs, so they add no metering rows.
    after_actions = _count_ai_actions(org_id)
    delta = after_actions - baseline_actions
    checks.add(
        "rejected dispatches added no ai_actions (only the token seed did)",
        1,
        delta,
        delta == 1,
    )
    info["baseline_ai_actions"] = baseline_actions
    info["after_ai_actions"] = after_actions
    return info


def plan_bump_leg(
    client: httpx.Client,
    owner_headers: dict[str, str],
    member_headers: dict[str, str],
    org_id: str,
    checks: Check,
) -> dict[str, Any]:
    """S8.2: members read the plan, only the owner can change it."""
    info: dict[str, Any] = {}
    plan_url = f"/organizations/{org_id}/plan"  # GET — any member
    org_url = f"/organizations/{org_id}"  # PATCH — owner only

    resp = client.get(plan_url, headers=member_headers)
    checks.add("member reads plan (free)", 200, resp.status_code, resp.status_code == 200)
    if resp.status_code == 200:
        data = resp.json()
        info["free_limits"] = data.get("limits")
        checks.add(
            "free plan caps are the locked S8.4 caps",
            {
                "max_projects": 1,
                "concurrent_jobs": 1,
                "runs_per_month": 25,
                "tokens_per_month": 100000,
            },
            data.get("limits"),
            data.get("name") == "free"
            and data.get("limits", {}).get("concurrent_jobs") == 1
            and data.get("limits", {}).get("runs_per_month") == 25
            and data.get("limits", {}).get("tokens_per_month") == 100000,
        )

    resp = client.patch(org_url, json={"plan": "pro"}, headers=member_headers)
    checks.add("member changes plan (denied)", 403, resp.status_code, resp.status_code == 403)
    if resp.status_code == 403:
        info["member_denied"] = True

    resp = client.patch(org_url, json={"plan": "pro"}, headers=owner_headers)
    checks.add("owner bumps free -> pro", 200, resp.status_code, resp.status_code == 200)
    if resp.status_code == 200:
        data = resp.json()
        info["pro_limits"] = data.get("limits")
        checks.add(
            "pro plan caps",
            {
                "max_projects": 10,
                "concurrent_jobs": 5,
                "runs_per_month": 500,
                "tokens_per_month": 2000000,
            },
            data.get("limits"),
            data.get("name") == "pro"
            and data.get("limits", {}).get("concurrent_jobs") == 5
            and data.get("limits", {}).get("runs_per_month") == 500
            and data.get("limits", {}).get("tokens_per_month") == 2000000,
        )

    resp = client.patch(org_url, json={"plan": "enterprise"}, headers=owner_headers)
    checks.add("owner bumps pro -> enterprise", 200, resp.status_code, resp.status_code == 200)
    if resp.status_code == 200:
        data = resp.json()
        info["enterprise_limits"] = data.get("limits")
        limits = data.get("limits", {})
        checks.add(
            "enterprise plan caps (unlimited)",
            "all caps large",
            {k: limits.get(k) for k in ("concurrent_jobs", "runs_per_month", "tokens_per_month")},
            data.get("name") == "enterprise"
            and limits.get("concurrent_jobs", 0) >= 100
            and limits.get("runs_per_month", 0) >= 100000
            and limits.get("tokens_per_month", 0) >= 100000000,
        )

    resp = client.get(plan_url, headers=member_headers)
    checks.add("member re-reads plan (enterprise)", 200, resp.status_code, resp.status_code == 200)
    if resp.status_code == 200:
        checks.add(
            "plan is enterprise after bumps",
            "enterprise",
            resp.json().get("name"),
            resp.json().get("name") == "enterprise",
        )

    # Usage (S8.3): the monthly rollup reflects the seeded world.
    resp = client.get(f"/organizations/{org_id}/usage", headers=member_headers)
    checks.add("member reads usage", 200, resp.status_code, resp.status_code == 200)
    if resp.status_code == 200:
        usage = resp.json()
        info["usage"] = usage
        month = datetime.now(UTC).strftime("%Y-%m")
        checks.add(
            "usage month is the current month",
            month,
            usage.get("month"),
            usage.get("month") == month,
        )
        checks.add(
            "usage counts the seeded runs + tokens",
            "runs >= 25, tokens >= 100000, projects == 1",
            (usage.get("runs"), usage.get("tokens"), usage.get("projects")),
            (usage.get("runs") or 0) >= 25
            and (usage.get("tokens") or 0) >= 100000
            and (usage.get("projects") or 0) == 1,
        )
    return info


def audit_leg(
    client: httpx.Client,
    owner_headers: dict[str, str],
    member_headers: dict[str, str],
    org_id: str,
    checks: Check,
) -> dict[str, Any]:
    """S8.5: owner-only audit export with every expected event type."""
    info: dict[str, Any] = {}
    resp = client.get(f"/organizations/{org_id}/audit", headers=member_headers)
    checks.add("member reads audit (denied)", 403, resp.status_code, resp.status_code == 403)

    resp = client.get(f"/organizations/{org_id}/audit", headers=owner_headers)
    checks.add("owner reads audit", 200, resp.status_code, resp.status_code == 200)
    if resp.status_code != 200:
        return info
    rows = resp.json()
    info["audit_count"] = len(rows)
    by_action: dict[str, int] = {}
    for row in rows:
        by_action[row.get("action")] = by_action.get(row.get("action"), 0) + 1
    info["by_action"] = by_action

    checks.add(
        "audit: 3 org.quota.denied rows (S8.4 denials)",
        3,
        by_action.get("org.quota.denied"),
        by_action.get("org.quota.denied") == 3,
    )
    checks.add(
        "audit: 2 org.plan.updated rows (free->pro, pro->enterprise)",
        2,
        by_action.get("org.plan.updated"),
        by_action.get("org.plan.updated") == 2,
    )
    checks.add(
        "audit: 1 org.membership.add row",
        1,
        by_action.get("org.membership.add"),
        by_action.get("org.membership.add") == 1,
    )
    checks.add(
        "audit: >= 2 org.gate.denied rows (plan PATCH, audit GET)",
        ">= 2",
        by_action.get("org.gate.denied"),
        (by_action.get("org.gate.denied") or 0) >= 2,
    )

    denied_rows = [row for row in rows if row.get("action") == "org.quota.denied"]
    checks.add(
        "quota.denied rows carry outcome=denied",
        "denied",
        sorted({row.get("outcome") for row in denied_rows}),
        denied_rows and all(row.get("outcome") == "denied" for row in denied_rows),
    )
    plan_rows = [row for row in rows if row.get("action") == "org.plan.updated"]
    checks.add(
        "plan.updated rows carry outcome=success",
        "success",
        sorted({row.get("outcome") for row in plan_rows}),
        plan_rows and all(row.get("outcome") == "success" for row in plan_rows),
    )

    stamps = [row.get("at") for row in rows if row.get("at")]
    checks.add(
        "audit rows are newest-first",
        "non-increasing timestamps",
        stamps[:2],
        bool(stamps) and all(stamps[i] >= stamps[i + 1] for i in range(len(stamps) - 1)),
    )
    return info


# --- API lifecycle (S7.5 pattern) ---------------------------------------------
def _api_ready() -> bool:
    try:
        return httpx.get(f"http://{API_HOST}:{API_PORT}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


_api_log_file: object | None = None  # keep a ref so the child's stdout handle isn't GC'd


def _read_log_tail(path: Path, chars: int = 2000) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-chars:].decode("utf-8", "replace")


def _start_api(env: dict[str, str]) -> subprocess.Popen[str]:
    # Log the API to a file, NOT an undrained pipe: the driver never reads a
    # stdout pipe, so once its buffer fills the API blocks on write() and its
    # event loop freezes. A file has no backpressure (S7.5 lesson).
    global _api_log_file
    API_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _api_log_file = open(API_LOG_PATH, "a", encoding="utf-8", buffering=1)
    cmd = [
        str(VENV_PYTHON),
        "-m",
        "uvicorn",
        "qa_copilot_api.main:app",
        "--host",
        API_HOST,
        "--port",
        str(API_PORT),
    ]
    return subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=_api_log_file,
        stderr=subprocess.STDOUT,
    )


def _wait_for_api(proc: subprocess.Popen[str], timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _api_ready():
            return
        if proc.poll() is not None:
            raise RuntimeError(
                f"API subprocess exited early — tail of {API_LOG_PATH}:\n"
                f"{_read_log_tail(API_LOG_PATH)}"
            )
        time.sleep(0.25)
    raise RuntimeError(f"API did not become ready in {timeout_s}s")


# --- Report --------------------------------------------------------------------
def _build_report(
    checks: Check,
    world: dict[str, Any],
    auth_info: dict[str, Any],
    pipeline_info: dict[str, Any],
    jira_info: dict[str, Any],
    quota_info: dict[str, Any],
    plan_info: dict[str, Any],
    audit_info: dict[str, Any],
) -> dict[str, Any]:
    passed = sum(1 for c in checks.items if c["passed"])
    return {
        "schema_version": "commercialization-v1/1",
        "step": "S8.6",
        "title": (
            "Live commercialization pilot: auth + org/project + analyze + "
            "Jira link + quotas + audit"
        ),
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "run_id": RUN_ID,
            "owner_email": OWNER_EMAIL,
            "member_email": MEMBER_EMAIL,
            "org_name": ORG_NAME,
            "project_name": PROJECT_NAME,
            "org_id": world.get("org_id"),
            "project_id": world.get("project_id"),
            "failure_id": world.get("failure_id"),
            "fake_jira_base": FAKE_JIRA_BASE,
            "api_base": API_BASE,
            "plan_catalog": {"free": FREE, "pro": PRO, "enterprise": "unlimited caps"},
        },
        "auth": auth_info,
        "pipeline": pipeline_info,
        "jira_leg": jira_info,
        "quota_legs": quota_info,
        "plan_leg": plan_info,
        "audit_leg": audit_info,
        "checks": checks.items,
        "summary": {
            "total": len(checks.items),
            "passed": passed,
            "failed": len(checks.failed),
            "status": "pass" if not checks.failed else "fail",
        },
    }


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    checks = Check()
    fake_jira = FakeJiraServer()
    api_proc: subprocess.Popen[str] | None = None
    api_we_started = False

    env = dict(os.environ)
    env[JIRA_TOKEN_REF] = JIRA_TOKEN  # the real JiraClient resolves the token from this env var

    world: dict[str, Any] = {}
    auth_info: dict[str, Any] = {}
    pipeline_info: dict[str, Any] = {}
    jira_info: dict[str, Any] = {}
    quota_info: dict[str, Any] = {}
    plan_info: dict[str, Any] = {}
    audit_info: dict[str, Any] = {}

    print("== S8.6 live commercialization pilot (auth + pipeline + Jira + quotas + audit) ==")
    try:
        fake_jira.start()
        print(f"fake Jira up at {FAKE_JIRA_BASE}")

        if _api_ready():
            print("reusing already-running API on :8000")
        else:
            api_proc = _start_api(env)
            api_we_started = True
            _wait_for_api(api_proc, 90)
        print("API ready on :8000")

        with httpx.Client(base_url=API_BASE, timeout=60) as client:
            # --- Auth bootstrap -------------------------------------------------
            owner_reg = register(client, checks, OWNER_EMAIL, org_name=ORG_NAME)
            org_id = str(owner_reg.get("organization", {}).get("id") or "")
            if not org_id:
                raise RuntimeError("registration did not return an organization id")
            register(
                client, checks, MEMBER_EMAIL, org_name=f"{MEMBER_EMAIL.split('@')[0]} Personal"
            )
            dup = client.post("/auth/register", json={"email": OWNER_EMAIL, "password": PASSWORD})
            checks.add(
                "duplicate register (same email)", 409, dup.status_code, dup.status_code == 409
            )
            wrong = client.post(
                "/auth/login", json={"email": OWNER_EMAIL, "password": "wrong-password-123"}
            )
            checks.add(
                "login with wrong password", 401, wrong.status_code, wrong.status_code == 401
            )

            _, owner_body = login(client, checks, OWNER_EMAIL)
            owner_headers = {"Authorization": f"Bearer {owner_body.get('token')}"}
            _, member_body = login(client, checks, MEMBER_EMAIL)
            member_headers = {"Authorization": f"Bearer {member_body.get('token')}"}

            owner_me = me(client, owner_headers, checks, "owner")
            member_me = me(client, member_headers, checks, "member (own org)")
            checks.add(
                "member starts with only their own org",
                1,
                len(member_me.get("organizations", [])),
                len(member_me.get("organizations", [])) == 1,
            )

            add_member(client, owner_headers, org_id, checks)
            member_me2 = me(client, member_headers, checks, "member (after add)")
            pilot_orgs = [o for o in member_me2.get("organizations", []) if o.get("id") == org_id]
            checks.add(
                "member now sees the pilot org as member",
                (org_id, "member"),
                (pilot_orgs[0].get("id"), pilot_orgs[0].get("role")) if pilot_orgs else None,
                bool(pilot_orgs)
                and pilot_orgs[0].get("id") == org_id
                and pilot_orgs[0].get("role") == "member",
            )
            roster(client, member_headers, org_id, checks)
            auth_info = {
                "owner_orgs_after_register": len(owner_me.get("organizations", [])),
                "member_orgs_after_add": len(member_me2.get("organizations", [])),
                "org_id": org_id,
            }

            # --- World seeding (project + memberships + failure chain) ----------
            world = _seed_world(OWNER_EMAIL, MEMBER_EMAIL)
            project_id = world["project_id"]
            failure_id = world["failure_id"]
            print(
                f"world seeded: org={org_id[:8]} project={project_id[:8]} failure={failure_id[:8]}"
            )

            # --- Member pipeline (analyze -> completed + SSE terminal) ---------
            run_member_pipeline(client, member_headers, project_id, checks)
            pipeline_info = {"note": "member analyze leg; see checks"}

            # --- Owner-only Jira linkage (member 403 / owner 202 / QA-1) -------
            jira_info = run_jira_leg(
                client, owner_headers, member_headers, project_id, failure_id, checks
            )

            # --- S8.4 quota denial legs (free caps, locked order) --------------
            quota_info = quota_legs(client, member_headers, org_id, project_id, checks)

            # --- S8.2/S8.3 plan bumps + usage (owner-only changes) -------------
            plan_info = plan_bump_leg(client, owner_headers, member_headers, org_id, checks)

            # --- S8.5 audit export (owner-only) --------------------------------
            audit_info = audit_leg(client, owner_headers, member_headers, org_id, checks)

        report = _build_report(
            checks, world, auth_info, pipeline_info, jira_info, quota_info, plan_info, audit_info
        )
        _write_report(report)
        print(f"report written to {REPORT_PATH}")

        if checks.failed:
            print(f"\n{len(checks.failed)} check(s) FAILED — see report")
            return 1
        print("\nS8.6 live commercialization pilot GREEN")
        return 0
    finally:
        if api_proc is not None and api_we_started:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api_proc.kill()
        fake_jira.stop()


if __name__ == "__main__":
    sys.exit(main())
