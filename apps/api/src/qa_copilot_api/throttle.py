"""Redis-backed login brute-force throttling (build bible §19 S8.1).

Fixed-window **failure** counters in Redis (``INCR`` + window ``EXPIRE``),
tracked per **email** *and* per **IP**. Once either counter reaches the
configured maximum, logins for that email/IP answer ``429`` + ``Retry-After``
until the window expires — no password check, no timing signal. A
*successful* login resets both counters.

Design notes:

- **Fail-open** on Redis errors: this is a local-first dev setup and
  availability wins; S8.5 adds deployment-grade rate limiting on top.
- Only *failures* are counted (not attempts), so a legitimate retry after
  one typo is not throttled.
- Secrets never appear in the keys or in log lines (§17); the keys are
  ``qa_copilot:login_fail:{email|ip}:<value>``.
"""

from __future__ import annotations

import logging

from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

__all__ = ["LoginThrottler", "ThrottleDecision"]

_KEY_PREFIX = "qa_copilot:login_fail"


class ThrottleDecision:
    """Outcome of a throttle check (``blocked`` + how long to wait)."""

    __slots__ = ("blocked", "retry_after_s")

    def __init__(self, blocked: bool, retry_after_s: int) -> None:
        self.blocked = blocked
        self.retry_after_s = retry_after_s


class LoginThrottler:
    """Fixed-window failed-login counters (per email + per IP) in Redis."""

    def __init__(self, url: str, max_failures: int = 5, window_s: int = 60) -> None:
        self._max_failures = max(1, max_failures)
        self._window_s = max(1, window_s)
        self._redis = Redis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
        )

    # -- internals -----------------------------------------------------------

    def _keys(self, email: str, ip: str) -> tuple[str, str]:
        return (f"{_KEY_PREFIX}:email:{email.lower()}", f"{_KEY_PREFIX}:ip:{ip}")

    def _counter(self, key: str) -> tuple[int, int]:
        """``(count, ttl)`` of *key*; a missing key is ``(0, 0)``."""
        raw = self._redis.get(key)
        count = int(raw) if raw is not None else 0
        ttl = self._redis.ttl(key)
        if ttl is None or ttl < 0:
            ttl = self._window_s
        return count, ttl

    # -- public API -----------------------------------------------------------

    def check(self, email: str, ip: str) -> ThrottleDecision:
        """Blocked when *either* counter has already reached the maximum."""
        try:
            for key in self._keys(email, ip):
                count, ttl = self._counter(key)
                if count >= self._max_failures:
                    return ThrottleDecision(True, ttl or self._window_s)
            return ThrottleDecision(False, 0)
        except RedisError as exc:
            logger.warning(
                "login throttle unavailable (%s); failing open", exc.__class__.__name__
            )
            return ThrottleDecision(False, 0)

    def record_failure(self, email: str, ip: str) -> None:
        """Count one failed login for both the email and the IP.

        The window starts on the first failure (``INCR`` returns 1) and
        slides nothing — the TTL only expires on its own (fixed window).
        """
        try:
            for key in self._keys(email, ip):
                if self._redis.incr(key) == 1:
                    self._redis.expire(key, self._window_s)
        except RedisError as exc:
            logger.warning(
                "login throttle unavailable (%s); failure not counted",
                exc.__class__.__name__,
            )

    def reset(self, email: str, ip: str) -> None:
        """Clear both counters (successful login)."""
        try:
            self._redis.delete(*self._keys(email, ip))
        except RedisError as exc:
            logger.warning("login throttle unavailable (%s); reset skipped", exc.__class__.__name__)

    def close(self) -> None:
        """Release the underlying Redis connection pool."""
        self._redis.close()