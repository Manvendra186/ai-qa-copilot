"""One-time, code-based organization invites (build bible §19 S8.2).

The S8.2 contract (local-first, §29 — no SMTP): the org **owner** creates an
invite for an email + role and receives a single-use ``code`` (returned
exactly once; only its SHA-256 hash is stored, §17). The invitee — an
authenticated account whose email matches the invite — accepts it and joins
the org with the invited role.

Lifecycle rules enforced here (the API maps the taxonomy to HTTP):

- the code is **single-use**: accepting it sets ``accepted_at``; a reused
  code is rejected (404 — the reason is never leaked, §31.3 rule);
- the code **expires 7 days out** (``INVITE_TTL``); an expired code is
  rejected (404, same shape as unknown — no reason leak);
- the accepting account's email must **match the invite's email** (403 —
  the code alone is not a transferable credential);
- a user who is already a member of the org gets 409 on accept.

LLM-free (S2.1/S3.3/S5.1/S6.1/S7.3 pattern): DB + stdlib crypto only.
Nothing here commits — the API layer owns the transaction (S7.3 pattern).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from qa_copilot_domain.enums import OrgRole
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "INVITE_TTL",
    "InviteError",
    "InviteErrorKind",
    "accept_invite",
    "find_invite_by_code",
    "hash_invite_code",
    "issue_invite",
    "new_invite_code",
]

#: S8.2 (bible §19): invite codes expire 7 days out.
INVITE_TTL = timedelta(days=7)


def new_invite_code() -> str:
    """A fresh 128-bit URL-safe invite code (a single-use secret, §17)."""
    return secrets.token_urlsafe(16)


def hash_invite_code(code: str) -> str:
    """SHA-256 hex digest — only this is ever persisted (S8.1 pattern, §17)."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class InviteErrorKind:
    """Why an invite action failed (the API maps these to HTTP statuses)."""

    #: Code unknown, already consumed, or expired → 404 (no reason leak).
    UNKNOWN = "unknown"
    #: Accepting account's email differs from the invite's email → 403.
    EMAIL_MISMATCH = "email_mismatch"
    #: The account is already a member of the invited org → 409.
    ALREADY_MEMBER = "already_member"


class InviteError(Exception):
    """A failed invite action, tagged with its :class:`InviteErrorKind`."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def find_invite_by_code(session: Session, code: str) -> models.OrganizationInvite | None:
    """The invite row for *code* (hashed lookup), or ``None``."""
    return session.scalar(
        select(models.OrganizationInvite).where(
            models.OrganizationInvite.code_hash == hash_invite_code(code)
        )
    )


def issue_invite(
    session: Session,
    *,
    organization_id: str,
    email: str,
    role: OrgRole,
    now: datetime,
) -> tuple[models.OrganizationInvite, str]:
    """Create an invite row (expiring 7 days after *now*); return ``(row, code)``.

    The plaintext ``code`` is returned **once** — only its SHA-256 hash is
    stored (§17). The caller commits (nothing here commits, S7.3 pattern).
    """
    code = new_invite_code()
    row = models.OrganizationInvite(
        organization_id=organization_id,
        email=email,
        role=role,
        code_hash=hash_invite_code(code),
        expires_at=now + INVITE_TTL,
    )
    session.add(row)
    session.flush()
    return row, code


def accept_invite(
    session: Session,
    *,
    code: str,
    user: models.User,
    now: datetime,
) -> tuple[models.OrganizationMember, models.Organization]:
    """Validate + consume the invite and insert the org membership row.

    Returns the new ``OrganizationMember`` row and the organization.
    Raises :class:`InviteError` on any failure (see :class:`InviteErrorKind`
    for the taxonomy and the HTTP mapping in the API layer). The caller
    commits; nothing here commits.
    """
    invite = find_invite_by_code(session, code)
    if invite is None or invite.accepted_at is not None:
        # Unknown, already consumed, or expired — all 404 (no reason leak,
        # §31.3): leaking "expired" vs "used" vs "unknown" is an info oracle.
        raise InviteError(
            InviteErrorKind.UNKNOWN,
            "invalid or expired invite code",
        )
    if now >= invite.expires_at:
        raise InviteError(
            InviteErrorKind.UNKNOWN,
            "invalid or expired invite code",
        )
    if user.email != invite.email:
        raise InviteError(
            InviteErrorKind.EMAIL_MISMATCH,
            "this invite was issued for a different email",
        )
    existing = session.scalar(
        select(models.OrganizationMember).where(
            and_(
                models.OrganizationMember.organization_id == invite.organization_id,
                models.OrganizationMember.user_id == user.id,
            )
        )
    )
    if existing is not None:
        raise InviteError(InviteErrorKind.ALREADY_MEMBER, "already a member of this organization")

    member = models.OrganizationMember(
        organization_id=invite.organization_id,
        user_id=user.id,
        role=invite.role,
    )
    invite.accepted_at = now
    invite.accepted_by = user.id
    session.add(member)
    session.flush()
    org = session.get(models.Organization, invite.organization_id)
    if org is None:  # pragma: no cover — FK keeps the org alive
        raise RuntimeError("invited organization vanished mid-transaction")
    return member, org
