"""``ai_actions`` audit recorder (build bible §31.1, §31.5).

"One row per model call: model, tokens in/out, latency, approval status."
The AI gateway produces the payload (:class:`AICallResult`); this module
persists it. §31.5: redacted prompt/completion snapshots go to
``ai_actions.output_ref`` (store the reference, not the raw text).
"""

from __future__ import annotations

from qa_copilot_ai.gateway import AICallResult
from sqlalchemy.orm import Session

from . import models

__all__ = ["record_ai_action", "record_ai_call"]


def record_ai_action(
    session: Session,
    *,
    session_id: str,
    agent: str,
    model: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    input_hash: str | None = None,
    tool: str | None = None,
    output_ref: str | None = None,
    approval_status: str | None = None,
) -> models.AIAction:
    """Insert one ``ai_actions`` row (flushed, not committed)."""
    action = models.AIAction(
        session_id=session_id,
        agent=agent,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        input_hash=input_hash,
        tool=tool,
        output_ref=output_ref,
        approval_status=approval_status,
    )
    session.add(action)
    session.flush()
    return action


def record_ai_call(
    session: Session,
    *,
    session_id: str,
    result: AICallResult,
    tool: str | None = None,
    output_ref: str | None = None,
) -> models.AIAction:
    """Persist one gateway :class:`AICallResult` as an ``ai_actions`` row."""
    return record_ai_action(
        session,
        session_id=session_id,
        agent=result.agent,
        model=result.model,
        tokens_in=result.usage.tokens_in,
        tokens_out=result.usage.tokens_out,
        latency_ms=result.latency_ms,
        input_hash=result.input_hash,
        tool=tool,
        output_ref=output_ref,
    )
