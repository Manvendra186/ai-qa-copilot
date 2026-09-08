"""S7.5 live E2E baseline: webhook -> regression -> run -> Jira link.

Build bible §19 S7.5 (the "live E2E + baseline report" step), modelled on the
S6.5 live-evidence pair (``scripts/_s65_live.py``) but with the S7.3 webhook as
the entry point instead of a direct ``/regression/analyze`` call. This covers
the full webhook → regression → ranked-set → S3 run → **Jira-link** leg (the
S7.4 Jira leg was completed 2026-09-08 and is now driven live).

What is *live* (real code paths, no fakes of the code under test):
  * the S7.3 inbound webhook route (``POST /api/v1/webhooks/github``) with a
    genuine ``X-Hub-Signature-256`` HMAC over the raw payload bytes;
  * the S7.2 PR -> regression resolution, including the **real** S7.1
    ``GitHubClient`` HTTP call (the only fake is a local GitHub *server*, the
    S6.5 "local HTTP fixture" pattern — there is no real GitHub on this box);
  * the deterministic S6.1 impact + S6.2 stats + S6.3 ranking chain;
  * the S3 Playwright "run this set" execution (the demo app under test is
    auto-started by the demo project's Playwright ``webServer``);
  * the S7.4 failure -> Jira link (``POST /projects/{id}/failures/{fid}/jira``):
    the **real** S7.4 ``JiraClient`` create/update/fetch calls (the only fake is
    a local Jira *server*, the same S6.5 "local HTTP fixture" pattern) driving
    the real ``jira_link`` job + ``jira.issue`` SSE event, then a read-back that
    asserts ``failures.jira_issue_key`` and a re-link proving "updated, never
    duplicated".

Everything else is deterministic and offline. The driver is self-contained:
it starts its fake-GitHub and fake-Jira HTTP servers and the API subprocess
itself, waits for readiness, drives the flow over real HTTP, asserts the live
baseline, writes ``reports/integrations_v1.json``, and tears the subprocesses
down. Secrets are env-referenced only (S7.1 §17): the API resolves the PAT, the
webhook secret, and the Jira API token from the env vars named by the project's
``integration_configs.token_ref`` rows (``S75_FAKE_GH_TOKEN`` /
``S75_WEBHOOK_SECRET`` / ``S75_FAKE_JIRA_TOKEN``); nothing is stored.

Exits 0 when every assertion holds, non-zero otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

# --- the PR / project under test (demo app) ---------------------------------
OWNER = "example"
REPO = "app-under-test"
FULL_NAME = "example/app-under-test"
PR_NUMBER = 1
PR_TITLE = "Fix login selector drift"
TEST_FILE = "e2e/demo.spec.js"
# The PR's changed-file set (the S6.5 evidence pair): the applied generated
# test plus the fixtures module it imports. Both must be in the diff for the
# S6.1 impact core to mark the test ``direct`` + ``generated`` + ``referenced``
# (``referenced`` requires the test to import a *changed* source file).
CHANGED_FILES = ["e2e/fixtures.js", TEST_FILE]
PROJECT_ID = "f500a3b2-04a3-4cd2-9eaf-87f7c39c98fe"
REPO_PATH = r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app"

# --- local endpoints ---------------------------------------------------------
FAKE_GH_HOST = "127.0.0.1"
FAKE_GH_PORT = 8710
FAKE_GH_BASE = f"http://{FAKE_GH_HOST}:{FAKE_GH_PORT}"
FAKE_JIRA_HOST = "127.0.0.1"
FAKE_JIRA_PORT = 8711
FAKE_JIRA_BASE = f"http://{FAKE_JIRA_HOST}:{FAKE_JIRA_PORT}"
API_HOST = "127.0.0.1"
API_PORT = 8000
API_BASE = f"http://{API_HOST}:{API_PORT}/api/v1"

# --- env-referenced secrets (the values live only in the process env) --------
GH_TOKEN = "ghp_S75FakeToken0123456789"
WEBHOOK_SECRET = "whsec_S75FakeWebhookSecret0123456789"
GH_TOKEN_REF = "S75_FAKE_GH_TOKEN"
WEBHOOK_SECRET_REF = "S75_WEBHOOK_SECRET"
# S7.4 Jira-link leg: the fake Jira's Bearer token (loopback only, never stored)
# and the Jira project key the issue is filed under. ``JIRA_FIRST_KEY`` is the
# deterministic key a fresh fake Jira returns for the first ``create`` (QA-1).
JIRA_TOKEN = "ATATT7xS75FakeJiraToken1234567890"
JIRA_TOKEN_REF = "S75_FAKE_JIRA_TOKEN"
JIRA_PROJECT_KEY = "QA"
JIRA_FIRST_KEY = "QA-1"

EMAIL = "dev@local.dev"
PASSWORD = "dev-password"

REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
REPORT_PATH = REPO_ROOT / "reports" / "integrations_v1.json"

TERMINAL_EVENTS = ("job.completed", "job.failed", "job.cancelled")


# --- fake GitHub server (the S6.5 "local HTTP fixture" pattern) --------------
class _FakeGitHubHandler(BaseHTTPRequestHandler):
    """Serves exactly the S7.1 ``GitHubClient`` calls the regression job makes.

    The response bodies match ``packages/integrations/golden/github_v1.json``
    (the pinned contract): ``GET /repos/{o}/{r}`` for ``resolve_repository`` and
    ``GET /repos/{o}/{r}/pulls/{n}`` + ``/files`` for ``fetch_pull_request``.
    The PAT is checked (``Bearer``) so the redaction/auth path is exercised too.
    """

    def _send_json(self, status: int, body: object) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _auth_ok(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {GH_TOKEN}"

    def do_GET(self) -> None:  # noqa: N802 (http.server contract)
        path = self.path.split("?", 1)[0]
        if not self._auth_ok():
            self._send_json(401, {"message": "Bad credentials"})
            return
        if path == f"/repos/{OWNER}/{REPO}":
            self._send_json(
                200,
                {
                    "id": 7000,
                    "full_name": FULL_NAME,
                    "html_url": f"https://github.com/{FULL_NAME}",
                    "clone_url": f"https://github.com/{FULL_NAME}.git",
                    "default_branch": "main",
                    "private": False,
                },
            )
        elif path == f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}":
            self._send_json(
                200,
                {
                    "number": PR_NUMBER,
                    "title": PR_TITLE,
                    "state": "open",
                    "html_url": f"https://github.com/{FULL_NAME}/pull/{PR_NUMBER}",
                    "head": {"ref": "fix/login-selector", "sha": "a" * 40},
                    "base": {"ref": "main", "sha": "b" * 40},
                },
            )
        elif path == f"/repos/{OWNER}/{REPO}/pulls/{PR_NUMBER}/files":
            self._send_json(
                200,
                [{"filename": f, "status": "modified"} for f in CHANGED_FILES],
            )
        else:
            self._send_json(404, {"message": "Not Found"})

    def log_message(self, *args: object) -> None:  # noqa: ARG002 (quiet)
        pass


class FakeGitHubServer:
    """A :class:`ThreadingHTTPServer` on 127.0.0.1 serving the fake PR."""

    def __init__(self) -> None:
        self._server = ThreadingHTTPServer((FAKE_GH_HOST, FAKE_GH_PORT), _FakeGitHubHandler)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        finally:
            self._server = None  # type: ignore[assignment]


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
    :meth:`FakeJiraServer.start`, so a fresh server hands out ``QA-1`` first
    (the deterministic key the baseline asserts). Bearer auth is checked
    (``JIRA_TOKEN``) so the §17 auth/redaction path is exercised too.
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
        key = path[len(prefix):]
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


# --- SSE stream (S6.5 pattern) -----------------------------------------------
def stream_job(
    headers: dict[str, str], job_id: str, *, timeout_s: float
) -> list[tuple[str, dict[str, object]]]:
    """Consume ``GET /events?job_id=...`` until the terminal event.

    Uses a **fresh** ``httpx.Client`` for the long-lived SSE stream (the S6.5 /
    S5.5 live-driver pattern) instead of the driver's shared client: a new
    connection avoids the shared client's keep-alive/connection-reuse state that
    left this box's stream hanging (the job itself completed fine — only the
    shared-client stream never delivered the terminal frame).
    """
    url = f"/events?job_id={job_id}"
    events: list[tuple[str, dict[str, object]]] = []
    event: str | None = None
    data_lines: list[str] = []
    with httpx.Client(base_url=API_BASE, timeout=timeout_s) as stream_client:
        with stream_client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                line = raw.strip()
                if not line:
                    if event is not None and data_lines:
                        payload = json.loads("\n".join(data_lines))
                        events.append((event, payload))
                        print(f"  sse {event}: {json.dumps(payload)[:160]}")
                        if event in TERMINAL_EVENTS:
                            break
                    event, data_lines = None, []
                    continue
                if line.startswith(":"):
                    continue  # keepalive comment frame
                if line.startswith("event:"):
                    event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    data_lines.append(line.removeprefix("data:").strip())
    if event is not None and data_lines:
        events.append((event, json.loads("\n".join(data_lines))))
    terminal = events[-1][0] if events else "<none>"
    if terminal not in TERMINAL_EVENTS:
        raise RuntimeError(f"stream ended without a terminal event (last={terminal})")
    if terminal != "job.completed":
        raise RuntimeError(f"job ended with {terminal}: {events[-1][1].get('error')}")
    return events


# --- auth --------------------------------------------------------------------
def login(client: httpx.Client, checks: Check) -> dict[str, str]:
    """Login -> (demo project id check + auth headers)."""
    res = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    res.raise_for_status()
    login = res.json()
    projects = login.get("projects") or []
    demo = next((p for p in projects if p.get("name") == "Demo App"), projects[0] or {})
    checks.add("login.project", PROJECT_ID, demo.get("id"), demo.get("id") == PROJECT_ID)
    token = login.get("access_token") or login.get("token") or ""
    if not token:
        raise RuntimeError(f"login did not return a token: {login}")
    return {"Authorization": f"Bearer {token}"}


# --- signed webhook (the S7.3 entry point) -----------------------------------
def _sign(secret: str, body: bytes) -> str:
    import hashlib
    import hmac

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _webhook_payload() -> dict[str, object]:
    return {
        "action": "opened",
        "repository": {
            "full_name": FULL_NAME,
            "name": REPO,
            "owner": {"login": OWNER},
        },
        "pull_request": {
            "html_url": f"https://github.com/{FULL_NAME}/pull/{PR_NUMBER}",
            "title": PR_TITLE,
            "number": PR_NUMBER,
        },
    }


def deliver_webhook(
    client: httpx.Client,
    delivery_id: str,
    *,
    checks: Check,
    expect_status: int = 202,
    sign: bool = True,
) -> httpx.Response:
    """POST the signed (or unsigned) GitHub ``pull_request`` webhook."""
    payload = _webhook_payload()
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_id,
    }
    if sign:
        headers["X-Hub-Signature-256"] = _sign(WEBHOOK_SECRET, body)
    res = client.post("/webhooks/github", content=body, headers=headers)
    checks.add(
        "webhook.http_status",
        expect_status,
        res.status_code,
        res.status_code == expect_status,
    )
    return res


# --- regression leg (webhook -> SSE regression.set) ---------------------------
def run_regression(
    client: httpx.Client, headers: dict[str, str], job_id: str, *, checks: Check
) -> Any:
    """Consume the webhook job's SSE stream; assert the ranked set on the PR file."""
    events = stream_job(headers, job_id, timeout_s=300)
    set_events = [d for n, d in events if n == "regression.set"]
    checks.add("regression.set_events", 1, len(set_events), len(set_events) == 1)
    reg: Any = set_events[0] if set_events else {}
    recommendation: Any = reg.get("recommendation") or {}
    recs: list[Any] = recommendation.get("recommendations") or []
    top: Any = recs[0] if recs else {}
    checks.add(
        "regression.top_test_key",
        TEST_FILE,
        top.get("test_key"),
        top.get("test_key") == TEST_FILE,
    )
    checks.add("regression.top_rank", 1, top.get("rank"), top.get("rank") == 1)
    impacted: list[Any] = (reg.get("impact") or {}).get("impacted") or []
    demo: Any = next((i for i in impacted if i.get("path") == TEST_FILE), {})
    checks.add(
        "regression.impact_has_demo",
        TEST_FILE,
        demo.get("path"),
        demo.get("path") == TEST_FILE,
    )
    kinds: list[Any] = sorted(demo.get("kinds") or [])
    checks.add(
        "regression.impact_kinds",
        ["direct", "generated", "referenced"],
        kinds,
        set(["direct", "generated", "referenced"]) <= set(kinds),
    )
    stats: Any = top.get("stats") or {}
    checks.add(
        "regression.stats_is_flaky",
        True,
        stats.get("is_flaky"),
        stats.get("is_flaky") is True,
    )

    time.sleep(0.3)
    job_row = client.get(f"/jobs/{job_id}", headers=headers).json()
    checks.add(
        "regression.job_row.status",
        "completed",
        job_row.get("status"),
        job_row.get("status") == "completed",
    )
    checks.add(
        "regression.job_row.output_ref",
        f"regression://{PROJECT_ID}",
        job_row.get("output_ref"),
        job_row.get("output_ref") == f"regression://{PROJECT_ID}",
    )
    return reg


