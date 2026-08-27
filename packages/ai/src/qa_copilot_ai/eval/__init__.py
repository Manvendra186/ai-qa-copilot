"""S1.4 evaluation — golden set (build bible §22) + eval runner + CLI.

One dataset, two consumers (§22 "build early"):

- the S1.2 unit tests use the golden set as their fake "model" fixtures
  (``tests/unit/test_test_design_agent.py``);
- the S1.4 eval runner (``python -m qa_copilot_ai.eval`` or
  ``uv run python scripts/eval_run.py``) runs a live local LLM over the
  golden set and emits a JSON report scored against the §31.7 targets.

Prompts are pinned by ``name@version`` (§31.6), so a prompt change is
regression-tested against the golden set, never silent.
"""

from .cli import build_parser, main
from .golden import (
    GoldenFixture,
    GoldenSet,
    GoldenSetError,
    GoldenSource,
    GoldenTargets,
    default_golden_path,
    load_golden_set,
    step_coverage,
)
from .runner import EvalTotals, EvaluationReport, FixtureEvalResult, run_test_design_eval

__all__ = [
    "EvalTotals",
    "EvaluationReport",
    "FixtureEvalResult",
    "GoldenFixture",
    "GoldenSet",
    "GoldenSetError",
    "GoldenSource",
    "GoldenTargets",
    "build_parser",
    "default_golden_path",
    "load_golden_set",
    "main",
    "run_test_design_eval",
    "step_coverage",
]
