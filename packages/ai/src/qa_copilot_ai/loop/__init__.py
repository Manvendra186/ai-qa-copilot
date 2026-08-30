"""Approve → re-run loop (S4.3, build bible §19 Phase 4) — the full loop E2E.

The MVP contract (§20): the proposed code fix **can be reviewed and
approved**, and the fixed test **can be re-run**. This package closes the
loop on one failing test:

    S3 run (broken spec) → S3.3 normalize → S4.1 diagnose → S4.2 propose
    → human approval (§26: no auto-heal) → patch applies (S4.2 contract)
    → S3 re-run (patched spec) → closed when it passes (or was correctly
    declined / rejected / left un-fixed — always reported, never faked).

Layout:

- :mod:`~qa_copilot_ai.loop.approval` — the §26 human decision gate
  (explicit ``approve``/``reject``, interactive ``y/n``, fail-safe reject
  without a TTY) + the auditable decision record;
- :mod:`~qa_copilot_ai.loop.runner` — :func:`run_fix_loop` + the
  ``LoopReport`` stable JSON contract (injected spec runner → offline
  testable);
- :mod:`~qa_copilot_ai.loop.live` — the live spec executor (Playwright
  against the demo app, reusing the S4.2 verifier's stack management);
- :mod:`~qa_copilot_ai.loop.cli` — ``python -m qa_copilot_ai.loop.cli``
  (or ``scripts/loop_run.py``).

Local tooling, same posture as the S4.1/S4.2 runners (§19): no DB; the
caller persists and serves the report.
"""

from .approval import APPROVE, REJECT, ApprovalDecision, default_prompt, resolve_approval
from .live import PlaywrightLoopRunner, SpecVerifier
from .runner import (
    CLOSED_OUTCOMES,
    LoopFixer,
    LoopInvestigator,
    LoopReport,
    LoopSpecRunner,
    LoopTarget,
    SpecRun,
    exit_code_for,
    run_fix_loop,
)

__all__ = [
    "APPROVE",
    "CLOSED_OUTCOMES",
    "REJECT",
    "ApprovalDecision",
    "LoopFixer",
    "LoopInvestigator",
    "LoopReport",
    "LoopSpecRunner",
    "LoopTarget",
    "PlaywrightLoopRunner",
    "SpecRun",
    "SpecVerifier",
    "default_prompt",
    "exit_code_for",
    "resolve_approval",
    "run_fix_loop",
]