# --- run leg (S3 Playwright "run this set") -----------------------------------
def run(client: httpx.Client, headers: dict[str, str], *, checks: Check) -> dict[str, object]:
    """POST /projects/{id}/runs -> SSE run.result (S3 path, demo app auto-started)."""
    body = {"repository_path": REPO_PATH, "tests": [TEST_FILE], "timeout_s": 600}
    res = client.post(f"/projects/{PROJECT_ID}/runs", json=body, headers=headers)
    checks.add("run.http_status", 202, res.status_code, res.status_code == 202)
    if res.status_code != 202:
        raise RuntimeError(f"run rejected: {res.text[:300]}")
    job_id: str = str(res.json().get("job_id"))
    events = stream_job(headers, job_id, timeout_s=900)
    result_events = [d for n, d in events if n == "run.result"]
    checks.add("run.result_events", 1, len(result_events), len(result_events) == 1)
    result: Any = result_events[0] if result_events else {}
    totals: Any = result.get("totals") or {}
    checks.add("run.totals.total", 1, totals.get("total"), totals.get("total") == 1)
    checks.add("run.totals.passed", 1, totals.get("passed"), totals.get("passed") == 1)
    checks.add("run.totals.failed", 0, totals.get("failed"), totals.get("failed") == 0)
    run_id: str = str(result.get("run_id") or "")
    checks.add("run.run_id", "non-empty", run_id, bool(run_id))
    return {"result": result, "run_id": run_id, "job_id": job_id}


