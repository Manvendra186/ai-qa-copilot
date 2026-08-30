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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from qa_copilot_domain.enums import FailureCategory

__all__ = [
    "FailureExpectations",
    "FailureFixture",
    "FailureGoldenSet",
    "FailureGoldenSetError",
    "FailureGoldenSource",
    "FailureTargets",
    "FixFixture",
    "FixGoldenSet",
    "FixGoldenSetError",
    "FixTargets",
    "GoldenMismatch",
    "GoldenReport",
    "default_fix_golden_path",
    "default_golden_path",
    "load_failure_golden_set",
    "load_fix_golden_set",
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
    #: THE S4.1 exit gate (§19): Failure Investigator top-1 category accuracy
    #: on this set (build bible §31.7 default: 0.8).
    top1_min: float = Field(default=0.8, ge=0.0, le=1.0)


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


# ---------------------------------------------------------------------------
# S4.2 fix golden set — broken test files with known, reviewable fixes
# ---------------------------------------------------------------------------


class FixTargets(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: THE S4.2 exit gate (§19 S4.2, §31.7): fraction of fixtures whose
    #: proposed fix is **applicable** (the patch applies to the broken file)
    #: and **passing** (the patched test passes) — default 0.5 (≥ 5/10).
    passing_min: float = Field(default=0.5, ge=0.0, le=1.0)


class FixFixture(BaseModel):
    """One broken test with a known root cause and a known-good fix.

    ``failure`` is the raw failure text the runner reported (the same shape
    as :class:`FailureFixture.raw`); ``test_code`` is the broken test file
    (the fixer's input); ``fixed_code`` is the known-good fixed file — the
    oracle reference the offline gate compares against. ``has_fix`` is the
    ground truth for the category guard: ``False`` means the failure is a
    product/environment defect and the correct action is to **decline**
    (no safe test-side patch, build bible §26). ``app_env`` declares the
    app-under-test environment the live verifier must run against (e.g. the
    demo app's defect-injection flags — the app is *deliberately* wrong
    for that fixture).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(pattern=r"^FIX-\d{3}$")
    title: str = Field(min_length=1)
    #: §16 taxonomy category of the underlying failure (known root cause).
    category: FailureCategory
    #: Raw failure text exactly as the runner reported it (message + snippet).
    failure: str = Field(min_length=1)
    #: Repo-relative test file the fix targets (e.g. ``e2e/checkout.spec.js``).
    file_path: str = Field(min_length=1)
    #: The broken test file (pre-fix) — the Fix Agent's input.
    test_code: str = Field(min_length=1)
    #: The known-good fixed test file — the oracle reference (offline gate).
    #: Required when ``has_fix`` is true; must be ``None`` when the correct
    #: action is to decline (there is no test-side fix to reference).
    fixed_code: str | None = None
    #: True when a correct test-side fix exists; False → the correct action
    #: is to decline (product/environment defect — no safe test-side patch).
    has_fix: bool = True
    #: App-under-test env for the live verifier (e.g. ``{"DEFECT_API_500": "1"}``).
    app_env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_fix_consistency(self) -> FixFixture:
        if self.has_fix:
            if not (self.fixed_code or "").strip():
                raise ValueError(
                    "has_fix=true requires the known-good fixed_code (oracle reference)"
                )
            if self.fixed_code == self.test_code:
                raise ValueError(
                    "fixed_code must differ from test_code — the oracle fix must be a real change"
                )
        elif self.fixed_code is not None:
            raise ValueError(
                "has_fix=false must not carry a fixed_code — the correct action is to decline"
            )
        return self


class FixGoldenSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    name: str = Field(min_length=1)
    version: str = "v1"
    description: str = ""
    source: FailureGoldenSource
    targets: FixTargets = Field(default_factory=FixTargets)
    fixtures: list[FixFixture] = Field(min_length=1)

    @field_validator("fixtures")
    @classmethod
    def _check_ids_unique(cls, fixtures: list[FixFixture]) -> list[FixFixture]:
        ids = [fixture.id for fixture in fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixture ids must be unique")
        return fixtures


class FixGoldenSetError(ValueError):
    """The fix golden set file is missing or invalid (fail loud, never guess)."""


def default_fix_golden_path() -> Path:
    """Canonical dataset location: ``packages/execution/golden/fix_v1.json``."""
    return _PKG_ROOT / "golden" / "fix_v1.json"


def load_fix_golden_set(path: Path) -> FixGoldenSet:
    """Load + validate the fix golden set; raise :class:`FixGoldenSetError`."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FixGoldenSetError(f"fix golden set not found: {path}") from exc
    except OSError as exc:
        raise FixGoldenSetError(f"cannot read fix golden set {path}: {exc}") from exc
    try:
        return FixGoldenSet.model_validate(json.loads(raw))
    except Exception as exc:  # pydantic.ValidationError et al. → one loud error
        raise FixGoldenSetError(f"invalid fix golden set {path}: {exc}") from exc
