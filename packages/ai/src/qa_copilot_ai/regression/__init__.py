"""S6.3 deterministic regression recommender — golden set, eval runner, CLI.

The LLM-free core is :func:`qa_copilot_repository.recommend` (S6.1 impact joined
with the S6.2 risk ranking); this feature package provides its **golden set**
(:mod:`.golden`), the **eval runner** that scores the core against it
(:mod:`.runner`), and the ``regression eval`` **CLI** (:mod:`.cli`). The optional
LLM advisor lives in :mod:`qa_copilot_ai.agents.regression_advisor` and only
summarizes the deterministic set — it never re-orders it.
"""

from .cli import build_parser, main
from .golden import (
    RegressionExpect,
    RegressionFixture,
    RegressionGoldenSet,
    RegressionGoldenSetError,
    RegressionGoldenSource,
    RegressionTargets,
    default_golden_path,
    load_regression_golden_set,
)
from .runner import (
    RegressionCaseResult,
    RegressionReport,
    RegressionTotals,
    run_regression_eval,
)

__all__ = [
    "RegressionCaseResult",
    "RegressionExpect",
    "RegressionFixture",
    "RegressionGoldenSet",
    "RegressionGoldenSetError",
    "RegressionGoldenSource",
    "RegressionReport",
    "RegressionTargets",
    "RegressionTotals",
    "build_parser",
    "default_golden_path",
    "load_regression_golden_set",
    "main",
    "run_regression_eval",
]
