"""Fake-server replay runner for the S7.4 Jira golden set (build bible §22, §31.7).

Same pattern as the other integrations: each fixture spins up a
:class:`~http.server.ThreadingHTTPServer` bound to ``127.0.0.1`` on an
ephemeral port (loopback only, never on a shared port) and scripts the
Jira REST v2 responses it serves, in order, per path. The real
:class:`~qa_copilot_integrations.jira.client.JiraClient` is pointed at it.
A case passes only if the typed result matches the golden ``expect`` —
including the token-redaction contract (§17: the token never appears in a
raised error message).

Mapping pins (``mappings`` in the golden) are replayed directly against
:func:`~qa_copilot_integrations.jira.issue.build_issue_payload` and must
match the pinned ``expect_payload`` 100% (S7.4 exit criterion: the failure
→ issue mapping matches golden 100%).

Determinism: fixed ``127.0.0.1`` bind, ephemeral port, scripted
responses, no clock, no LLM (§31.1 off the path).

Note on the fake server: it is a single-purpose test double (not a general
HTTP mock) and is intentionally tiny.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from qa_copilot_integrations.jira.client import (
    JiraAuthError,
    JiraClient,
    JiraError,
    JiraHTTPError,
    JiraIssue,
    JiraNotFoundError,
)
from qa_copilot_integrations.jira.golden import (
    JiraFixture,
    JiraGoldenSet,
    MappingPin,
)
from qa_copilot_integrations.jira.issue import build_issue_payload

#: Bearer token the client sends in every replay — the golden's
#: ``expect_auth`` must pin it, and redaction checks must never see it
#: in an error message (§17).
REPLAY_TOKEN = "ATATT7xS74GoldenToken1234567890"
_NO_SCRIPT = 599


class _FakeJiraState:
    """Per-fixture scripted state: response cursors + a request log."""

    def __init__(self, fixture: JiraFixture) -> None:
        self._cursor: dict[str, int] = {}
        self._responses = list(fixture.responses)
        self.seen: list[tuple[str, str, str | None, Any]] = []

    def record(self, method: str, path: str, auth: str | None, body: Any) -> None:
        self.seen.append((method, path, auth, body))

    def next_response(self, path: str) -> tuple[int, Any] | None:
        """Next scripted response for ``path`` (consumed in golden order)."""
        idx = self._cursor.get(path, 0)
        matches = [r for r in self._responses if r.path == path]
        if idx >= len(matches):
            return None
        self._cursor[path] = idx + 1
        return (matches[idx].status, matches[idx].body)


def _make_handler(state: _FakeJiraState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def _read_body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return None
            try:
                return json.loads(raw)
            except ValueError:
                return raw.decode("utf-8", "replace")

        def _dispatch(self, method: str) -> None:
            path = urlsplit(self.path).path
            body = self._read_body() if method in ("POST", "PUT") else None
            state.record(method, path, self.headers.get("Authorization"), body)
            scripted = state.next_response(path)
            if scripted is None:
                payload: Any = {"message": f"no scripted response for {method} {path}"}
                status = _NO_SCRIPT
            else:
                status, payload = scripted
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802 (http.server API)
            self._dispatch("PUT")

        def log_message(self, *args: object) -> None:
            """Silence default stderr logging (deterministic stdout/stderr)."""
            return None

    return _Handler


class FakeJiraServer:
    """Threaded loopback Jira fake; start/stop, exposes ``base_url``."""

    def __init__(self, fixture: JiraFixture) -> None:
        self._fixture = fixture
        self._state = _FakeJiraState(fixture)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""

    def start(self) -> None:
        handler = _make_handler(self._state)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base_url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def seen(self) -> list[tuple[str, str, str | None, Any]]:
        """Request log: (method, path, authorization, body) in order."""
        return list(self._state.seen)


class JiraCaseResult(BaseModel):
    """Per-case golden result (§31.7 reportable unit)."""

    case_id: str
    kind: str  # "mapping" | "client"
    title: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any] | str
    error: str | None = None


class JiraReport(BaseModel):
    """Golden report: the §31.7 gate is ``targets.pass_min`` (here 1.0)."""

    golden_name: str
    golden_version: str
    mappings: int
    fixtures: int
    passed: int
    failures: list[str]
    score: float
    gate: float
    gate_met: bool
    cases: list[JiraCaseResult]


def _issue_dict(issue: JiraIssue) -> dict[str, Any]:
    if is_dataclass(issue):
        return asdict(issue)
    return dict(vars(issue))


def _first_diff(actual: Any, expected: Any, path: str = "$") -> str:
    """First structural difference between computed and golden payload."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected:
            if key not in actual:
                return f"missing key at {path}.{key}"
        for key in actual:
            if key not in expected:
                return f"unexpected key at {path}.{key}"
        for key in expected:
            if actual[key] != expected[key]:
                return _first_diff(actual[key], expected[key], f"{path}.{key}")
        return f"mismatch at {path}"
    if isinstance(expected, list) and isinstance(actual, list):
        if len(actual) != len(expected):
            return f"list length at {path}: expected {len(expected)}, got {len(actual)}"
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            if a != e:
                return _first_diff(a, e, f"{path}[{i}]")
        return f"mismatch at {path}"
    return f"mismatch at {path}: expected {expected!r}, got {actual!r}"