def link_failure_to_jira(
    client: httpx.Client, headers: dict[str, str], *, checks: Check
) -> dict[str, object]:
    """S7.4 leg: link the seeded failure to a Jira issue (create -> read-back -> re-link).

    Drives the real ``POST /projects/{id}/failures/{fid}/jira`` route (202 + job),
    the real ``jira_link`` job (the real ``JiraClient`` against our fake Jira),
    and the ``jira.issue`` SSE event. Asserts the issue key is ``QA-1``, that
    ``failures.jira_issue_key`` is persisted, and that a re-link *updates* the
    same key (never duplicates) -- the S7.4 "create-or-update, self-healing"
    contract exercised live.
    """
    failure_id = _pick_jira_failure()

    def _link() -> httpx.Response:
        return client.post(
            f"/projects/{PROJECT_ID}/failures/{failure_id}/jira",
            json={"project_key": JIRA_PROJECT_KEY},
            headers=headers,
        )

    # -- first link: create (202 + job + Location) ---------------------------
    first = _link()
    checks.add("jira.http_status", 202, first.status_code, first.status_code == 202)
    if first.status_code != 202:
        raise RuntimeError(f"jira link rejected: {first.text[:300]}")
    first_job_id = str(first.json().get("job_id"))
    checks.add(
        "jira.location_header",
        f"/api/v1/jobs/{first_job_id}",
        first.headers.get("location"),
        first.headers.get("location") == f"/api/v1/jobs/{first_job_id}",
    )

    # -- drive the job; assert the jira.issue SSE event (action=created) -----
    events = stream_job(headers, first_job_id, timeout_s=120)
    issue_events = [d for n, d in events if n == "jira.issue"]
    checks.add("jira.issue_events", 1, len(issue_events), len(issue_events) == 1)
    issue: Any = issue_events[0] if issue_events else {}
    checks.add("jira.action", "created", issue.get("action"), issue.get("action") == "created")
    checks.add(
        "jira.issue_key", JIRA_FIRST_KEY, issue.get("key"), issue.get("key") == JIRA_FIRST_KEY
    )
    checks.add(
        "jira.project_key",
        JIRA_PROJECT_KEY,
        issue.get("project_key"),
        issue.get("project_key") == JIRA_PROJECT_KEY,
    )
    checks.add("jira.url", "non-empty", issue.get("url"), bool(issue.get("url")))

    # -- read-back: failures.jira_issue_key is persisted (the idempotency anchor)
    readback = _readback_jira_key(failure_id)
    checks.add("jira.readback_key", JIRA_FIRST_KEY, readback, readback == JIRA_FIRST_KEY)

    # -- re-link: update in place, never duplicates --------------------------
    relink_action: str | None = None
    relink_key: object = None
    second = _link()
    checks.add("jira.relink_status", 202, second.status_code, second.status_code == 202)
    if second.status_code == 202:
        second_job_id = str(second.json().get("job_id"))
        events2 = stream_job(headers, second_job_id, timeout_s=120)
        issue_events2 = [d for n, d in events2 if n == "jira.issue"]
        issue2: Any = issue_events2[0] if issue_events2 else {}
        relink_action = issue2.get("action")
        relink_key = issue2.get("key")
    checks.add("jira.relink_action", "updated", relink_action, relink_action == "updated")
    checks.add(
        "jira.relink_key_same", JIRA_FIRST_KEY, relink_key, relink_key == JIRA_FIRST_KEY
    )

    return {
        "failure_id": failure_id,
        "project_key": JIRA_PROJECT_KEY,
        "action": issue.get("action"),
        "issue_key": issue.get("key"),
        "url": issue.get("url"),
        "readback_key": readback,
        "relink_action": relink_action,
        "relink_key": relink_key,
    }


