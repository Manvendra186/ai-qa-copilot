"""Fake-server replay runner for the S7.1 GitHub golden set (§22, §31.7).

Deterministic + **LLM-free**: each fixture is replayed through the real
:class:`GitHubClient` against an in-process GitHub-shaped HTTP server
(``ThreadingHTTPServer`` on 127.0.0.1) — the same offline pattern as the
S4.1/S6.3 eval runners. No network, no model, no DB.

Per-fixture contract:

* scripted responses are matched by request path (query ignored) and
  consumed in fixture order (so pagination works: page 1 first, then the
  ``Link: rel="next"`` page);
* ``{{base}}`` inside a scripted ``Link`` header is replaced with the
  fake server's real base URL at serve time;
* when a fixture sets ``expect_auth``, **every** request must carry that
  exact ``Authorization`` header (PAT wiring check);
* a request with no scripted response returns ``599`` (the runner marks
  the case failed with a fixture error — the client never sees it as an
  API result).
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

from .client import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubHTTPError,
    GitHubNotFoundError,
)
from .golden import FixtureResponse, GitHubFixture, GitHubGoldenSet

#: PAT used for every replay — a plausible GitHub PAT shape so the
#: redaction contract (Bearer + ``ghp_``) is genuinely exercised.
REPLAY_TOKEN = "ghp_S71GoldenToken0123456789"

#: Sentinel status the fake server returns for unscripted requests.
_NO_SCRIPT = 599


class GitHubCaseResult(BaseModel):
    """One fixture's replay outcome (report line)."""

    fixture_id: str
    title: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any] | str
    error: str | None = None


class GitHubReport(BaseModel):
    """The S7.1 golden replay report (JSON; §22/§31.7 gate)."""

    golden_name: str
    golden_version: str
    fixtures: int
    passed: int
    pass_fraction: float
    targets: dict[str, float]
    gate_passed: bool
    cases: list[GitHubCaseResult]


class _FakeGitHubState:
    """Per-fixture server state (thread-safe across handler threads)."""

    def __init__(self, fixture: GitHubFixture) -> None:
        self.fixture = fixture
        self.lock = threading.Lock()
        self.cursor: dict[str, int] = {}
        self.seen_auth: list[str | None] = []
        self.seen_paths: list[str] = []

    def record(self, path: str, auth: str | None) -> None:
        with self.lock:
            self.seen_paths.append(path)
            self.seen_auth.append(auth)

    def next_response(self, path: str) -> FixtureResponse | None:
        with self.lock:
            idx = self.cursor.get(path, 0)
            matches = [r for r in self.fixture.responses if r.path == path]
            if idx >= len(matches):
                return None
            self.cursor[path] = idx + 1
            return matches[idx]


def _host_port(server_address: object) -> tuple[str, int]:
    """(host, port) from a bound socketserver address (fail loud on exotic types)."""
    if not isinstance(server_address, tuple) or len(server_address) < 2:
        raise RuntimeError(f"unexpected server address: {server_address!r}")
    return str(server_address[0]), int(server_address[1])


def _make_handler(state: _FakeGitHubState) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API name
            path = urlsplit(self.path).path
            state.record(path, self.headers.get("Authorization"))
            response = state.next_response(path)
            if response is None:
                self._send(_NO_SCRIPT, {"message": f"fixture: no scripted response for GET {path}"})
                return
            extra: dict[str, str] = {}
            if response.link:
                host, port = _host_port(self.server.server_address)
                extra["Link"] = response.link.replace("{{base}}", f"http://{host}:{port}")
            self._send(response.status, response.body, extra)

        def _send(self, status: int, body: Any, extra: dict[str, str] | None = None) -> None:
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args: object) -> None:  # keep test output clean
            return

    return _Handler


class FakeGitHubServer:
    """Threaded 127.0.0.1 server serving one fixture's scripted responses."""

    def __init__(self, fixture: GitHubFixture) -> None:
        self.state = _FakeGitHubState(fixture)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.state))
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        host, port = _host_port(self._httpd.server_address)
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def _actual_fields(result: object) -> dict[str, Any]:
    """Dataclass → report dict (``changed_files`` surfaced as S6.1 ``files``)."""
    if not is_dataclass(result) or isinstance(result, type):
        raise TypeError(f"expected a typed client result, got {type(result).__name__}")
    data = dict(asdict(result))
    if "changed_files" in data:
        data["files"] = list(data.pop("changed_files"))
    return data


