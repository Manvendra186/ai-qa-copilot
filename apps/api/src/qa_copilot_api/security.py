"""Deployment hardening middleware (build bible §19 S8.5).

- :class:`RequestIDMiddleware` — assigns (or echoes) an ``X-Request-ID`` on
  every response and puts it in the ``logging_config.request_id_var``
  contextvar, so every JSON log line emitted during the request carries the
  same id (request-id correlation, §19 S8.5 / §31.5).
- :class:`SecurityHeadersMiddleware` — CSP / HSTS / X-Content-Type-Options /
  Referrer-Policy on every response (bible: "security headers (CSP/HSTS/
  X-Content-Type-Options/Referrer-Policy)").
- :class:`RateLimiter` — fixed-window request counters in Redis, per **IP**
  and per **authenticated user** (same fail-open stance as S8.1's
  ``LoginThrottler`` — a local-first deployment must stay available when
  Redis is down).
- :class:`RateLimitMiddleware` — answers **429 + ``Retry-After``** once
  either bucket is exhausted (bible exit criterion); ``/health`` and the
  docs endpoints are exempt so healthchecks and tooling always reach the
  service.

Ordering (see ``main.create_app`` — the last ``add_middleware`` call is the
outermost layer): ``RequestID`` → ``CORS`` → ``RateLimit`` →
``SecurityHeaders``. Pre-flight OPTIONS requests are answered by the CORS
layer before the rate limiter counts them; the security headers ride on
every response that passes through the inner layers.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import jwt
from redis import Redis
from redis.exceptions import RedisError
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from qa_copilot_api.logging_config import request_id_var

logger = logging.getLogger(__name__)

__all__ = [
    "EXEMPT_PATHS",
    "SECURITY_HEADERS",
    "RateDecision",
    "RateLimiter",
    "RateLimitMiddleware",
    "RequestIDMiddleware",
    "SecurityHeadersMiddleware",
    "user_id_from_authorization",
]


# --- request-id (S8.5: correlation across logs, responses, the proxy) --------


def _client_ip(scope: Scope) -> str:
    """Best client IP: first ``X-Forwarded-For`` hop, else the socket peer."""
    headers = Headers(scope=scope)
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


class RequestIDMiddleware:
    """Echo the caller's ``X-Request-ID`` or mint one; correlate every log.

    The id is exposed on ``scope["state"]["request_id"]`` (Starlette's
    ``Request.state``) and in the ``request_id_var`` contextvar. Any log
    record emitted while the request is in flight — sync endpoints in the
    threadpool included (anyio copies the context to its workers) — picks up
    the id via ``RequestIDFilter`` and lands as a top-level ``request_id``
    field in the JSON log line.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id", "").strip()
        request_id = incoming if incoming else uuid.uuid4().hex
        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state["request_id"] = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_seen: list[int] = []

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["x-request-id"] = request_id
                status_seen.append(int(message.get("status", 0)))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)
            # Access-style line (method/path/status/duration) carrying the
            # id — the correlation anchor for structured JSON logs (§31.5).
            logger.info(
                "http.request",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status_seen[0] if status_seen else 0,
                    "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
                },
            )


# --- security headers (S8.5: CSP/HSTS/X-Content-Type-Options/Referrer-Policy) -

#: Added to every response. Values are deliberately conservative: the API is
#: a JSON service with no inline scripts, so ``default-src 'self'`` is enough.
SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'"
    ),
    "strict-transport-security": "max-age=31536000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}


