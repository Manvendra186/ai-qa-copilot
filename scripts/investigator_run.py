"""S4.1 — run the Failure Investigator eval against the local LLM.

    uv run python scripts/investigator_run.py [--report reports/investigator_v1.json]

Reads ``LLM_BASE_URL`` / ``LLM_MODEL`` from ``.env`` (same convention as
``scripts/eval_run.py``; real environment variables win) and delegates to
``qa_copilot_ai.investigator.cli.main``: the JSON report goes to stdout
(and ``--report``), the human summary to stderr.

Exit codes: ``0`` targets met (top-1 ≥ 80%, §31.7) · ``1`` targets missed
· ``2`` configuration error. See
``tests/unit/test_failure_investigator.py`` for the offline test suite.
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    from qa_copilot_ai.config import load_dotenv
    from qa_copilot_ai.investigator import cli as investigator_cli

    load_dotenv(_ROOT / ".env")
    return investigator_cli.main()


if __name__ == "__main__":
    sys.exit(main())