# --- local-stack plumbing -----------------------------------------------------
def _repo_src_paths() -> list[str]:
    return [
        str(REPO_ROOT / "apps" / "api" / "src"),
        str(REPO_ROOT / "packages" / "domain" / "src"),
        str(REPO_ROOT / "packages" / "repository" / "src"),
    ]


def _point_github_at_fake_server() -> None:
    """Point the demo project's S7.1 GitHub integration at the fake server.

    The S7.1 row stores ``base_url`` + ``token_ref`` (the PAT's env-var name);
    the API's ``build_github_client`` reads ``base_url`` to route the real
    ``GitHubClient`` HTTP calls at our local fixture (S6.5 pattern).
    """
    sys.path.insert(0, os.pathsep.join(_repo_src_paths()))
    from qa_copilot_api.config import get_settings  # noqa: PLC0415
    from qa_copilot_api.db import make_app_engine  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    engine = make_app_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            row = session.scalar(
                select(models.IntegrationConfig).where(
                    models.IntegrationConfig.project_id == PROJECT_ID,
                    models.IntegrationConfig.provider == "github",
                )
            )
            if row is None:
                raise RuntimeError(
                    "github integration config not found for the demo project "
                    f"(run scripts/_s75_seed.py first) — project={PROJECT_ID}"
                )
            row.base_url = FAKE_GH_BASE
            session.commit()
    finally:
        engine.dispose()


