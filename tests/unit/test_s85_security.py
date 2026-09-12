"""S8.5 deployment hardening (build bible §19 S8.5) — middleware behaviour.

Covers the S8.5 security layers end to end:

- security headers (CSP / HSTS / nosniff / Referrer-Policy) on success,
  error (404) and rate-limited (429) responses
- request-id correlation: echoed ``X-Request-ID``, generated when absent,
  present on error responses, stamped onto JSON log lines (top-level
  ``request_id`` field via ``RequestIDFilter``)
- CORS lockdown: allowed origins answered, unknown origins silent,
  preflight allow/deny, locked-down-by-default when no origins are set
- rate limiting (per IP + per verified user, Redis fixed-window):
  429 + ``Retry-After`` + ``rate_limited`` body, client IP from the first
  ``X-Forwarded-For`` hop, user bucket only from a *verified* JWT ``sub``
  (invalid tokens fall back to the IP bucket), exempt paths bypass the
  limiter, fail-open when Redis is unavailable
- live-Redis tests (skipped when Redis is down): fixed-window counts,
  ``Retry-After`` within the window, independent user/IP buckets

The middleware tests are pure: the app is built with
``create_app(settings=...)`` and driven through ``TestClient`` *without*
entering its context manager, so the job runner never touches a database.
"""

from __future__ import annotations

import json
import logging
import socket
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from fastapi.testclient import TestClient
from qa_copilot_api import main as api_main
from qa_copilot_api import security
from qa_copilot_api.config import Settings
from qa_copilot_api.logging_config import JsonFormatter

SECRET = "s85-security-test-secret-0123456789"  # 16+ chars, test-only
ALLOWED_ORIGIN = "https://good.example.com"
DENIED_ORIGIN = "https://evil.example.com"
USER_ID = "s85-user"
# engine is created lazily and never queried by these tests
UNUSED_DB = "postgresql+psycopg://qa:qa@localhost:5433/qa_copilot_s85_unused"


def _token(sub: str = USER_ID, secret: str = SECRET) -> str:
    return jwt.encode({"sub": sub}, secret, algorithm="HS256")


class RecordingLimiter:
    """Deterministic stand-in for ``security.RateLimiter``.

    Records every ``(user_id, ip)`` it is asked about and answers with a
    programmable decision — the middleware only ever sees this interface,
    which is exactly the contract S8.5 pins down.
    """

    def __init__(self, decision: security.RateDecision | None = None) -> None:
        self.max_requests = 5
        self.calls: list[tuple[str | None, str]] = []
        self.decision = decision or security.RateDecision(False, 0)

    def check(self, user_id: str | None, ip: str) -> security.RateDecision:
        self.calls.append((user_id, ip))
        return self.decision

    def close(self) -> None:
        return None


