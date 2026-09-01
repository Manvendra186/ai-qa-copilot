"""``regression eval`` CLI (S6.3) — the deterministic recommender gate.

The S6.3 exit criterion is the **deterministic core**
(:func:`qa_copilot_repository.recommend`) matching the golden set 100%
(§19 S6.3, §22, §31.7) — a fully offline pass (no LLM, no network, no DB).
The JSON report goes to **stdout** and optionally to ``--report PATH``; the
human summary goes to **stderr**, so ``regression ... > report.json`` stays
clean.

The optional LLM advisor (``regression-advisor@1``) is off by default;
``--advise`` attaches a one-line brief over one case's recommendation set —
the live model when ``LLM_BASE_URL``/``LLM_MODEL`` are set, else the
deterministic stub. The advisor never touches the ranking or the gate.

Exit codes: ``0`` §31.7 gate met · ``1`` run completed, gate missed ·
``2`` configuration/usage error (no eval attempted).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from qa_copilot_domain import RecommendationSet
from qa_copilot_repository import recommend

from ..agents import AdvisorInput, RegressionAdvisorAgent, stub_summary
from ..config import load_model_settings
from ..gateway import LLMGateway
from ..prompts import FilePromptStore
from .golden import (
    RegressionFixture,
    RegressionGoldenSet,
    RegressionGoldenSetError,
    default_golden_path,
    load_regression_golden_set,
)
from .runner import RegressionReport, run_regression_eval

# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/regression/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PKG_ROOT / "prompts"

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-regression",
        description=(
            "S6.3 eval runner: replays the golden set through the deterministic "
            "regression recommender (§19 S6.3, §22, §31.7: 100% order match) and "
            "emits a JSON report. Fully offline — no LLM required."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="regression golden set JSON (default: packages/ai/golden/regression_v1.json)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="also write the JSON report to this path",
    )
    parser.add_argument(
        "--advise",
        action="store_true",
        default=False,
        help=(
            "attach an optional advisor brief over one case (LLM if configured, "
            "else the deterministic stub) — does not affect the deterministic gate"
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL for --advise (default: $LLM_BASE_URL)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id for --advise (default: $LLM_MODEL)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> int:
    """CLI entry point (module docstring: exit codes, stdout/stderr contract)."""
    args = build_parser().parse_args(argv)
    golden_path = args.golden or default_golden_path()
    try:
        golden = load_regression_golden_set(golden_path)
    except RegressionGoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = run_regression_eval(golden)

    if args.advise:
        base = (args.base_url or base_url or os.environ.get("LLM_BASE_URL") or "").strip()
        model_id = (args.model or model or os.environ.get("LLM_MODEL") or "").strip()
        if base and model_id:
            asyncio.run(_advise_with_llm(golden, report, base, model_id))
        else:
            _advise_with_stub(golden, report)

    return _emit(report, report_path=args.report)


def _first_recommended(
    golden: RegressionGoldenSet,
) -> tuple[RegressionFixture | None, RecommendationSet | None]:
    """The first fixture with a non-empty recommendation set (to summarize)."""
    for fixture in golden.fixtures:
        result = recommend(fixture.impact, fixture.ranking, top_n=fixture.top_n)
        if result.recommendations:
            return fixture, result
    return None, None


def _advise_with_stub(golden: RegressionGoldenSet, report: RegressionReport) -> None:
    """Attach the deterministic stub brief (no LLM configured)."""
    fixture, result = _first_recommended(golden)
    if fixture is None or result is None:
        return
    report.summary = stub_summary(result)
    report.summary_source = "stub"
    report.summary_fixture = fixture.id


async def _advise_with_llm(
    golden: RegressionGoldenSet,
    report: RegressionReport,
    base: str,
    model: str,
) -> None:
    """Attach the LLM advisor brief (falls back to the stub on any error)."""
    fixture, result = _first_recommended(golden)
    if fixture is None or result is None:
        return
    store = FilePromptStore(_PROMPTS_DIR)
    gateway = LLMGateway(base_url=base, model=model, timeout=load_model_settings().timeout_s)
    agent = RegressionAdvisorAgent(store, gateway)
    try:
        advisor_result = await agent.run(AdvisorInput(set=result))
    finally:
        await gateway.aclose()
    report.summary = advisor_result.summary
    report.summary_source = advisor_result.source
    report.summary_fixture = fixture.id


def _emit(
    report: RegressionReport,
    *,
    report_path: Path | None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Write the JSON report (stdout + optional file) and the summary (stderr)."""
    # Resolve the streams at call time — not import time — so stdout/stderr
    # redirection (and tests capturing them) work as expected.
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    payload = report.model_dump_json(indent=2)
    print(payload, file=out)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload + "\n", encoding="utf-8")
    _print_summary(report, file=err)
    return 0 if report.passed else 1


def _print_summary(report: RegressionReport, *, file: TextIO) -> None:
    totals = report.totals
    pass_min = report.targets["pass_min"] * 100
    print(
        f"regression S6.3 · golden {report.golden_name} {report.golden_version} "
        f"({totals.fixtures} fixtures) · deterministic core",
        file=file,
    )
    print(
        f"  order match  {totals.passed}/{totals.fixtures} "
        f"({totals.pass_fraction * 100:.1f}%)  target ≥ {pass_min:.1f}%",
        file=file,
    )
    if report.summary is not None:
        print(
            f"  advisor      {report.summary_source} ({report.summary_fixture}) — {report.summary}",
            file=file,
        )
    if report.passed:
        print("  result         PASSED (exit 0)", file=file)
        return
    print("  result         FAILED (exit 1)", file=file)
    for case in report.cases:
        if case.passed:
            continue
        print(
            f"    {case.fixture_id} {case.title} — expected {case.expected_keys} "
            f"got {case.actual_keys}",
            file=file,
        )