def _check_auth(server: FakeGitHubServer, fixture: GitHubFixture) -> str | None:
    """The PAT wiring contract: every request carries the expected header."""
    if fixture.expect_auth is None:
        return None
    missing = [
        i for i, auth in enumerate(server.state.seen_auth) if auth != fixture.expect_auth
    ]
    if missing:
        got = server.state.seen_auth[missing[0]]
        return f"Authorization header mismatch on request {missing[0]}: got {got!r}"
    return None


def _check_expectation(
    fixture: GitHubFixture, exc: GitHubError | None, result: object
) -> tuple[bool, str | None]:
    """Compare the outcome against the fixture's ``expect`` block."""
    expect = fixture.expect
    if expect["kind"] == "ok":
        if exc is not None or result is None:
            return False, f"expected ok, got error: {exc}"
        actual = _actual_fields(result)
        for key, want in expect.items():
            if key == "kind":
                continue
            if actual.get(key) != want:
                return False, f"field {key!r}: expected {want!r}, got {actual.get(key)!r}"
        return True, None

    if exc is None:
        return False, f"expected error {expect.get('error')!r}, but the call succeeded"
    want_error = expect["error"]
    if want_error == "auth" and not isinstance(exc, GitHubAuthError):
        return False, f"expected GitHubAuthError, got {type(exc).__name__}"
    if want_error == "not_found" and not isinstance(exc, GitHubNotFoundError):
        return False, f"expected GitHubNotFoundError, got {type(exc).__name__}"
    if want_error == "http" and not isinstance(exc, GitHubHTTPError):
        return False, f"expected GitHubHTTPError, got {type(exc).__name__}"
    if "status" in expect and exc.status != expect["status"]:
        return False, f"status: expected {expect['status']!r}, got {exc.status!r}"
    message = str(exc)
    if "message_contains" in expect and expect["message_contains"] not in message:
        return False, f"message should contain {expect['message_contains']!r}"
    for bad in expect.get("message_not_contains", []):
        if bad in message:
            return False, f"message must not contain {bad!r} (PAT redaction, §17)"
    return True, None


async def _run_fixture(server: FakeGitHubServer, fixture: GitHubFixture) -> GitHubCaseResult:
    """Replay one fixture through the real client; return the report line."""
    client = GitHubClient(base_url=server.base_url, token=REPLAY_TOKEN)
    exc: GitHubError | None = None
    result: object = None
    try:
        try:
            if fixture.call.kind == "resolve_repository":
                result = await client.resolve_repository(fixture.call.owner, fixture.call.repo)
            else:
                result = await client.fetch_pull_request(
                    fixture.call.owner, fixture.call.repo, fixture.call.number or 0
                )
        except GitHubError as caught:
            exc = caught
    finally:
        await client.aclose()

    auth_error = _check_auth(server, fixture)
    expect_error = _check_expectation(fixture, exc, result)
    passed = auth_error is None and expect_error[0]
    error = auth_error or expect_error[1]
    if exc is not None:
        actual: dict[str, Any] | str = f"{type(exc).__name__}: {exc} (status={exc.status})"
    else:
        actual = _actual_fields(result) if result is not None else "<no result>"
    return GitHubCaseResult(
        fixture_id=fixture.id,
        title=fixture.title,
        passed=passed,
        expected=fixture.expect,
        actual=actual,
        error=error,
    )


def run_github_eval(golden: GitHubGoldenSet) -> GitHubReport:
    """Replay every fixture (fake server per fixture) and score the §31.7 gate.

    Deterministic + LLM-free: no network beyond loopback, no model, no DB.
    """
    cases: list[GitHubCaseResult] = []
    for fixture in golden.fixtures:
        server = FakeGitHubServer(fixture)
        server.start()
        try:
            case = asyncio.run(_run_fixture(server, fixture))
        finally:
            server.stop()
        cases.append(case)

    total = len(cases)
    passed = sum(1 for case in cases if case.passed)
    fraction = passed / total if total else 0.0
    pass_min = golden.targets.get("pass_min", 1.0)
    return GitHubReport(
        golden_name=golden.name,
        golden_version=golden.version,
        fixtures=total,
        passed=passed,
        pass_fraction=fraction,
        targets=dict(golden.targets),
        gate_passed=fraction >= pass_min,
        cases=cases,
    )


__all__ = [
    "FakeGitHubServer",
    "GitHubCaseResult",
    "GitHubReport",
    "REPLAY_TOKEN",
    "run_github_eval",
]