def _settings(**overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {
        "database_url": UNUSED_DB,
        "auth_token_secret": SECRET,
        "_env_file": None,  # keep tests from reading the dev .env
    }
    kwargs.update(overrides)
    return Settings(**kwargs)  # type: ignore[call-arg]


@pytest.fixture()
def app() -> Iterator[TestClient]:
    """The full S8.5 stack: CORS (one allowed origin) + rate limiting ON.

    ``TestClient`` without a context manager: no lifespan runs, so the job
    runner never touches a database (the middleware under test needs none).
    """
    created = api_main.create_app(
        settings=_settings(cors_origins=ALLOWED_ORIGIN, rate_limit_enabled=True)
    )
    # production-like: server errors surface as 500 responses, not test noise
    yield TestClient(created, raise_server_exceptions=False)


def _blocked(app: TestClient) -> RecordingLimiter:
    """Install a limiter that blocks everything and hand it back."""
    limiter = RecordingLimiter(security.RateDecision(True, 7))
    app.app.state.rate_limiter = limiter
    return limiter


# --- security headers ---------------------------------------------------------


def test_security_headers_on_success_response(app: TestClient) -> None:
    r = app.get("/health")
    assert r.status_code == 200
    assert "default-src" in r.headers["content-security-policy"]
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_security_headers_on_error_response(app: TestClient) -> None:
    r = app.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert "default-src" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_security_headers_on_rate_limited_response(app: TestClient) -> None:
    _blocked(app)
    r = app.get("/api/v1/projects")
    assert r.status_code == 429
    assert "default-src" in r.headers["content-security-policy"]
    assert r.headers["strict-transport-security"].startswith("max-age=31536000")


# --- request id ---------------------------------------------------------------


def test_request_id_is_echoed_when_provided(app: TestClient) -> None:
    r = app.get("/health", headers={"X-Request-ID": "s85-req-123"})
    assert r.headers["x-request-id"] == "s85-req-123"


def test_request_id_is_generated_when_absent(app: TestClient) -> None:
    first = app.get("/health").headers["x-request-id"]
    second = app.get("/health").headers["x-request-id"]
    assert len(first) == 32 and len(second) == 32  # uuid4 hex
    assert first != second  # a fresh id per request


def test_request_id_present_on_error_responses(app: TestClient) -> None:
    r = app.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert r.headers["x-request-id"]


def test_json_log_line_carries_request_id(app: TestClient) -> None:
    """RequestIDFilter must stamp the id onto the log record and the JSON
    line must expose it as a top-level ``request_id`` field."""

    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = captured.append  # type: ignore[method-assign]

    target = logging.getLogger("qa_copilot_api.security")
    old_level, old_handlers, old_disabled = target.level, target.handlers, target.disabled
    # Earlier tests that run alembic in-process may leave this logger
    # disabled (logging.fileConfig's disable_existing_loggers default) —
    # reset it so the assertion is order-independent.
    target.disabled = False
    target.setLevel(logging.INFO)
    target.addHandler(handler)
    try:
        app.get("/health", headers={"X-Request-ID": "s85-correlated"})
    finally:
        target.removeHandler(handler)
        target.setLevel(old_level)
        target.handlers = old_handlers
        target.disabled = old_disabled

    assert captured, "the middleware should log one http.request line"
    record = captured[-1]
    assert getattr(record, "request_id", None) == "s85-correlated"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["request_id"] == "s85-correlated"
    assert payload["message"] == "http.request"


# --- CORS ----------------------------------------------------------------------


def test_cors_allows_configured_origin(app: TestClient) -> None:
    r = app.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert r.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_cors_silent_for_unknown_origin(app: TestClient) -> None:
    r = app.get("/health", headers={"Origin": DENIED_ORIGIN})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_cors_preflight_allowed(app: TestClient) -> None:
    r = app.options(
        "/api/v1/projects",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code in (200, 204)
    assert r.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]


def test_cors_preflight_denied(app: TestClient) -> None:
    r = app.options(
        "/api/v1/projects",
        headers={
            "Origin": DENIED_ORIGIN,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_cors_locked_down_by_default() -> None:
    """No origins configured → no CORS middleware at all → a browser
    cross-origin request gets no ACAO header (fail-closed)."""
    created = api_main.create_app(settings=_settings())
    r = TestClient(created).get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


# --- rate limiting (middleware contract) ---------------------------------------


def test_blocked_request_gets_429_with_retry_after(app: TestClient) -> None:
    _blocked(app)
    r = app.get("/api/v1/projects")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "7"
    body = r.json()["detail"]
    assert body["code"] == "rate_limited"
    assert body["retry_after_s"] == 7
    assert body["limit"] == 5


def test_limiter_sees_the_forwarded_client_ip(app: TestClient) -> None:
    limiter = RecordingLimiter()
    app.app.state.rate_limiter = limiter
    app.get("/api/v1/projects", headers={"X-Forwarded-For": "10.1.2.3, 198.51.100.7"})
    assert limiter.calls[-1][1] == "10.1.2.3"  # first hop = the real client


def test_user_bucket_uses_verified_token_sub(app: TestClient) -> None:
    limiter = RecordingLimiter()
    app.app.state.rate_limiter = limiter
    # a 404 path: the limiter runs before routing, and no database is touched
    app.get(
        "/api/v1/does-not-exist",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert limiter.calls[-1][0] == USER_ID


@pytest.mark.parametrize(
    "header",
    [
        "Bearer not-a-jwt",  # malformed → untrusted
        "Basic dXNlcjpwYXNz",  # wrong scheme
        None,  # no auth at all
    ],
)
def test_unverified_credentials_fall_back_to_ip_bucket(app: TestClient, header: str | None) -> None:
    limiter = RecordingLimiter()
    app.app.state.rate_limiter = limiter
    headers = {"Authorization": header} if header else {}
    r = app.get("/api/v1/projects", headers=headers)
    assert r.status_code != 429  # not blocked: the decision is "allow"
    assert limiter.calls[-1][0] is None  # user bucket must NOT be keyed by garbage


def test_exempt_paths_bypass_the_limiter(app: TestClient) -> None:
    limiter = _blocked(app)
    for path in ("/health", "/docs", "/openapi.json"):
        r = app.get(path)
        assert r.status_code == 200, path
    assert limiter.calls == []  # the limiter was never consulted


def test_fail_open_when_not_blocked(app: TestClient) -> None:
    app.app.state.rate_limiter = RecordingLimiter(security.RateDecision(False, 0))
    assert app.get("/api/v1/projects").status_code != 429


def test_fail_open_when_redis_is_unreachable(app: TestClient) -> None:
    """A real ``RateLimiter`` pointed at a dead Redis must let traffic
    through (availability beats a soft limit)."""
    limiter = security.RateLimiter("redis://127.0.0.1:1/0", max_requests=1, window_s=5)
    app.app.state.rate_limiter = limiter
    r = app.get("/api/v1/projects")
    assert r.status_code != 429


# --- token extraction (unit) -----------------------------------------------------


def test_user_id_from_authorization_rejects_forged_or_invalid_tokens() -> None:
    assert security.user_id_from_authorization(f"Bearer {_token()}", SECRET) == USER_ID
    # wrong secret, bad scheme, malformed token, missing sub
    assert security.user_id_from_authorization(f"Bearer {_token(secret='x' * 32)}", SECRET) is None
    assert security.user_id_from_authorization(f"Basic {_token()}", SECRET) is None
    assert security.user_id_from_authorization("Bearer garbage", SECRET) is None
    assert security.user_id_from_authorization(None, SECRET) is None
    assert (
        security.user_id_from_authorization(
            f"Bearer {jwt.encode({}, SECRET, algorithm='HS256')}", SECRET
        )
        is None
    )
    # no secret configured → never trust any token
    assert security.user_id_from_authorization(f"Bearer {_token()}", None) is None


# --- live Redis: fixed-window semantics ------------------------------------------


def _redis_up() -> bool:
    try:
        with socket.create_connection(("localhost", 6379), timeout=1.0):
            return True
    except OSError:
        return False


needs_redis = pytest.mark.skipif(not _redis_up(), reason="Redis is not reachable")


@needs_redis
def test_fixed_window_blocks_after_limit_with_retry_after() -> None:
    run = uuid.uuid4().hex[:8]  # fresh bucket keys: no stale window from a prior run
    limiter = security.RateLimiter("redis://localhost:6379/0", max_requests=3, window_s=30)
    ip = f"s85-live-{run}"
    try:
        for _ in range(3):
            assert limiter.check(None, ip).blocked is False
        decision = limiter.check(None, ip)
        assert decision.blocked is True
        assert 0 < decision.retry_after_s <= 30
    finally:
        limiter.close()


@needs_redis
def test_user_and_ip_buckets_are_independent() -> None:
    run = uuid.uuid4().hex[:8]  # fresh bucket keys: no stale window from a prior run
    limiter = security.RateLimiter("redis://localhost:6379/0", max_requests=2, window_s=30)
    user, fresh_ip = f"s85-live-user-{run}", f"s85-live-fresh-{run}"
    busy_ip = f"s85-live-busy-{run}"
    try:
        assert limiter.check(user, busy_ip).blocked is False
        assert limiter.check(user, busy_ip).blocked is False
        assert limiter.check(user, busy_ip).blocked is True  # user bucket full
        # …but an unauthenticated request from a *different* IP is still
        # under its own IP bucket:
        assert limiter.check(None, fresh_ip).blocked is False
    finally:
        limiter.close()
