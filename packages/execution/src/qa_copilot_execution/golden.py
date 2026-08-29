"""S3.3 golden set — 30 broken-test failure texts with known root causes.

Build bible §22 ("At least 30 intentionally broken Playwright tests with
known root causes" + "Golden outputs for ... failure classification") and
§19 S3.3 (exit: 30 broken tests normalize 100%). Same one-dataset pattern as
the S1.4/S2.3 golden sets: the S3.3 unit tests score the deterministic
normalizer against it; S4.1 (Failure Investigator, AI) will score its top-1
classification on the same texts (≥ 80%, §31.7).

Each fixture is one raw failure text (the shape of
``TestResultReport.error``: message + snippet) with the expected
classification — the §16 taxonomy category plus the structured fields the
normalizer must extract (``http_status`` / ``selector`` / ``endpoint``) and
the rule names that must be among the matched signals (subset check).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from qa_copilot_domain.enums import FailureCategory

__all__ = [
    "FailureExpectations",
    "FailureFixture",
    "FailureGoldenSet",
    "FailureGoldenSetError",
    "FailureGoldenSource",
    "FailureTargets",
    "GoldenMismatch",
    "GoldenReport",
    "default_golden_path",
    "load_failure_golden_set",
]

_PKG_ROOT = Path(__file__).resolve().parents[2]  # packages/execution


class FailureGoldenSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    build_bible: str = Field(min_length=1)
    step: str = Field(default="S3.3", min_length=1)


class FailureTargets(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: THE S3.3 exit gate (§19): fraction of fixtures that must normalize correctly.
    normalize_pass_min: float = Field(default=1.0, ge=0.0, le=1.0)


class FailureExpectations(BaseModel):
    """What the normalizer must produce for one fixture.

    ``category`` / ``http_status`` / ``selector`` / ``endpoint`` are exact;
    ``signals`` is a subset check (every listed rule name must be present in
    the actual ``category_signals``). Omitted fields mean ``None``.
    """

    model_config = ConfigDict(frozen=True)

    category: FailureCategory
    http_status: int | None = Field(default=None, ge=100, le=599)
    selector: str | None = Field(default=None, min_length=1)
    endpoint: str | None = Field(default=None, min_length=1)
    signals: list[str] = Field(default_factory=list)


class FailureFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^FAIL-\d{3}$")
    title: str = Field(min_length=1)
    #: Raw failure text exactly as Playwright reported it (message + snippet).
    raw: str = Field(min_length=1)
    expect: FailureExpectations


class FailureGoldenSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    name: str = Field(min_length=1)
    version: str = "v1"
    description: str = ""
    source: FailureGoldenSource
    targets: FailureTargets = Field(default_factory=FailureTargets)
    fixtures: list[FailureFixture] = Field(min_length=1)

    @field_validator("fixtures")
    @classmethod
    def _check_ids_unique(cls, fixtures: list[FailureFixture]) -> list[FailureFixture]:
        ids = [fixture.id for fixture in fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixture ids must be unique")
        return fixtures


class GoldenMismatch(BaseModel):
    """One fixture that did not normalize as expected (id + field diffs)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    mismatches: list[str] = Field(default_factory=list)


class GoldenReport(BaseModel):
    """Golden-set run result; ``gate_met`` is the S3.3 exit criterion (§19)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: list[GoldenMismatch] = Field(default_factory=list)
    gate: float = Field(ge=0.0, le=1.0)
    gate_met: bool = False


class FailureGoldenSetError(ValueError):
    """The golden set file is missing or invalid (fail loud, never guess)."""


def default_golden_path() -> Path:
    """Canonical dataset location: ``packages/execution/golden/failure_v1.json``."""
    return _PKG_ROOT / "golden" / "failure_v1.json"


def load_failure_golden_set(path: Path) -> FailureGoldenSet:
    """Load + validate the golden set; raise :class:`FailureGoldenSetError` on problems."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FailureGoldenSetError(f"failure golden set not found: {path}") from exc
    except OSError as exc:
        raise FailureGoldenSetError(f"cannot read failure golden set {path}: {exc}") from exc
    try:
        return FailureGoldenSet.model_validate(json.loads(raw))
    except Exception as exc:  # pydantic.ValidationError et al. → one loud error
        raise FailureGoldenSetError(f"invalid failure golden set {path}: {exc}") from exc
