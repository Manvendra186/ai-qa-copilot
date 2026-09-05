"""Inbound webhook delivery helpers (build bible §19 S7.3).

The S7.3 contract: ``POST /api/v1/webhooks/github`` — HMAC
``X-Hub-Signature-256`` is the auth (verified in the API layer against the
project's webhook secret); a ``pull_request`` ``opened``/``synchronize``
delivery maps ``repository.full_name`` → project → ``regression_analysis``
job (202 + Location); every delivery is recorded in ``webhook_events``
with a **unique** ``delivery_id`` so a re-sent delivery is deduped and
never spawns a second job.

LLM-free (S2.1/S3.3/S5.1/S6.1/S7.1 pattern): DB lookups only.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models

__all__ = ["find_project_by_repository", "record_delivery"]


def _full_name_from_url(url: str | None) -> str | None:
    """``owner/repo`` (lowercase) from a repository URL, or ``None``.

    Accepts the common GitHub forms: ``https://github.com/owner/repo``
    (± ``.git``), ``git@github.com:owner/repo(.git)`` (ssh/scp), and a
    bare ``owner/repo``.
    """
    if not url:
        return None
    cleaned = url.strip()
    for marker in ("://git@github.com/", "github.com/", "github.com:"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[1]
            break
    cleaned = cleaned.strip("/").removesuffix(".git").strip("/")
    owner, sep, repo = cleaned.partition("/")
    if not sep or not owner.strip() or not repo.strip():
        return None
    return f"{owner.strip()}/{repo.strip()}".lower()


def find_project_by_repository(session: Session, owner: str, repo: str) -> models.Project | None:
    """The project whose ``repositories`` row matches ``owner/repo`` (S7.3).

    Webhook payloads carry no local checkout path, so the project is
    resolved from its S7.1 ``repositories.url`` (owner/repo match,
    case-insensitive, URL-form agnostic). Deterministic: projects ordered
    by ``(name, id)`` — the first match wins. V1 limitation (documented):
    if several projects point at the same repository, the first one by
    that ordering receives the webhook.
    """
    target = f"{owner.strip()}/{repo.strip()}".lower()
    if not target or target == "/":
        return None
    projects = session.scalars(
        select(models.Project)
        .join(models.Repository, models.Project.repository_id == models.Repository.id)
        .order_by(models.Project.name, models.Project.id)
    ).all()
    for project in projects:
        repository = project.repository
        if repository is not None and _full_name_from_url(repository.url) == target:
            return project
    return None


def record_delivery(
    session: Session,
    *,
    project_id: str,
    delivery_id: str,
    event: str,
    action: str | None,
    provider: str = "github",
) -> tuple[models.WebhookEvent, bool]:
    """Record a delivery row; ``(existing_row, False)`` when already seen.

    The ``uq_webhook_events_delivery_id`` unique constraint is the dedupe
    gate (S7.3 exit: "duplicate delivery id → 200 with no second job") —
    the first insert wins even under a race. The caller commits (or rolls
    back) the row; nothing here commits.
    """
    row = models.WebhookEvent(
        project_id=project_id,
        provider=provider,
        delivery_id=delivery_id,
        event=event,
        action=action,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(models.WebhookEvent).where(models.WebhookEvent.delivery_id == delivery_id)
        )
        if existing is None:  # pragma: no cover — the constraint fired, a row exists
            raise
        return existing, False
    return row, True
