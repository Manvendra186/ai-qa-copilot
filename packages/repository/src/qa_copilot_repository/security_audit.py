"""Security audit trail — append-only core (build bible §19 S8.3, §17).

One ``audit_log`` row per audited security event: who (*actor_id*, ``NULL``
when the actor is unknown — e.g. a login failure), what (*action*), against
what (*target*: an org / project / user id or email — never a credential),
the *outcome*, the client *ip* and the server *at* timestamp.

Append-only by contract (S8.3 "audit trail"): this module only inserts
(:func:`record`) and reads (:func:`list_for_target`) — there is no update
or delete path here, and the API exposes no mutating ``/audit`` endpoint.
Rows outlive their actors: ``actor_id`` is ON DELETE SET NULL, so
self-deletion and org deletion never erase history ("audit rows remain",
§17).

Nothing here commits — the API layer owns the transaction (S7.3 pattern):
success events land in the caller's commit; failure events are committed
explicitly by the route before the error response.
"""

from __future__ import annotations

from qa_copilot_domain.enums import AuditAction, AuditOutcome
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

__all__ = ["DEFAULT_LIMIT", "list_for_target", "record"]

#: Cap for the org audit export (S8.3 "audit trail export").
DEFAULT_LIMIT = 200


def record(
    session: Session,
    *,
    actor_id: str | None,
    action: AuditAction,
    target: str | None,
    outcome: AuditOutcome,
    ip: str | None,
) -> models.AuditLog:
    """Insert one ``audit_log`` row (flushed, not committed).

    *actor_id* may be ``None`` (login failure — the caller is unknown).
    *target* is an opaque identifier (org / project / user id or email) —
    never a credential, token or password (§17: the audit log carries no
    secrets).
    """
    row = models.AuditLog(
        actor_id=actor_id,
        action=action,
        target=target,
        outcome=outcome,
        ip=ip,
    )
    session.add(row)
    session.flush()
    return row


def list_for_target(
    session: Session, target: str, *, limit: int = DEFAULT_LIMIT
) -> list[models.AuditLog]:
    """The newest *limit* rows for *target*, newest first (S8.3 export)."""
    return list(
        session.scalars(
            select(models.AuditLog)
            .where(models.AuditLog.target == target)
            .order_by(models.AuditLog.at.desc(), models.AuditLog.id.desc())
            .limit(limit)
        )
    )