def _point_jira_at_fake_server() -> None:
    """Point the demo project's S7.4 Jira integration at the fake Jira server.

    Mirrors :func:`_point_github_at_fake_server`: the S7.1 ``jira`` row stores
    ``base_url`` + ``token_ref`` (the token's env-var name); the API's
    ``build_jira_client`` reads ``base_url`` to route the real ``JiraClient``
    HTTP calls at our local fixture (S6.5 pattern).
    """
    sys.path.insert(0, os.pathsep.join(_repo_src_paths()))
    from qa_copilot_api.config import get_settings  # noqa: PLC0415
    from qa_copilot_api.db import make_app_engine  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    engine = make_app_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            row = session.scalar(
                select(models.IntegrationConfig).where(
                    models.IntegrationConfig.project_id == PROJECT_ID,
                    models.IntegrationConfig.provider == "jira",
                )
            )
            if row is None:
                raise RuntimeError(
                    "jira integration config not found for the demo project "
                    f"(run scripts/_s75_seed.py first) -- project={PROJECT_ID}"
                )
            row.base_url = FAKE_JIRA_BASE
            row.token_ref = JIRA_TOKEN_REF
            row.enabled = True
            session.commit()
    finally:
        engine.dispose()


def _pick_jira_failure() -> str:
    """A seeded failure id in the demo project; reset for a deterministic create.

    Walks ``failure -> test_result -> run`` (the same scoping the S7.4 job uses)
    and returns the first failure id, clearing ``jira_issue_key`` so this leg
    always exercises the *create* path (``QA-1``) regardless of prior runs.
    """
    sys.path.insert(0, os.pathsep.join(_repo_src_paths()))
    from qa_copilot_api.config import get_settings  # noqa: PLC0415
    from qa_copilot_api.db import make_app_engine  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    engine = make_app_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            failure = session.scalar(
                select(models.Failure)
                .join(models.TestResult, models.Failure.test_result_id == models.TestResult.id)
                .join(models.TestRun, models.TestResult.run_id == models.TestRun.id)
                .where(models.TestRun.project_id == PROJECT_ID)
                .order_by(models.Failure.id)
            )
            if failure is None:
                raise RuntimeError(
                    f"no seeded failure found for the demo project -- project={PROJECT_ID}"
                )
            if failure.jira_issue_key is not None:
                failure.jira_issue_key = None
            session.commit()
            return failure.id
    finally:
        engine.dispose()


