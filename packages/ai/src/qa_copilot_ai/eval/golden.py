"""Golden set v1 — the build-bible §22 evaluation dataset.

One dataset, two consumers (§22 "build early", §31.6 "golden evals pin
``name@version``"):

- the S1.2 unit tests use the fixtures/oracles/golden suites as their fake
  "model" outputs (``tests/unit/test_test_design_agent.py``);
- the S1.4 eval runner (``python -m qa_copilot_ai.eval``) runs a live local
  LLM over the same fixtures and scores the result against the §31.7 targets.

Per fixture: the requirement, a hand-authored **QA oracle** (the independent
expected behavior — §19 S1.2) and a **golden suite**: a competent model's
reference output, validated by the same §12 ``TestSuite`` schema the agents
are held to.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agents import TestSuite

__all__ = [
    "GoldenFixture",
    "GoldenSet",
    "GoldenSetError",
    "GoldenSource",
    "GoldenTargets",
    "default_golden_path",
    "load_golden_set",
    "step_coverage",
]


class GoldenSetError(ValueError):
    """The golden set file is missing, unreadable, or fails its own schema."""


class GoldenSource(BaseModel):
    """Provenance: which build-bible sections this dataset implements."""

    model_config = ConfigDict(frozen=True)

    build_bible: str = Field(min_length=1)
    dataset_section: str = "22"
    targets_section: str = "31.7"
    prompt: str = Field(default="test-designer@1", min_length=1)


class GoldenTargets(BaseModel):
    """§31.7 numeric evaluation targets (v1.1)."""

    model_config = ConfigDict(frozen=True)

    schema_valid_min: float = Field(default=0.99, ge=0.0, le=1.0)
    oracle_step_coverage_min: float = Field(default=0.85, ge=0.0, le=1.0)


class GoldenFixture(BaseModel):
    """One golden requirement: input + QA oracle + golden (reference) suite."""

    id: str = Field(pattern=r"^REQ-\d{3}$")
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    category: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(default_factory=list)
    oracle_steps: list[str] = Field(min_length=1)
    suite: TestSuite


class GoldenSet(BaseModel):
    """The versioned golden set (``golden/golden_v1.json``)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    name: str = Field(min_length=1)
    version: str = "v1"
    description: str = ""
    source: GoldenSource
    targets: GoldenTargets = Field(default_factory=GoldenTargets)
    categories: list[str] = Field(default_factory=list)
    fixtures: list[GoldenFixture] = Field(min_length=1)


# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/eval/``.
_PKG_ROOT: Final[Path] = Path(__file__).resolve().parents[3]


def default_golden_path() -> Path:
    """The checked-in golden set: ``packages/ai/golden/golden_v1.json``."""
    return _PKG_ROOT / "golden" / "golden_v1.json"


def load_golden_set(path: str | Path) -> GoldenSet:
    """Load and validate a golden set file.

    Raises:
        GoldenSetError: the file is missing/unreadable or fails validation —
            a broken dataset must fail loud, never silently shrink the eval.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GoldenSetError(f"cannot read golden set {file_path}: {exc}") from exc
    try:
        return GoldenSet.model_validate_json(text)
    except ValidationError as exc:
        raise GoldenSetError(f"golden set {file_path} failed validation: {exc}") from exc


# --- Step coverage (§31.7: "test-design step coverage vs oracle") ------------
#
# Shared by the S1.2 unit tests and the S1.4 eval runner — one metric, one
# implementation (moved out of tests/unit/test_test_design_agent.py in S1.4).

_STOPWORDS: Final[frozenset[str]] = frozenset(
    """
    a an and any are as at be been but by can could do does did every for from
    had has have he her him his how i if in into is it its just me more most my
    no nor not of off on or other our ours out over she so some such than that
    the their theirs them then there these they this those through to too under
    until up us very was we were what when where which while who whom why will
    with within without you your yours
    """.split()
)


def _tokens(text: str) -> set[str]:
    """Meaningful tokens: lowercase alphanumerics, ≥ 3 chars, stopwords gone."""
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in _STOPWORDS
    }


def _token_in_pool(token: str, pool: set[str]) -> bool:
    """Exact or prefix match (tolerates inflection: returns/returned, ...)."""
    if token in pool:
        return True
    return any(
        len(token) >= 4
        and len(candidate) >= 4
        and (token.startswith(candidate) or candidate.startswith(token))
        for candidate in pool
    )


def step_coverage(
    generated_steps: Sequence[str],
    oracle_steps: Sequence[str],
    per_step: float = 0.6,
) -> float:
    """Share of oracle steps covered by the generated step pool (0.0-1.0).

    An oracle step is *covered* when at least *per_step* of its meaningful
    tokens appear (exact or prefix) in the union of the generated steps'
    tokens. Only steps are pooled — titles, preconditions and expected
    results are not what the suite *does*. The metric is deliberately
    lexical: it scores behavior coverage, not prose similarity.
    """
    if not oracle_steps:
        return 1.0
    pool: set[str] = set()
    for step in generated_steps:
        pool |= _tokens(step)
    covered = 0
    for oracle_step in oracle_steps:
        tokens = _tokens(oracle_step)
        if not tokens:
            continue
        hits = sum(1 for token in tokens if _token_in_pool(token, pool))
        if hits / len(tokens) >= per_step:
            covered += 1
    return covered / len(oracle_steps)
