"""S4.3 — run the full S3 → S4 → approval → re-run loop live.

    uv run python scripts/loop_run.py --approve [--report reports/loop_v1.json]
    uv run python scripts/loop_run.py --reject  # fail-safe: nothing applied

Reads ``LLM_BASE_URL`` / ``LLM_MODEL`` from ``.env`` (same convention as
``scripts/fixer_run.py``; real environment variables win) and delegates
to ``qa_copilot_ai.loop.cli.main``: the JSON report goes to stdout (and
``--report``), the human summary to stderr.

Approval: pass ``--approve`` (apply + re-run) or ``--reject`` (decline);
without either, an interactive y/n appears on a TTY — and with piped
stdin the loop fail-safes to reject (§26: no auto-heal).

Exit codes: ``0`` loop closed (``fixed`` · ``declined`` · ``passing``) ·
``1`` loop ran but did not close (``rejected`` · ``not_fixed``) ·
``2`` configuration/usage/LLM/patch error.
See ``tests/unit/test_fix_loop.py`` for the offline test suite.
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    from qa_copilot_ai.config import load_dotenv
    from qa_copilot_ai.loop import cli as loop_cli

    load_dotenv(_ROOT / ".env")
    return loop_cli.main()


if __name__ == "__main__":
    sys.exit(main())
