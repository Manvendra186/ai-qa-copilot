"""Auth baseline (build bible §31.3 — S0.8) + S8.1 hardening.

Dev-mode single user + JWT, project-scoped roles ``owner`` / ``member`` /
``viewer``:

- ``POST /api/v1/auth/login`` (email + password) → HS256 access token
  (PyJWT); passwords are PBKDF2-SHA256 (stdlib ``hashlib``, 390k
  iterations — OWASP's 2023 minimum for PBKDF2-SHA256), stored in
  ``users.password_hash``.
- ``Authorization: Bearer <token>`` is verified by :func:`get_current_user`.
- :func:`require_role` enforces project-scoped RBAC (§31.3: code
  apply/approve needs ``member``+, project deletion needs ``owner``).
  ``users.role`` is only a default and is never used for authorization.
  S8.2 (build bible §19): the effective role resolves through
  ``project_members`` first, then falls back to the caller's **org** role
  for the project's organization (org baseline; the explicit row always
  wins) — see
  :func:`qa_copilot_repository.membership.get_project_role`.

S8.1 (build bible §19 S8.1) hardens the baseline without replacing it:

- :func:`password_policy_violations` — the register/change-password policy
  (length + letter + digit); the plaintext is only ever hashed.
- Opaque **rotating** refresh tokens: :func:`issue_refresh_token` /
  :func:`rotate_refresh_token` / :func:`revoke_user_refresh_tokens`.
  Only the SHA-256 hash is persisted (``user_refresh_tokens``); a token
  reused after rotation revokes its whole family (stolen-token rule, §17).

The HS256 secret comes from ``Settings.auth_token_secret``
(``AUTH_TOKEN_SECRET`` env var, 16+ chars) — fail loud if unset, no
fallback key in code. SSO / OAuth providers stay deferred (Enterprise,
§24).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from qa_copilot_domain.enums import (
    AuditAction,
    AuditOutcome,
    OrgRole,
    ProjectRole,
    org_role_at_least,
    role_at_least,
)
from qa_copilot_repository import models
from qa_copilot_repository import security_audit as repo_security_audit
from qa_copilot_repository.membership import get_org_role, get_project_role
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from .config import Settings
from .db import get_db

__all__ = [
    "AuthError",
    "REFRESH_TTL",
    "check_password",
    "create_access_token",
    "decode_access_token",
    "get_current_user",
    "hash_password",
    "issue_refresh_token",
    "new_refresh_token",
    "password_policy_violations",
    "require_org_role",
    "require_role",
    "revoke_user_refresh_tokens",
    "rotate_refresh_token",
    "verify_password",
]

#: PBKDF2-SHA256 work factor (OWASP 2023 minimum for PBKDF2-SHA256).
_PBKDF2_ITERATIONS = 390_000
_SALT_BYTES = 16
#: Access-token lifetime (dev baseline).
TOKEN_TTL = timedelta(hours=8)
#: Refresh-token lifetime (S8.1: opaque, rotating; stored hashed, §17).
REFRESH_TTL = timedelta(days=30)


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
        algorithm, iterations_str, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
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


# --- Password policy (S8.1, §19: register + change-password) --------------------

#: Minimum length for self-service password choices (register / change).
PASSWORD_MIN_LENGTH = 10


def password_policy_violations(password: str) -> list[str]:
    """Human-readable policy violations (empty list = acceptable password).

    Deliberately small and local: length + letter + digit (build bible
    S8.1 "password policy"). The violations never contain the password
    itself (§17 — nothing secret ever reaches a message or log line).
    """
    violations: list[str] = []
    if len(password) < PASSWORD_MIN_LENGTH:
        violations.append(f"be at least {PASSWORD_MIN_LENGTH} characters long")
    if not any(c.isalpha() for c in password):
        violations.append("contain a letter")
    if not any(c.isdigit() for c in password):
        violations.append("contain a digit")
    return violations


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


# --- Refresh tokens (S8.1, §19: opaque, rotating, stored hashed) ----------------


def new_refresh_token() -> str:
    """A fresh opaque refresh token (384 bits of entropy, URL-safe).

    The plaintext is returned exactly once (login/refresh response) and is
    never logged, audited or persisted — only its :func:`hash_refresh_token`
    lands in the database (§17).
    """
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hex digest of *token* — the only form stored (§17)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_refresh_token(
    db: Session, user_id: str, family_id: str | None = None
) -> tuple[str, models.UserRefreshToken]:
    """Store a new refresh token for *user_id*; return ``(plaintext, row)``.

    *family_id* groups one login chain: a login mints a new family, while
    :func:`rotate_refresh_token` keeps the family so reuse detection works
    across rotations. The plaintext is returned exactly once — the database
    holds only its SHA-256 hash (§17).
    """
    token = new_refresh_token()
    row = models.UserRefreshToken(
        user_id=user_id,
        family_id=family_id or str(uuid.uuid4()),
        token_hash=hash_refresh_token(token),
        expires_at=datetime.now(UTC) + REFRESH_TTL,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return token, row


def rotate_refresh_token(db: Session, presented: str) -> tuple[models.User, str]:
    """Validate *presented*, revoke it, and issue its family successor.

    Returns ``(user, new_plaintext)``. Rejections (all 401):

    - unknown token;
    - **reuse after rotation** (token already revoked) → the *whole family*
      is revoked, so a stolen token kills every live successor;
    - expired token.
    """
    row = db.scalar(
        select(models.UserRefreshToken).where(
            models.UserRefreshToken.token_hash == hash_refresh_token(presented)
        )
    )
    if row is None:
        raise AuthError("invalid refresh token")
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        db.execute(
            sa_update(models.UserRefreshToken)
            .where(
                models.UserRefreshToken.family_id == row.family_id,
                models.UserRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        db.commit()
        raise AuthError("refresh token reuse detected; token family revoked")
    if row.expires_at <= now:
        raise AuthError("refresh token expired")
    row.revoked_at = now
    db.commit()
    user = db.get(models.User, row.user_id)
    if user is None:
        raise AuthError("refresh token subject no longer exists")
    token, _ = issue_refresh_token(db, user.id, family_id=row.family_id)
    return user, token


def revoke_user_refresh_tokens(db: Session, user_id: str) -> None:
    """Revoke every active refresh token of *user_id* (password change, S8.1)."""
    db.execute(
        sa_update(models.UserRefreshToken)
        .where(
            models.UserRefreshToken.user_id == user_id,
            models.UserRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


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
        # S8.2 (bible §19): explicit project_members row wins, else the
        # caller's org role is the baseline — resolved in the repository
        # package (LLM-free core).
        role = get_project_role(db, project_id, user.id)
        if role is None:
            raise HTTPException(status_code=403, detail="no role for this project")
        if not role_at_least(ProjectRole(role), minimum):
            raise HTTPException(
                status_code=403, detail=f"requires {minimum.value} role (has {role})"
            )
        return user, project_id

    return dependency


def require_org_role(minimum: OrgRole) -> Callable[..., tuple[models.User, str]]:
    """FastAPI dependency factory: org-scoped RBAC (build bible §19 S8.3).

    Usage (the route must have an ``{organization_id}`` path parameter)::

        @router.delete("/organizations/{organization_id}")
        def delete_org(
            ctx: tuple[models.User, str] = Depends(require_org_role(OrgRole.OWNER)),
        ):
            user, organization_id = ctx
            ...

    * ``OrgRole.MEMBER`` → roster read, self-leave (any member).
    * ``OrgRole.OWNER`` → membership changes, invites, deletion, audit
      export (S8.2/S8.3).

    Non-members and roles below *minimum* get 403 (auth runs before lookup,
    so unknown orgs also 403 for non-members — no existence leak). The
    denial itself is audited (``org.gate.denied``, §17). Because FastAPI
    resolves dependencies before body validation, RBAC (403/401) always
    takes precedence over a malformed body (422).
    """

    def dependency(
        organization_id: str,
        user: models.User = Depends(get_current_user),  # noqa: B008
        db: Session = Depends(get_db),  # noqa: B008
    ) -> tuple[models.User, str]:
        role = get_org_role(db, organization_id, user.id)
        if role is None:
            _audit_org_gate_denied(db, user, organization_id)
            raise HTTPException(status_code=403, detail="not a member of this organization")
        if not org_role_at_least(OrgRole(role), minimum):
            _audit_org_gate_denied(db, user, organization_id)
            raise HTTPException(
                status_code=403, detail=f"requires {minimum.value} org role (has {role})"
            )
        return user, organization_id

    return dependency


def _audit_org_gate_denied(db: Session, user: models.User, organization_id: str) -> None:
    """S8.3: a denied org RBAC gate (403) is part of the audit trail (§17)."""
    repo_security_audit.record(
        db,
        actor_id=user.id,
        action=AuditAction.ORG_GATE_DENIED,
        target=organization_id,
        outcome=AuditOutcome.DENIED,
        ip=None,
    )
    db.commit()
