"""Integration-config lookups (build bible §19 S7.1, §17).

Single DB entry point for the API's ``integration_configs`` endpoints.
Token *values* never pass through here — only ``token_ref`` (the name of
the secret, e.g. an env-var name), so no code path in this package can
persist or leak a PAT (S7.1 exit: "PAT never appears in logs or audit").
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

__all__ = [
    "delete_integration",
    "get_integration",
    "list_integrations",
    "upsert_integration",
]


def get_integration(
    session: Session, project_id: str, provider: str
) -> models.IntegrationConfig | None:
    """The config row for ``project_id`` + ``provider`` (unique), or ``None``."""
    return (
        session.scalars(
            select(models.IntegrationConfig).where(
                models.IntegrationConfig.project_id == project_id,
                models.IntegrationConfig.provider == provider,
            )
        )
        .unique()
        .first()
    )


def list_integrations(session: Session, project_id: str) -> list[models.IntegrationConfig]:
    """All of a project's integration configs, ordered by provider."""
    rows = session.scalars(
        select(models.IntegrationConfig)
        .where(models.IntegrationConfig.project_id == project_id)
        .order_by(models.IntegrationConfig.provider)
    ).all()
    return list(rows)


def upsert_integration(
    session: Session,
    project_id: str,
    provider: str,
    *,
    base_url: str | None,
    token_ref: str | None,
    enabled: bool,
) -> models.IntegrationConfig:
    """Create-or-update the project's config for *provider* (idempotent PUT)."""
    config = get_integration(session, project_id, provider)
    if config is None:
        config = models.IntegrationConfig(project_id=project_id, provider=provider)
        session.add(config)
    config.base_url = base_url
    config.token_ref = token_ref
    config.enabled = enabled
    config.updated_at = datetime.now(UTC)
    session.flush()
    return config


def delete_integration(session: Session, project_id: str, provider: str) -> bool:
    """Delete the project's config for *provider*; ``False`` when it did not exist."""
    config = get_integration(session, project_id, provider)
    if config is None:
        return False
    session.delete(config)
    session.flush()
    return True