def _check_expectation(
    fixture: JiraFixture,
    result: JiraIssue | None,
    exc: JiraError | None,
) -> JiraCaseResult:
    """Compare the golden ``expect`` against the client outcome (§17 redaction included)."""
    exp = fixture.expect
    if exp.get("kind") == "ok":
        if exc is not None:
            return JiraCaseResult(
                case_id=fixture.id,
                kind="client",
                title=fixture.title,
                passed=False,
                expected=exp,
                actual=type(exc).__name__,
                error=f"expected ok, got {type(exc).__name__}: {exc}",
            )
        if result is None:
            return JiraCaseResult(
                case_id=fixture.id,
                kind="client",
                title=fixture.title,
                passed=False,
                expected=exp,
                actual="no result",
                error="expected ok, got no result and no error",
            )
        got = _issue_dict(result)
        reasons: list[str] = []
        for key in ("key", "id", "summary", "status", "url"):
            want = exp.get(key)
            if want is not None and got.get(key) != want:
                reasons.append(f"{key}: expected {want!r}, got {got.get(key)!r}")
        return JiraCaseResult(
            case_id=fixture.id,
            kind="client",
            title=fixture.title,
            passed=not reasons,
            expected=exp,
            actual=got,
            error="; ".join(reasons) if reasons else None,
        )
    # error expectation
    want_error = exp.get("error")
    if exc is None:
        return JiraCaseResult(
            case_id=fixture.id,
            kind="client",
            title=fixture.title,
            passed=False,
            expected=exp,
            actual=_issue_dict(result) if result is not None else "no result",
            error=f"expected error {want_error!r}, got success",
        )
    reasons = []
    if want_error == "auth" and not isinstance(exc, JiraAuthError):
        reasons.append(f"expected JiraAuthError, got {type(exc).__name__}")
    if want_error == "not_found" and not isinstance(exc, JiraNotFoundError):
        reasons.append(f"expected JiraNotFoundError, got {type(exc).__name__}")
    if want_error == "http" and not isinstance(exc, JiraHTTPError):
        reasons.append(f"expected JiraHTTPError, got {type(exc).__name__}")
    want_status = exp.get("status")
    if want_status is not None and getattr(exc, "status", None) != want_status:
        reasons.append(f"status: expected {want_status!r}, got {getattr(exc, 'status', None)!r}")
    msg = str(exc)
    for needle in exp.get("message_contains", []):
        if needle not in msg:
            reasons.append(f"message missing {needle!r}")
    for needle in exp.get("message_not_contains", []):
        if needle in msg:
            reasons.append(f"message must not contain {needle!r} (§17 redaction)")
    return JiraCaseResult(
        case_id=fixture.id,
        kind="client",
        title=fixture.title,
        passed=not reasons,
        expected=exp,
        actual={
            "error": type(exc).__name__,
            "status": getattr(exc, "status", None),
            "message": msg,
        },
        error="; ".join(reasons) if reasons else None,
    )


