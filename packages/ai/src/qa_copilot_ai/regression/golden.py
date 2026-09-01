"""S6.3 golden set — the deterministic regression recommender core.

The S6.3 ranking is **deterministic and LLM-free**
(:func:`qa_copilot_repository.recommend`), so its golden set is a pure
input/expected-output dataset — the S2.3/S4.1 "one dataset" pattern, but with
no ``model_output`` (there is no model in the deterministic path): each fixture
is an :class:`qa_copilot_domain.ImpactSet` + :class:`qa_copilot_domain.RiskRanking`
+ ``top_n`` with an :class:`RegressionExpect` block naming the expected ranked
order (and, where it documents the join, the impact kind per item).

Two consumers (the S2.3 "one dataset, two consumers" pattern):

- the S6.3 unit tests (``tests/unit/test_regression.py``) build the same domain
  objects and assert the core's join/order/tie-break/truncation;
- the S6.3 eval runner (:mod:`qa_copilot_ai.regression.runner`) replays the
  fixtures through :func:`qa_copilot_repository.recommend` and scores them —
  fully offline, no LLM, no network, no database.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from qa_copilot_domain import ImpactSet, RiskRanking

_PKG_ROOT = Path(__file__).resolve().parents[3]  # packages/ai


class RegressionGoldenSource(BaseModel):
    """Provenance — what this golden set is ground truth for (§22)."""

    model_config = ConfigDict(frozen=True)

    build_bible: str = Field(min_length=1, description="Build bible revision")
    step: str = Field(default="S6.3", min_length=1, description="Build-bible step")
    prompt: str = Field(default="regression-advisor@1", min_length=1)


class RegressionTargets(BaseModel):
    """Exit-gate thresholds (build bible §19 S6.3).

    The deterministic core is fully reproducible, so the S6.3 gate is a 100%
    pass: every fixture's expected order must match exactly (no LLM sampling).
    """

    model_config = ConfigDict(frozen=True)

    pass_min: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Fixture pass fraction (S6.3 gate)"
    )


class RegressionExpect(BaseModel):
    """The deterministic expected output for one fixture.

    ``ordered_keys`` is the expected ``test_key`` order (after top-N
    truncation); ``impact_kinds`` (optional, aligned to ``ordered_keys``)
    documents the S6.1→S6.2 join's strongest impact kind per item.
    """

    model_config = ConfigDict(frozen=True)

    ordered_keys: list[str] = Field(default_factory=list)
    impact_kinds: list[str] | None = Field(default=None)


class RegressionFixture(BaseModel):
    """One S6.3 case: an impact set + a risk ranking + the expected order."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^REG-\d{3}$")
    title: str = Field(min_length=1)
    impact: ImpactSet
    ranking: RiskRanking
    top_n: int = Field(default=10, ge=1)
    expect: RegressionExpect


class RegressionGoldenSet(BaseModel):
    """The S6.3 golden dataset (build bible §22)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    name: str = Field(min_length=1)
    version: str = "v1"
    description: str = ""
    source: RegressionGoldenSource
    targets: RegressionTargets = Field(default_factory=RegressionTargets)
    fixtures: list[RegressionFixture] = Field(min_length=1)

    @field_validator("fixtures")
    @classmethod
    def _check_ids_unique(cls, fixtures: list[RegressionFixture]) -> list[RegressionFixture]:
        ids = [fixture.id for fixture in fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixture ids must be unique")
        return fixtures


class RegressionGoldenSetError(ValueError):
    """The golden set file is missing or invalid (fail loud)."""


def default_golden_path() -> Path:
    """Default golden set location: ``packages/ai/golden/regression_v1.json``."""
    return _PKG_ROOT / "golden" / "regression_v1.json"


def load_regression_golden_set(path: Path) -> RegressionGoldenSet:
    """Load + validate the S6.3 golden set from ``path``.

    Raises:
        RegressionGoldenSetError: missing file or schema violation.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RegressionGoldenSetError(f"regression golden set not found: {path}") from exc
    try:
        return RegressionGoldenSet.model_validate(json.loads(raw))
    except Exception as exc:  # noqa: BLE001 — one error type for all bad sets
        raise RegressionGoldenSetError(f"invalid regression golden set {path}: {exc}") from exc


__all__ = [
    "RegressionExpect",
    "RegressionFixture",
    "RegressionGoldenSet",
    "RegressionGoldenSetError",
    "RegressionGoldenSource",
    "RegressionTargets",
    "default_golden_path",
    "load_regression_golden_set",
]
