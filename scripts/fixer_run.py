"""S4.2 — run the Fix Agent live gate against the local LLM + demo app.

    uv run python scripts/fixer_run.py [--report reports/fixer_v1.json]

Reads ``LLM_BASE_URL`` / ``LLM_MODEL`` from ``.env`` (same convention as
``scripts/investigator_run.py``; real environment variables win) and
delegates to ``qa_copilot_ai.fixer.cli.main``: the JSON report goes to
stdout (and ``--report``), the human summary to stderr.

Exit codes: ``0`` gate passed (≥ 5/10 applicable + passing + correct
action, §31.7) · ``1`` gate missed · ``2`` configuration error.
See ``tests/unit/test_fixer.py`` for the offline test suite.
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    from qa_copilot_ai.config import load_dotenv
    from qa_copilot_ai.fixer import cli as fixer_cli

    load_dotenv(_ROOT / ".env")
    return fixer_cli.main()


if __name__ == "__main__":
    sys.exit(main())
