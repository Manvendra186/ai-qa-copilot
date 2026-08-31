#!/usr/bin/env python3
"""S5.4 live gate — Knowledge Q&A agent vs the qa_v1 golden set.

Thin wrapper over :mod:`qa_copilot_ai.knowledge_qa.cli` that loads the repo
``.env`` first (so ``LLM_BASE_URL`` / ``LLM_MODEL`` work without exporting
them), mirroring :mod:`scripts.investigator_run`.

Exit codes: ``0`` gates met · ``1`` run completed, gates missed ·
``2`` configuration/usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo root — this file lives in ``scripts/``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qa_copilot_ai.config import load_dotenv
from qa_copilot_ai.knowledge_qa.cli import main

_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_ROOT / ".env")

if __name__ == "__main__":
    sys.exit(main())
