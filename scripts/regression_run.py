"""S6.3 — run the deterministic regression recommender eval (golden gate).

    uv run python scripts/regression_run.py [--report reports/regression_v1.json]
    uv run python scripts/regression_run.py --advise   # attach the optional LLM brief

Reads ``LLM_BASE_URL`` / ``LLM_MODEL`` from ``.env`` (same convention as
``scripts/investigator_run.py``; real environment variables win) and delegates
to ``qa_copilot_ai.regression.cli.main``: the JSON report goes to stdout (and
``--report``), the human summary to stderr.

The S6.3 gate is the deterministic core (no LLM) matching the golden set 100%;
``--advise`` only attaches the optional advisor brief (LLM if configured, else
the deterministic stub) and never affects the gate.

Exit codes: ``0`` §31.7 gate met (100% order match) · ``1`` gate missed ·
``2`` configuration error. See ``tests/unit/test_regression.py`` for the
offline test suite.
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    from qa_copilot_ai.config import load_dotenv
    from qa_copilot_ai.regression import cli as regression_cli

    load_dotenv(_ROOT / ".env")
    return regression_cli.main()


if __name__ == "__main__":
    sys.exit(main())
