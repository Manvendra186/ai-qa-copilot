"""S2.3 live eval runner (build bible §19 S2.3 / §21).

Runs a real local LLM (through the gateway) over the S2.3 golden set and
scores every generated test with the full §21 gate:

- **schema** — the model output parses into a :class:`GeneratedTest`;
- **conventions** — per-fixture expectations (file path, framework, the
  repo's locator style, no raw-DOM escape hatches);
- **lint + type** — real ``tsc --strict`` + ESLint (``checker.py``), the
  same toolchain the web app lints with.

Exit gate: the lint+type pass fraction over the fixtures must be ≥ 95%
(``AutomationTargets.lint_type_pass_min``). The report is plain JSON —
persistable as an artifact (§10) once the API layer exists.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from qa_copilot_domain import RepositoryProfile, TestConventions

from ..agents.automation import (
    AutomationAgent,
    AutomationAgentResult,
    AutomationInput,
    GeneratedTest,
)
from ..gateway import LLMError
from .checker import CheckResult, Toolchain, check_generated_file, prepare_sandbox
from .golden import AutomationExpectations, AutomationFixture, AutomationGoldenSet


@dataclass(frozen=True, slots=True)
class RepoContext:
    """The shared S2.x contract for one target repository (S2.1 + S2.2)."""

    profile: RepositoryProfile
    conventions: TestConventions


class FixtureAutomationResult(BaseModel):
    """One fixture through the agent + the §21 gate (per-gate verdicts)."""

    model_config = ConfigDict(frozen=True)

    fixture_id: str
    title: str = ""
    file_path: str | None = None
    schema_valid: bool
    conventions_respected: bool
    lint_ok: bool
    type_ok: bool
    passed: bool
    error: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    lint_output: str | None = None
    type_output: str | None = None


class AutomationTotals(BaseModel):
    """Fractions over the fixtures — the §21 exit gate reads ``lint_type``."""

    model_config = ConfigDict(frozen=True)

    fixtures: int = Field(ge=1)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    schema_valid_fraction: float = Field(ge=0.0, le=1.0)
    conventions_respected_fraction: float = Field(ge=0.0, le=1.0)
    lint_pass_fraction: float = Field(ge=0.0, le=1.0)
    type_pass_fraction: float = Field(ge=0.0, le=1.0)
    lint_type_pass_fraction: float = Field(ge=0.0, le=1.0)


class AutomationReport(BaseModel):
    """The S2.3 eval result (persistable JSON, §10 artifact material)."""

    model_config = ConfigDict(frozen=True)

    schema_version: int = 1
    agent: str = "test-automator"
    model: str = Field(min_length=1)
    prompt_ref: str = Field(min_length=1)
    golden_name: str = Field(min_length=1)
    golden_version: str = Field(min_length=1)
    golden_fixtures: int = Field(ge=1)
    repos: list[str] = Field(min_length=1)
    targets: dict[str, float]
    totals: AutomationTotals
    passed: bool
    generated_at: str
    fixtures: list[FixtureAutomationResult]


def conventions_respected(
    generated: GeneratedTest, expectations: AutomationExpectations
) -> tuple[bool, list[str]]:
    """Check a generated test against the fixture's convention expectations.

    Returns ``(ok, problems)`` — problems double as the report's error text.
    """
    problems: list[str] = []
    if expectations.file_path is not None:
        if generated.file_path != expectations.file_path:
            problems.append(
                f"file_path {generated.file_path!r} != expected {expectations.file_path!r}"
            )
    elif expectations.file_path_pattern is not None:
        if not fnmatch.fnmatchcase(generated.file_path, expectations.file_path_pattern):
            problems.append(
                f"file_path {generated.file_path!r} does not match pattern "
                f"{expectations.file_path_pattern!r}"
            )
    if generated.language != expectations.language:
        problems.append(f"language {generated.language!r} != expected {expectations.language!r}")
    if generated.framework != expectations.framework:
        problems.append(f"framework {generated.framework!r} != expected {expectations.framework!r}")
    for token in expectations.must_use:
        if token not in generated.content:
            problems.append(f"content is missing {token!r}")
    for token in expectations.must_not_use:
        if token in generated.content:
            problems.append(f"content must not use {token!r}")
    return (not problems), problems


def _fraction(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _failed(fixture: AutomationFixture, error: str) -> FixtureAutomationResult:
    return FixtureAutomationResult(
        fixture_id=fixture.id,
        title=fixture.test_case.title,
        schema_valid=False,
        conventions_respected=False,
        lint_ok=False,
        type_ok=False,
        passed=False,
        error=error,
    )


def _score(
    fixture: AutomationFixture,
    generated: GeneratedTest,
    check: CheckResult,
    outcome: AutomationAgentResult,
) -> FixtureAutomationResult:
    """Fold schema + conventions + lint/type into one per-fixture verdict."""
    ok, problems = conventions_respected(generated, fixture.expectations)
    usage = getattr(outcome.call, "usage", None)
    detail = "; ".join(problems) if problems else ""
    if not check.ok:
        detail += f" | lint: {check.lint_output[:400]} | type: {check.type_output[:400]}"
    return FixtureAutomationResult(
        fixture_id=fixture.id,
        title=fixture.test_case.title,
        file_path=generated.file_path,
        schema_valid=True,
        conventions_respected=ok,
        lint_ok=check.lint_ok,
        type_ok=check.type_ok,
        passed=ok and check.ok,
        error=detail or None,
        tokens_in=getattr(usage, "tokens_in", None),
        tokens_out=getattr(usage, "tokens_out", None),
        latency_ms=getattr(outcome.call, "latency_ms", None),
        lint_output=check.lint_output or None,
        type_output=check.type_output or None,
    )


async def _eval_fixture(
    fixture: AutomationFixture,
    *,
    context: RepoContext,
    agent: AutomationAgent,
    toolchain: Toolchain,
    sandbox_root: Path,
) -> FixtureAutomationResult:
    try:
        outcome = await agent.run(
            AutomationInput(
                test_case=fixture.test_case,
                repository_profile=context.profile,
                conventions=context.conventions,
            )
        )
    except (ValueError, LLMError) as exc:
        return _failed(fixture, str(exc))
    sandbox = sandbox_root / fixture.id
    prepare_sandbox(sandbox, outcome.test, toolchain)
    check = check_generated_file(sandbox, outcome.test.file_path, toolchain)
    return _score(fixture, outcome.test, check, outcome)


async def run_automation_eval(
    golden: AutomationGoldenSet,
    *,
    agent: AutomationAgent,
    model: str,
    prompt_ref: str,
    contexts: dict[str, RepoContext],
    toolchain: Toolchain,
    sandbox_root: Path,
) -> AutomationReport:
    """Run the golden set through the agent + the §21 gate; return the report.

    ``contexts`` maps each fixture's ``repo`` name to its shared S2.x
    contract (profile + conventions) — built by the caller (CLI) with the
    S2.1 scanner + S2.2 extractor over the sample repositories.
    """
    sandbox_root.mkdir(parents=True, exist_ok=True)
    results: list[FixtureAutomationResult] = []
    for fixture in golden.fixtures:
        context = contexts.get(fixture.repo)
        if context is None:
            results.append(_failed(fixture, f"no repository context for {fixture.repo!r}"))
            continue
        results.append(
            await _eval_fixture(
                fixture,
                context=context,
                agent=agent,
                toolchain=toolchain,
                sandbox_root=sandbox_root,
            )
        )
    total = len(results)
    totals = AutomationTotals(
        fixtures=total,
        passed=sum(1 for result in results if result.passed),
        failed=sum(1 for result in results if not result.passed),
        schema_valid_fraction=_fraction(sum(1 for r in results if r.schema_valid), total),
        conventions_respected_fraction=_fraction(
            sum(1 for r in results if r.conventions_respected), total
        ),
        lint_pass_fraction=_fraction(sum(1 for r in results if r.lint_ok), total),
        type_pass_fraction=_fraction(sum(1 for r in results if r.type_ok), total),
        lint_type_pass_fraction=_fraction(
            sum(1 for r in results if r.lint_ok and r.type_ok), total
        ),
    )
    return AutomationReport(
        model=model,
        prompt_ref=prompt_ref,
        golden_name=golden.name,
        golden_version=golden.version,
        golden_fixtures=total,
        repos=sorted({fixture.repo for fixture in golden.fixtures}),
        targets={"lint_type_pass_min": golden.targets.lint_type_pass_min},
        totals=totals,
        passed=totals.lint_type_pass_fraction >= golden.targets.lint_type_pass_min,
        generated_at=datetime.now(UTC).isoformat(),
        fixtures=results,
    )


def report_to_json(report: AutomationReport) -> str:
    """The report as stable, human-readable JSON (for logs / artifacts)."""
    return json.dumps(report.model_dump(), indent=2, ensure_ascii=False)
