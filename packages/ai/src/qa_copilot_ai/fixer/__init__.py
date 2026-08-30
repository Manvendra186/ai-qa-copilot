"""S4.2 Fix Agent — deterministic patch utilities, eval runner, live verifier.

The Fix Agent (build bible §19 S4.2) turns S4.1 diagnoses into **reviewable
test-side patches** or explicit declines (the §26 category guard: never
auto-heal, never "fix" a test to hide a product bug). The deterministic
core of the S4.2 gate lives here:

- :mod:`~qa_copilot_ai.fixer.app_context` — deterministic read-only app
  context for the Fix Agent prompt (the §23 app under test);
- :mod:`~qa_copilot_ai.fixer.patch` — unified-diff make/parse/apply (the
  "applicable" contract — a patch that does not apply fails loud);
- :mod:`~qa_copilot_ai.fixer.runner` — the golden-set eval runner (the
  "passing" check is an injected :data:`FixVerifier`);
- :mod:`~qa_copilot_ai.fixer.live` — the live Playwright verifier (the
  patched spec really runs against the demo app);
- :mod:`~qa_copilot_ai.fixer.cli` — the S4.2 live-gate CLI.
"""

from .app_context import DEFAULT_MAX_CHARS, build_app_context
from .patch import PatchError, apply_patch, make_patch, parse_hunks
from .runner import (
    FixEvalReport,
    FixTotals,
    FixtureFixResult,
    FixVerifier,
    run_fix_eval,
)

__all__ = [
    "DEFAULT_MAX_CHARS",
    "FixtureFixResult",
    "FixEvalReport",
    "FixTotals",
    "FixVerifier",
    "PatchError",
    "apply_patch",
    "build_app_context",
    "make_patch",
    "parse_hunks",
    "run_fix_eval",
]
