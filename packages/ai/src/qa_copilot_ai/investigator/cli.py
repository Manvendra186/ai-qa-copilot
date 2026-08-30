"""``investigator run`` CLI (S4.1) — 30-broken-test set vs live local LLM.

The JSON report (the S4.1 artifact, §19/§22/§31.7) goes to **stdout** and
optionally to ``--report PATH``; the human summary goes to **stderr**, so
``investigator ... > report.json`` stays clean.

Exit codes: ``0`` §31.7 targets met · ``1`` run completed, targets
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

from qa_copilot_execution.golden import (
    FailureGoldenSetError,
    default_golden_path,
    load_failure_golden_set,
)

from ..agents import INVESTIGATOR_NAME, FailureInvestigatorAgent
from ..config import load_model_settings
from ..gateway import DEFAULT_TIMEOUT_S, LLMGateway
from ..prompts import FilePromptStore, PromptNotFound
from .runner import InvestigationReport, run_investigation_eval

# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/investigator/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PKG_ROOT / "prompts"

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-investigator",
        description=(
            "S4.1 eval runner: runs the Failure Investigator over the "
            "30-broken-test golden set (§19 S4.1, §31.7: top-1 ≥ 80%) and "
            "emits a JSON report."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="failure golden set JSON (default: packages/execution/golden/failure_v1.json)",
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
            "(or pass --base-url/--model; scripts/investigator_run.py reads them from .env)",
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
        golden = load_failure_golden_set(golden_path)
    except FailureGoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = FilePromptStore(_PROMPTS_DIR)
    try:
        spec = store.get(INVESTIGATOR_NAME)
    except PromptNotFound as exc:
        print(
            f"error: prompt {INVESTIGATOR_NAME} is not registered in {_PROMPTS_DIR}: {exc}",
            file=sys.stderr,
        )
        return 2

    gateway = LLMGateway(
        base_url=base_url,
        model=model,
        timeout=max(args.timeout, load_model_settings().timeout_s),
    )
    agent = FailureInvestigatorAgent(store, gateway)
    try:
        report = await run_investigation_eval(golden, agent=agent, model=model, prompt_ref=spec.ref)
    finally:
        await gateway.aclose()
    return _emit(report, report_path=args.report)


def _emit(
    report: InvestigationReport,
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


def _print_summary(report: InvestigationReport, *, file: TextIO) -> None:
    totals = report.totals
    top1_min = report.targets["top1_min"] * 100
    schema_valid = sum(1 for fixture in report.fixtures if fixture.schema_valid)

    print(
        f"investigator {report.agent} · model {report.model} · "
        f"golden {report.golden_name} {report.golden_version} "
        f"({totals.fixtures} fixtures)",
        file=file,
    )
    print(
        f"  top-1 category {totals.passed}/{totals.fixtures} "
        f"({totals.top1_fraction * 100:.1f}%)  target ≥ {top1_min:.1f}%",
        file=file,
    )
    print(
        f"  schema-valid   {schema_valid}/{totals.fixtures} "
        f"({schema_valid / totals.fixtures * 100:.1f}%)",
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
        else:
            reason = f"top-1 {fixture.category} ≠ expected (suggested {fixture.suggested})"
        print(f"    {fixture.fixture_id} {fixture.title} — {reason}", file=file)
