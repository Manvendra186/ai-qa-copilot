"""Human approval gate for the S4.3 Approve → re-run loop (§26: no auto-heal).

Build bible §20 (MVP): the proposed code fix **can be reviewed and
approved**. This module is that gate — it resolves the operator's
decision into an auditable :class:`ApprovalDecision`:

* explicit ``approve`` / ``reject`` — the automation/CI path (always wins);
* interactive — a TTY with no explicit flag shows the patch for review and
  asks ``y/n`` (Enter or EOF ⇒ reject);
* **fail-safe** — no explicit flag and no TTY (piped stdin) ⇒ *reject*:
  a patch is never auto-applied (§26, §19 S4.3).

The resolved decision is part of the loop report (§29 audit trail:
*who* approved *how*).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

#: The explicit decision values (``--approve`` / ``--reject``).
APPROVE = "approve"
REJECT = "reject"

__all__ = [
    "APPROVE",
    "REJECT",
    "ApprovalDecision",
    "default_prompt",
    "resolve_approval",
]


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """The resolved decision + how it was made (the §29 audit trail).

    ``decided_by`` values: ``explicit:approve`` · ``explicit:reject`` ·
    ``interactive:yes`` · ``interactive:no`` · ``interactive:other`` ·
    ``auto:reject-no-tty``.
    """

    approved: bool
    decided_by: str


def default_prompt(patch: str) -> str:
    """The interactive gate: show the patch for review, then ask y/n."""
    print("\n--- proposed patch (review before approving) ---", file=sys.stderr)
    print(patch, file=sys.stderr)
    try:
        return input("Approve this patch and re-run the fixed test? [y/N] ")
    except EOFError:  # stdin closed — treat as "no" (fail-safe, §26)
        return ""


def resolve_approval(
    decision: str | None,
    *,
    is_tty: bool,
    patch: str = "",
    prompt: Callable[[str], str] = default_prompt,
) -> ApprovalDecision:
    """Resolve the operator's decision into an :class:`ApprovalDecision`.

    Explicit ``approve``/``reject`` always wins (automation/CI path).
    Otherwise: with a TTY the operator is asked interactively (``y``/
    ``yes`` ⇒ approve; anything else, Enter, or EOF ⇒ reject). Without a
    TTY (piped stdin) the loop **fail-safes to reject** — §26: a patch is
    never auto-applied.

    *prompt* receives the patch text and returns the raw answer (inject
    for tests). An unknown explicit *decision* raises ``ValueError`` — a
    typo must fail loud, not silently pick a side.
    """
    if decision == APPROVE:
        return ApprovalDecision(approved=True, decided_by="explicit:approve")
    if decision == REJECT:
        return ApprovalDecision(approved=False, decided_by="explicit:reject")
    if decision is not None:
        raise ValueError(f"unknown approval decision: {decision!r} (use {APPROVE!r} or {REJECT!r})")
    if not is_tty:
        return ApprovalDecision(approved=False, decided_by="auto:reject-no-tty")
    answer = prompt(patch).strip().lower()
    if answer in {"y", "yes"}:
        return ApprovalDecision(approved=True, decided_by="interactive:yes")
    if answer in {"n", "no", ""}:
        return ApprovalDecision(approved=False, decided_by="interactive:no")
    return ApprovalDecision(approved=False, decided_by="interactive:other")