def _auth_reasons(server: FakeJiraServer, fixture: JiraFixture) -> list[str]:
    """Pin the Authorization header the client actually sent (never leaked, §17)."""
    if fixture.expect_auth is None:
        return []
    reasons: list[str] = []
    for method, path, auth, _body in server.seen:
        if auth != fixture.expect_auth:
            reasons.append(
                f"{method} {path}: Authorization was {auth!r}, expected {fixture.expect_auth!r}"
            )
    return reasons


def _payload_for(fixture: JiraFixture, golden: JiraGoldenSet) -> dict[str, Any]:
    """Deterministic body for create/update calls (the golden mapping pin)."""
    mapping_id = fixture.call.mapping
    if mapping_id is None:  # loader guarantees presence; belt & braces
        raise ValueError(f"fixture {fixture.id}: call.mapping is required")
    for pin in golden.mappings:
        if pin.id == mapping_id:
            return build_issue_payload(pin.failure, pin.project_key)
    raise ValueError(f"fixture {fixture.id}: unknown mapping pin {mapping_id!r}")


async def _run_fixture(
    server: FakeJiraServer, fixture: JiraFixture, golden: JiraGoldenSet
) -> JiraCaseResult:
    client = JiraClient(base_url=server.base_url, token=REPLAY_TOKEN)
    result: JiraIssue | None = None
    exc: JiraError | None = None
    try:
        try:
            kind = fixture.call.kind
            if kind == "create_issue":
                result = await client.create_issue(_payload_for(fixture, golden))
            elif kind == "update_issue":
                result = await client.update_issue(
                    fixture.call.key or "", _payload_for(fixture, golden)
                )
            else:  # fetch_issue
                result = await client.fetch_issue(fixture.call.key or "")
        except JiraError as caught:
            exc = caught
    finally:
        await client.aclose()
    case = _check_expectation(fixture, result, exc)
    reasons = _auth_reasons(server, fixture)
    if not case.passed or reasons:
        error = case.error or ""
        if reasons:
            error = f"{error}; " if error else ""
            error += "; ".join(reasons)
        case = case.model_copy(update={"passed": False, "error": error})
    return case


def _run_mapping(pin: MappingPin) -> JiraCaseResult:
    try:
        payload = build_issue_payload(pin.failure, pin.project_key)
    except ValueError as caught:
        return JiraCaseResult(
            case_id=pin.id,
            kind="mapping",
            title=f"failure → issue payload pin: {pin.id}",
            passed=False,
            expected=pin.expect_payload,
            actual=f"{type(caught).__name__}: {caught}",
            error=str(caught),
        )
    if payload == pin.expect_payload:
        return JiraCaseResult(
            case_id=pin.id,
            kind="mapping",
            title=f"failure → issue payload pin: {pin.id}",
            passed=True,
            expected=pin.expect_payload,
            actual=payload,
        )
    return JiraCaseResult(
        case_id=pin.id,
        kind="mapping",
        title=f"failure → issue payload pin: {pin.id}",
        passed=False,
        expected=pin.expect_payload,
        actual=payload,
        error=_first_diff(payload, pin.expect_payload),
    )


def run_jira_eval(golden: JiraGoldenSet) -> JiraReport:
    """Replay every golden case and score against ``targets.pass_min`` (§31.7)."""
    cases: list[JiraCaseResult] = [_run_mapping(pin) for pin in golden.mappings]
    for fixture in golden.fixtures:
        server = FakeJiraServer(fixture)
        server.start()
        try:
            cases.append(asyncio.run(_run_fixture(server, fixture, golden)))
        finally:
            server.stop()
    passed = sum(1 for c in cases if c.passed)
    total = len(cases)
    score = passed / total if total else 0.0
    gate = golden.targets.get("pass_min", 1.0)
    return JiraReport(
        golden_name=golden.name,
        golden_version=golden.version,
        mappings=len(golden.mappings),
        fixtures=len(golden.fixtures),
        passed=passed,
        failures=[c.case_id for c in cases if not c.passed],
        score=round(score, 6),
        gate=gate,
        gate_met=score >= gate,
        cases=cases,
    )


__all__ = [
    "FakeJiraServer",
    "JiraCaseResult",
    "JiraReport",
    "REPLAY_TOKEN",
    "run_jira_eval",
]