def _readback_jira_key(failure_id: str) -> str | None:
    """The failure's persisted ``jira_issue_key`` (the S7.4 read-back, off the row)."""
    sys.path.insert(0, os.pathsep.join(_repo_src_paths()))
    from qa_copilot_api.config import get_settings  # noqa: PLC0415
    from qa_copilot_api.db import make_app_engine  # noqa: PLC0415
    from qa_copilot_repository import models  # noqa: PLC0415
    from sqlalchemy.orm import Session  # noqa: PLC0415

    engine = make_app_engine(get_settings().database_url)
    try:
        with Session(engine) as session:
            failure = session.get(models.Failure, failure_id)
            return failure.jira_issue_key if failure is not None else None
    finally:
        engine.dispose()


def _api_ready() -> bool:
    try:
        return httpx.get(f"http://{API_HOST}:{API_PORT}/health", timeout=1).status_code == 200
    except httpx.HTTPError:
        return False


_api_log_path = REPO_ROOT / "logs" / "api_s75.log"
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
    # event loop freezes — which is exactly what left the SSE stream (and its
    # 15s keepalives) hanging while the job itself completed fine in the DB.
    # A file has no backpressure, so the API can never block on its own logs.
    global _api_log_file
    _api_log_path.parent.mkdir(parents=True, exist_ok=True)
    _api_log_file = open(_api_log_path, "a", encoding="utf-8", buffering=1)
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
                f"API subprocess exited early — tail of {_api_log_path}:\n"
                f"{_read_log_tail(_api_log_path)}"
            )
        time.sleep(0.25)
    raise RuntimeError(f"API did not become ready in {timeout_s}s")


