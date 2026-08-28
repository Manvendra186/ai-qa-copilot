"""S2.3 automation eval — golden set, §21 lint/type gate, runner, CLI.

Build bible §19 Phase 2, step S2.3: "Test automation: generate tests using
extracted conventions." Exit: generated code passes lint + type at ≥ 95%.

- :mod:`~qa_copilot_ai.automation.golden` — the S2.3 golden set (§22),
- :mod:`~qa_copilot_ai.automation.checker` — real tsc/ESLint gate over the
  ``apps/web`` toolchain (no sample-repo install — stub ``@playwright/test``
  types under ``tests/unit/support``),
- :mod:`~qa_copilot_ai.automation.runner` — live-LLM eval + JSON report,
- :mod:`~qa_copilot_ai.automation.cli` — ``python -m qa_copilot_ai.automation``.
"""

from .checker import (
    CheckResult,
    Toolchain,
    check_generated_file,
    find_toolchain,
    prepare_sandbox,
)
from .cli import ConfigError, build_parser, main
from .golden import (
    AutomationExpectations,
    AutomationFixture,
    AutomationGoldenSet,
    AutomationGoldenSetError,
    AutomationGoldenSource,
    AutomationTargets,
    default_golden_path,
    load_automation_golden_set,
)
from .runner import (
    AutomationReport,
    AutomationTotals,
    FixtureAutomationResult,
    RepoContext,
    conventions_respected,
    report_to_json,
    run_automation_eval,
)

__all__ = [
    "AutomationExpectations",
    "AutomationFixture",
    "AutomationGoldenSet",
    "AutomationGoldenSetError",
    "AutomationGoldenSource",
    "AutomationReport",
    "AutomationTargets",
    "AutomationTotals",
    "CheckResult",
    "ConfigError",
    "FixtureAutomationResult",
    "RepoContext",
    "Toolchain",
    "build_parser",
    "check_generated_file",
    "conventions_respected",
    "default_golden_path",
    "find_toolchain",
    "load_automation_golden_set",
    "main",
    "prepare_sandbox",
    "report_to_json",
    "run_automation_eval",
]
