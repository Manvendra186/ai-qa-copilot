"""Auth baseline (build bible §31.3 — S0.8).

Dev-mode single user + JWT, project-scoped roles ``owner`` / ``member`` /
``viewer``:

- ``POST /api/v1/auth/login`` (email + password) → HS256 access token
  (PyJWT); passwords are PBKDF2-SHA256 (stdlib ``hashlib``, 390k
  iterations — OWASP's 2023 minimum for PBKDF2-SHA256), stored in
  ``users.password_hash``.
- ``Authorization: Bearer <token>`` is verified by :func:`get_current_user`.
- :func:`require_role` enforces project-scoped RBAC against
  ``project_members`` (§31.3: code apply/approve needs ``member``+, project
  deletion needs ``owner``). ``users.role`` is only a default and is never
  used for authorization.

The HS256 secret comes from ``Settings.auth_token_secret``
(``AUTH_TOKEN_SECRET`` env var, 16+ chars) — fail loud if unset, no
fallback key in code. SSO / full RBAC stay in Phase 8 (§31.3).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from qa_copilot_domain.enums import ProjectRole, role_at_least
from qa_copilot_repository import models
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db

__all__ = [
    "AuthError",
    "check_password",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "hash_password",
    "require_role",
    "verify_password",
]

#: PBKDF2-SHA256 work factor (OWASP 2023 minimum for PBKDF2-SHA256).
_PBKDF2_ITERATIONS = 390_000
_SALT_BYTES = 16
#: Access-token lifetime (dev baseline; refresh/rotation is Phase 8).
TOKEN_TTL = timedelta(hours=8)


class AuthError(HTTPException):
    """401 for anything auth-related (missing/bad/expired token, no subject)."""

    def __init__(self, detail: str = "invalid or expired token") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _require_secret(settings: Settings) -> str:
    """The HS256 signing secret, or a loud failure (no fallback key)."""
    secret = settings.auth_token_secret
    if not secret or len(secret) < 16:
        raise RuntimeError(
            "AUTH_TOKEN_SECRET must be set to a value of 16+ characters. "
            'Generate one with: python -c "import secrets; '
            'print(secrets.token_hex(32))"'
        )
    return secret


# --- passwords (PBKDF2-SHA256, stdlib only) ---------------------------------


def hash_password(password: str) -> str:
    """Hash *password* into ``pbkdf2_sha256$<iter>$<salt>$<digest>`` (hex parts)."""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check of *password* against a :func:`hash_password` output.

    Malformed *stored* values return ``False`` (never raise).
    """
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# Pre-computed once at import (~100 ms) so unknown-user logins take the same
# path as known-user logins (no timing leak on which emails exist).
_DUMMY_HASH = hash_password("dummy")


def check_password(password: str, stored: str | None) -> bool:
    """``verify_password`` that is safe when the user row (or hash) is missing."""
    return verify_password(password, stored or _DUMMY_HASH)


# --- JWT (HS256, PyJWT) -------------------------------------------------------


def create_access_token(user_id: str, email: str, secret: str) -> str:
    """Sign a short-lived HS256 access token for *user_id* (claims: sub/email/iat/exp)."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    """Verify signature + expiry; returns the claims (``sub`` is the user id)."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc.__class__.__name__}") from exc


# --- FastAPI dependencies ------------------------------------------------------


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("missing Bearer token")
    return token.strip()


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> models.User:
    """Resolve the authenticated user from the ``Authorization: Bearer`` header."""
    settings: Settings = request.app.state.settings
    secret = _require_secret(settings)
    claims = decode_access_token(_bearer_token(request), secret)
    user_id = claims.get("sub")
    if not isinstance(user_id, str):
        raise AuthError("token has no subject")
    user = db.get(models.User, user_id)
    if user is None:
        raise AuthError("token subject no longer exists")
    return user


def require_role(minimum: ProjectRole) -> Callable[..., tuple[models.User, str]]:
    """FastAPI dependency factory: project-scoped RBAC (build bible §31.3).

    Usage (the route must have a ``{project_id}`` path parameter)::

        @router.delete("/projects/{project_id}")
        def delete(ctx: tuple[models.User, str] = Depends(require_role(ProjectRole.OWNER))):
            user, project_id = ctx
            ...

    * ``member`` or above → code apply/approve (§31.3).
    * ``owner`` → project deletion / destructive ops (§31.3).
    * Non-members and roles below *minimum* get 403 (auth runs before lookup,
      so unknown projects also 403 for non-members — no existence leak).
    """

    def dependency(
        project_id: str,
        user: models.User = Depends(get_current_user),  # noqa: B008
        db: Session = Depends(get_db),  # noqa: B008
    ) -> tuple[models.User, str]:
        role = db.scalars(
            select(models.ProjectMember.role).where(
                models.ProjectMember.project_id == project_id,
                models.ProjectMember.user_id == user.id,
            )
        ).first()
        if role is None:
            raise HTTPException(status_code=403, detail="no role for this project")
        if not role_at_least(ProjectRole(role), minimum):
            raise HTTPException(
                status_code=403, detail=f"requires {minimum.value} role (has {role})"
            )
        return user, project_id

    return dependency