# --- baseline report ----------------------------------------------------------
def _build_report(
    checks: Check,
    reg: Any,
    run_info: dict[str, object],
    jira_info: dict[str, object] | None,
) -> dict[str, object]:
    passed = sum(1 for c in checks.items if c["passed"])
    return {
        "schema_version": "integrations-v1/1",
        "step": "S7.5",
        "title": "Live E2E baseline: webhook -> regression -> ranked set -> S3 run -> Jira link",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "project_id": PROJECT_ID,
            "pull_request": {"owner": OWNER, "repo": REPO, "number": PR_NUMBER},
            "test_file": TEST_FILE,
            "fake_github_base": FAKE_GH_BASE,
            "fake_jira_base": FAKE_JIRA_BASE,
            "api_base": API_BASE,
        },
        "jira_leg": jira_info if jira_info is not None else "NOT RUN",
        "checks": checks.items,
        "regression_set": reg,
        "run": run_info,
        "summary": {
            "total": len(checks.items),
            "passed": passed,
            "failed": len(checks.failed),
            "status": "pass" if not checks.failed else "fail",
        },
    }


def _write_report(report: dict[str, object]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    checks = Check()
    fake_gh = FakeGitHubServer()
    fake_jira = FakeJiraServer()
    api_proc: subprocess.Popen[str] | None = None
    api_we_started = False
    jira_info: dict[str, object] | None = None

    env = dict(os.environ)
    env[GH_TOKEN_REF] = GH_TOKEN
    env[WEBHOOK_SECRET_REF] = WEBHOOK_SECRET
    env[JIRA_TOKEN_REF] = JIRA_TOKEN

    print("== S7.5 live E2E baseline (webhook -> regression -> run -> Jira link) ==")
    try:
        fake_gh.start()
        print(f"fake GitHub up at {FAKE_GH_BASE}")
        fake_jira.start()
        print(f"fake Jira up at {FAKE_JIRA_BASE}")
        _point_github_at_fake_server()
        print(f"github integration base_url -> {FAKE_GH_BASE}")
        _point_jira_at_fake_server()
        print(f"jira integration base_url -> {FAKE_JIRA_BASE}")

        if _api_ready():
            print("reusing already-running API on :8000")
        else:
            api_proc = _start_api(env)
            api_we_started = True
            _wait_for_api(api_proc, 90)
        print("API ready on :8000")

        with httpx.Client(base_url=API_BASE, timeout=30) as client:
            headers = login(client, checks)

            # A fresh delivery id per run: the webhook_events dedupe is keyed on
            # X-GitHub-Delivery, so a fixed id would collide with a prior run and
            # answer 200/duplicate instead of 202/new-job.
            delivery_id = f"s75-live-{uuid.uuid4().hex[:12]}"
            # Signed webhook -> 202 + job (the real S7.3 route).
            signed = deliver_webhook(client, delivery_id, checks=checks, expect_status=202)
            job_id = signed.json().get("job_id")
            if not job_id:
                raise RuntimeError(f"webhook 202 but no job_id: {signed.text[:300]}")
            # Unsigned -> 401 (the signature IS the auth).
            deliver_webhook(
                client, f"{delivery_id}-unsigned", checks=checks, expect_status=401, sign=False
            )
            # Duplicate signed delivery (same id) -> 200, no second job (dedupe).
            deliver_webhook(client, delivery_id, checks=checks, expect_status=200)
            print(f"webhook job_id={job_id} delivery={delivery_id}")

            reg = run_regression(client, headers, job_id, checks=checks)
            run_info = run(client, headers, checks=checks)
            jira_info = link_failure_to_jira(client, headers, checks=checks)

        report = _build_report(checks, reg, run_info, jira_info)
        _write_report(report)
        print(f"report written to {REPORT_PATH}")

        if checks.failed:
            print(f"\n{len(checks.failed)} check(s) FAILED — see report")
            return 1
        print("\nS7.5 live baseline GREEN")
        return 0
    finally:
        if api_proc is not None and api_we_started:
            api_proc.terminate()
            try:
                api_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api_proc.kill()
        fake_gh.stop()
        fake_jira.stop()


if __name__ == "__main__":
    sys.exit(main())
