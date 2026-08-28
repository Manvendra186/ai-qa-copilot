"""S2.3 golden set — automation generation over the sample repositories.

Same pattern as the S1.4 golden set (build bible §22): one dataset, two
consumers:

- the S2.3 unit tests use the fixtures' ``model_output`` as the fake
  "model" answer (``tests/unit/test_automation_agent.py``) — the same
  S1.4/S1.2 trick;
- the S2.3 live eval (``python -m qa_copilot_ai.automation.cli``) runs a real
  local LLM over the same fixtures and scores every output: schema
  (``GeneratedTest``) + conventions (per-fixture expectations) + **real**
  ``tsc --strict`` / ESLint (``checker.py``) — the §21 exit gate:
  lint + type pass ≥ 95%.

Each fixture carries the S1.2 :class:`TestCase` the agent must automate, the
target sample repo (``repo``), the fake model's full answer (``model_output``
— JSON metadata line + fenced code, the ``test-automator@1`` contract), and
``expectations`` (where the file goes, what it must/must not use).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..agents.test_design import TestCase

_PKG_ROOT = Path(__file__).resolve().parents[3]  # packages/ai


class AutomationGoldenSource(BaseModel):
    """Provenance — what this golden set is ground truth for (§22)."""

    model_config = ConfigDict(frozen=True)

    build_bible: str = Field(min_length=1, description="Build bible revision")
    step: str = Field(default="S2.3", min_length=1, description="Build-bible step")
    prompt: str = Field(default="test-automator@1", min_length=1)


class AutomationTargets(BaseModel):
    """Exit-gate thresholds (build bible §19 S2.3 / §21).

    ``lint_type_pass_min`` is THE S2.3 exit: the fraction of fixtures whose
    generated file passes both ESLint and strict tsc must be ≥ 95%.
    """

    model_config = ConfigDict(frozen=True)

    lint_type_pass_min: float = Field(
        default=0.95, ge=0.0, le=1.0, description="Lint + type pass fraction (§21 exit gate)"
    )


class AutomationExpectations(BaseModel):
    """Convention conformance a generated test must show (S2.3 gate)."""

    model_config = ConfigDict(frozen=True)

    file_path: str | None = Field(
        default=None,
        min_length=1,
        description="Exact repo-relative path the test file must land at",
    )
    file_path_pattern: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "fnmatch glob the repo-relative path must match — use when the prompt "
            "leaves the file name to the model (test-automator@1 rule 1: test dir + "
            "pattern, <name> free)"
        ),
    )
    language: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    must_use: list[str] = Field(
        min_length=1,
        description="Tokens the content must contain (e.g. 'getByRole', '@playwright/test')",
    )
    must_not_use: list[str] = Field(
        default_factory=list,
        description="Tokens the content must NOT contain (e.g. 'document.', 'querySelector')",
    )

    @model_validator(mode="after")
    def _require_file_placement(self) -> AutomationExpectations:
        if self.file_path is None and self.file_path_pattern is None:
            raise ValueError("expectations need file_path or file_path_pattern")
        return self


class AutomationFixture(BaseModel):
    """One S2.3 case: a test case to automate + the fake model's answer."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^AUTO-\d{3}$")
    repo: str = Field(min_length=1, description="Sample repo name (packages/repository/samples)")
    category: str = Field(min_length=1, description="e2e-functional / e2e-boundary / …")
    test_case: TestCase
    model_output: str = Field(
        min_length=1, description="Fake model answer: JSON metadata line + fenced code"
    )
    expectations: AutomationExpectations


class AutomationGoldenSet(BaseModel):
    """The S2.3 golden dataset (build bible §22)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    name: str = Field(min_length=1)
    version: str = "v1"
    description: str = ""
    source: AutomationGoldenSource
    targets: AutomationTargets = Field(default_factory=AutomationTargets)
    fixtures: list[AutomationFixture] = Field(min_length=1)

    @field_validator("fixtures")
    @classmethod
    def _check_ids_unique(cls, fixtures: list[AutomationFixture]) -> list[AutomationFixture]:
        ids = [fixture.id for fixture in fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixture ids must be unique")
        return fixtures


class AutomationGoldenSetError(ValueError):
    """The golden set file is missing or invalid (fail loud)."""


def default_golden_path() -> Path:
    """Default golden set location: ``packages/ai/golden/automation_v1.json``."""
    return _PKG_ROOT / "golden" / "automation_v1.json"


def load_automation_golden_set(path: Path) -> AutomationGoldenSet:
    """Load + validate the S2.3 golden set from ``path``.

    Raises:
        AutomationGoldenSetError: missing file or schema violation.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise AutomationGoldenSetError(f"automation golden set not found: {path}") from exc
    try:
        return AutomationGoldenSet.model_validate(json.loads(raw))
    except Exception as exc:  # noqa: BLE001 — one error type for all bad sets
        raise AutomationGoldenSetError(f"invalid automation golden set {path}: {exc}") from exc
