"""``eval run`` CLI (S1.4) — golden set vs live local LLM → JSON report.

The JSON report (the S1.4 artifact, §22/§31.7) goes to **stdout** and
optionally to ``--report PATH``; the human summary goes to **stderr**, so
``eval ... > report.json`` stays clean.

Exit codes: ``0`` all §31.7 targets met · ``1`` run completed, targets
missed · ``2`` configuration/usage error (no LLM call attempted).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from ..agents import TEST_DESIGNER_NAME, TestDesignAgent
from ..gateway import DEFAULT_TIMEOUT_S, LLMGateway
from ..prompts import FilePromptStore, PromptNotFound
from .golden import GoldenSetError, default_golden_path, load_golden_set
from .runner import EvaluationReport, run_test_design_eval

# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/eval/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PKG_ROOT / "prompts"

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-eval",
        description=(
            "S1.4 eval runner: runs the Test Design Agent over the golden set "
            "(§22) and emits a JSON report scored against the build-bible "
            "§31.7 targets."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="golden set JSON (default: packages/ai/golden/golden_v1.json)",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible base URL (default: $LLM_BASE_URL)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model id (default: $LLM_MODEL)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"per-call timeout in seconds (default {DEFAULT_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="also write the JSON report to this path",
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
    base = (args.base_url or base_url or os.environ.get("LLM_BASE_URL") or "").strip()
    model_id = (args.model or model or os.environ.get("LLM_MODEL") or "").strip()
    if not base or not model_id:
        print(
            "error: LLM endpoint not configured — set LLM_BASE_URL and LLM_MODEL "
            "(or pass --base-url/--model; scripts/eval_run.py reads them from .env)",
            file=sys.stderr,
        )
        return 2
    if args.timeout <= 0:
        print("error: --timeout must be > 0 seconds", file=sys.stderr)
        return 2
    return asyncio.run(_execute(args, base, model_id))


async def _execute(args: argparse.Namespace, base_url: str, model: str) -> int:
    golden_path = args.golden or default_golden_path()
    try:
        golden = load_golden_set(golden_path)
    except GoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        spec = FilePromptStore(_PROMPTS_DIR).get(TEST_DESIGNER_NAME)
    except PromptNotFound as exc:
        print(
            f"error: prompt {TEST_DESIGNER_NAME} is not registered in {_PROMPTS_DIR}: {exc}",
            file=sys.stderr,
        )
        return 2

    gateway = LLMGateway(base_url=base_url, model=model, timeout=args.timeout)
    agent = TestDesignAgent(FilePromptStore(_PROMPTS_DIR), gateway)
    try:
        report = await run_test_design_eval(golden, agent=agent, model=model, prompt_ref=spec.ref)
    finally:
        await gateway.aclose()
    return _emit(report, report_path=args.report)


def _emit(
    report: EvaluationReport,
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


def _print_summary(report: EvaluationReport, *, file: TextIO) -> None:
    totals = report.totals
    schema_min = report.targets["schema_valid_min"] * 100
    coverage_min = report.targets["oracle_step_coverage_min"] * 100
    schema_valid = sum(1 for fixture in report.fixtures if fixture.schema_valid)
    scored = [fixture.coverage for fixture in report.fixtures if fixture.coverage is not None]

    print(
        f"eval {report.agent} · model {report.model} · "
        f"golden {report.golden_name} {report.golden_version} "
        f"({totals.fixtures} fixtures)",
        file=file,
    )
    print(
        f"  schema-valid   {schema_valid}/{totals.fixtures} "
        f"({schema_valid / totals.fixtures * 100:.1f}%)  target ≥ {schema_min:.1f}%",
        file=file,
    )
    if scored:
        coverage_avg = totals.coverage_avg if totals.coverage_avg is not None else 0.0
        print(
            f"  step coverage  avg {coverage_avg * 100:.1f}% · "
            f"min {min(scored) * 100:.1f}%  target ≥ {coverage_min:.1f}%",
            file=file,
        )
    if report.passed:
        print("  result         PASSED (exit 0)", file=file)
        return
    print("  result         FAILED (exit 1)", file=file)
    for fixture in report.fixtures:
        if fixture.passed:
            continue
        if not fixture.schema_valid:
            reason = f"error: {(fixture.error or 'schema-invalid output')[:160]}"
        elif fixture.coverage is not None:
            reason = f"coverage {fixture.coverage * 100:.1f}% < {coverage_min:.1f}%"
        else:
            reason = "no coverage score"
        print(f"    {fixture.fixture_id} {fixture.title} — {reason}", file=file)