class SecurityHeadersMiddleware:
    """Set the four security headers on every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)


# --- rate limiting (S8.5: per-user + per-IP, Redis, 429 + Retry-After) -------

_KEY_PREFIX = "qa_copilot:rl"

#: Paths that must stay reachable regardless of load (healthchecks, docs).
EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/docs", "/redoc", "/openapi.json"})


class RateDecision:
    """Outcome of a rate-limit check (``blocked`` + how long to wait)."""

    __slots__ = ("blocked", "retry_after_s")

    def __init__(self, blocked: bool, retry_after_s: int) -> None:
        self.blocked = blocked
        self.retry_after_s = retry_after_s


class RateLimiter:
    """Fixed-window request counters in Redis (per IP and per user).

    Every request is counted against the IP bucket and — when a valid
    access token is present — the user bucket. Once *either* counter
    exceeds ``max_requests`` within the window the request is blocked with
    a ``Retry-After`` equal to the remaining TTL.

    - **Fail-open** on Redis errors (S8.1 stance: availability wins on a
      local-first deployment).
    - Counters are the only state; user ids and IPs are the only key
      material — never a secret (§17).
    """

    def __init__(self, url: str, max_requests: int = 120, window_s: int = 60) -> None:
        self.max_requests = max(1, max_requests)
        self.window_s = max(1, window_s)
        self._redis = Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )

    # -- internals -----------------------------------------------------------

    def _key(self, kind: str, value: str) -> str:
        return f"{_KEY_PREFIX}:{kind}:{value}"

    def _count(self, key: str) -> tuple[int, int]:
        """Increment *key* (the window starts on the first hit) → ``(count, ttl)``."""
        count = int(self._redis.incr(key))
        if count == 1:
            self._redis.expire(key, self.window_s)
        ttl = self._redis.ttl(key)
        if ttl is None or ttl < 0:
            ttl = self.window_s
        return count, ttl

    # -- public API ----------------------------------------------------------

    def check(self, user_id: str | None, ip: str) -> RateDecision:
        """Count the request; blocked when either bucket exceeds the limit."""
        try:
            if user_id is not None:
                count, ttl = self._count(self._key("user", user_id))
                if count > self.max_requests:
                    return RateDecision(True, ttl or self.window_s)
            count, ttl = self._count(self._key("ip", ip))
            if count > self.max_requests:
                return RateDecision(True, ttl or self.window_s)
            return RateDecision(False, 0)
        except RedisError as exc:
            logger.warning("rate limiter unavailable (%s); failing open", exc.__class__.__name__)
            return RateDecision(False, 0)

    def close(self) -> None:
        """Release the underlying Redis connection pool."""
        self._redis.close()


def user_id_from_authorization(authorization: str | None, secret: str | None) -> str | None:
    """The verified ``sub`` claim of a Bearer token, or ``None``.

    The token is *verified* (signature + expiry) before it may key a user
    bucket — an attacker cannot pin a victim's bucket with a forged token.
    Missing/invalid tokens simply fall back to the IP bucket; the route's
    own auth dependency answers 401 as before (the limiter never masks it).
    """
    if secret is None or authorization is None:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        claims: dict[str, Any] = jwt.decode(token.strip(), secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


class RateLimitMiddleware:
    """Answer **429 + ``Retry-After``** once the per-user or per-IP window
    is exhausted; let exempt paths (``/health``, docs) through always.

    The limiter is read from ``app.state.rate_limiter`` on every request so
    tests can install a deterministic stand-in (the S8.1 throttler pattern)
    without a live Redis.
    """

    def __init__(
        self, app: ASGIApp, api: Any = None, *, exempt: frozenset[str] = EXEMPT_PATHS
    ) -> None:
        self.app = app
        #: The FastAPI instance whose ``app.state`` holds the live limiter.
        self.api = api
        self._exempt = exempt

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self._exempt:
            await self.app(scope, receive, send)
            return
        assert self.api is not None  # wired in create_app
        limiter: RateLimiter = self.api.state.rate_limiter
        secret: str | None = self.api.state.settings.auth_token_secret
        headers = Headers(scope=scope)
        user_id = user_id_from_authorization(headers.get("authorization"), secret)
        decision = limiter.check(user_id, _client_ip(scope))
        if decision.blocked:
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": {
                        "code": "rate_limited",
                        "limit": limiter.max_requests,
                        "retry_after_s": decision.retry_after_s,
                    }
                },
                headers={"Retry-After": str(decision.retry_after_s)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
