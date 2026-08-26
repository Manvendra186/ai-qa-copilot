"""DB-backed prompt-registry loader (build bible §31.6).

Agents reference prompts by ``name@version``; the runtime resolves them from
the ``prompt_versions`` table. The shared types (``PromptSpec``,
``PromptNotFound``) live in ``qa_copilot_ai.prompts`` so agents and data
access speak one language.
"""

from __future__ import annotations

from qa_copilot_ai.prompts import PromptNotFound, PromptSpec
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models

__all__ = ["load_prompt"]


def load_prompt(session: Session, name: str, version: int | None = None) -> PromptSpec:
    """Resolve ``name@version`` (or latest *version*) to a :class:`PromptSpec`.

    Raises :class:`PromptNotFound` when the pair is not registered — a
    missing prompt is a configuration error and must fail loud (§31.6).
    """
    stmt = select(models.PromptVersion).where(models.PromptVersion.name == name)
    if version is None:
        stmt = stmt.order_by(models.PromptVersion.version.desc())
    else:
        stmt = stmt.where(models.PromptVersion.version == version)
    row = session.scalars(stmt).first()
    if row is None:
        raise PromptNotFound(f"{name}@{version if version is not None else 'latest'}")
    return PromptSpec(
        name=row.name,
        version=row.version,
        body=row.body,
        model_class=row.model_class,
        input_budget=row.input_budget,
        output_budget=row.output_budget,
        schema_ref=row.schema_ref,
        temperature=row.temperature,
    )
