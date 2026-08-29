"""S1.4 — run the golden set eval against the local LLM.

    uv run python scripts/eval_run.py [--report reports/eval_v1.json]

Reads ``LLM_BASE_URL`` / ``LLM_MODEL`` from ``.env`` (same convention as
``scripts/llm_live_check.py``; real environment variables win) and delegates
to ``qa_copilot_ai.eval.main``: the JSON report goes to stdout (and
``--report``), the human summary to stderr.

Exit codes: ``0`` targets met · ``1`` targets missed · ``2`` configuration
error. See ``tests/unit/test_eval_runner.py`` for the offline test suite.
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    from qa_copilot_ai.config import load_dotenv
    from qa_copilot_ai.eval import main as eval_main

    load_dotenv(_ROOT / ".env")
    return eval_main()


if __name__ == "__main__":
    sys.exit(main())
