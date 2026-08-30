"""``fixer run`` CLI (S4.2) — 10 broken-test fixes vs live local LLM + demo app.

The JSON report (the S4.2 artifact, §19/§22/§31.7) goes to **stdout** and
optionally to ``--report PATH``; the human summary goes to **stderr**, so
``fixer ... > report.json`` stays clean.

The gate unit per fixture (§19 S4.2, anti-gaming per §26): the action
matches the fixture's ground truth (patch/decline), the patch applies,
AND the patched test passes the live :class:`~qa_copilot_ai.fixer.live.PlaywrightVerifier`.

Exit codes: ``0`` gate passed (≥ 5/10, §31.7) · ``1`` run completed, gate
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
    FixGoldenSetError,
    default_fix_golden_path,
    load_fix_golden_set,
)

from ..agents import FIXER_NAME, INVESTIGATOR_NAME, FailureInvestigatorAgent, FixerAgent
from ..config import load_model_settings
from ..gateway import DEFAULT_TIMEOUT_S, LLMGateway
from ..prompts import FilePromptStore, PromptNotFound
from .app_context import build_app_context
from .live import PlaywrightVerifier
from .runner import FixEvalReport, FixtureFixResult, run_fix_eval

# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/fixer/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PKG_ROOT / "prompts"
_REPO_ROOT = _PKG_ROOT.parent.parent
_DEFAULT_DEMO_APP = _REPO_ROOT.parent / "ai-qa-copilot-demo-app"

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-fixer",
        description=(
            "S4.2 eval runner: runs the Fix Agent over the 10-broken-test "
            "golden set (§19 S4.2, §31.7: ≥ 5/10 fixes applicable and "
            "passing, correct action per §26) and verifies each patched spec "
            "live against the demo app. Emits a JSON report."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="fix golden set JSON (default: packages/execution/golden/fix_v1.json)",
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
        "--demo-app",
        type=Path,
        default=None,
        help=f"demo app dir with Playwright + node_modules (default: {_DEFAULT_DEMO_APP})",
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
            "(or pass --base-url/--model; scripts/fixer_run.py reads them from .env)",
            file=sys.stderr,
        )
        return 2
    if args.timeout <= 0:
        print("error: --timeout must be > 0 seconds", file=sys.stderr)
        return 2
    return asyncio.run(_execute(args, base, model_id))


async def _execute(args: argparse.Namespace, base_url: str, model: str) -> int:
    golden_path = args.golden or default_fix_golden_path()
    try:
        golden = load_fix_golden_set(golden_path)
    except FixGoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    demo_app = Path(args.demo_app or os.environ.get("DEMO_APP_DIR") or _DEFAULT_DEMO_APP).resolve()
    if (
        not (demo_app / "playwright.config.js").is_file()
        or not (demo_app / "node_modules/@playwright/test/cli.js").is_file()
    ):
        print(
            f"error: demo app at {demo_app} is not usable — it needs "
            "playwright.config.js and node_modules/@playwright/test "
            "(run `pnpm install` + `npx playwright install` in the demo app)",
            file=sys.stderr,
        )
        return 2

    # Read-only application context for the Fix Agent (v2 prompt) — the S4.2
    # app under test (§23): test-ids, routes, DOM, API shapes, seed data.
    # Opt out with FIXER_NO_APP_CONTEXT=1 to A/B against the v1 behavior.
    opt_out = (os.environ.get("FIXER_NO_APP_CONTEXT") or "").strip().lower()
    app_context = "" if opt_out in {"1", "true", "yes", "on"} else build_app_context(demo_app)

    store = FilePromptStore(_PROMPTS_DIR)
    try:
        fixer_spec = store.get(FIXER_NAME)
        store.get(INVESTIGATOR_NAME)  # the pipeline runs S4.1 first — fail loud if absent
    except PromptNotFound as exc:
        print(f"error: prompt not registered in {_PROMPTS_DIR}: {exc}", file=sys.stderr)
        return 2

    gateway = LLMGateway(
        base_url=base_url,
        model=model,
        timeout=max(args.timeout, load_model_settings().timeout_s),
    )
    verifier = PlaywrightVerifier(demo_app)
    investigator = FailureInvestigatorAgent(store, gateway)
    fixer = FixerAgent(store, gateway)
    try:
        report = await run_fix_eval(
            golden,
            investigator=investigator,
            fixer=fixer,
            model=model,
            fixer_prompt_ref=fixer_spec.ref,
            verifier=verifier,
            app_context=app_context or None,
        )
    finally:
        await verifier.aclose()  # stops the demo stack the verifier started (if any)
        await gateway.aclose()
    return _emit(report, report_path=args.report)


def _emit(
    report: FixEvalReport,
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


def _print_summary(report: FixEvalReport, *, file: TextIO) -> None:
    totals = report.totals
    passing_min = report.targets["passing_min"] * 100
    print(
        f"fixer {report.agent} · model {report.model} · "
        f"golden {report.golden_name} {report.golden_version} "
        f"({totals.fixtures} fixtures)",
        file=file,
    )
    print(
        f"  passing        {totals.passed}/{totals.fixtures} "
        f"({totals.passing_fraction * 100:.1f}%)  target ≥ {passing_min:.1f}% "
        "(correct action + patch applies + patched test passes)",
        file=file,
    )
    print(
        f"  applicable     {totals.applicable}/{totals.fixtures}   "
        f"declined {totals.declined}   correct action {totals.correct_action}/{totals.fixtures}",
        file=file,
    )
    if report.passed:
        print("  result         PASSED (exit 0)", file=file)
        return
    print("  result         FAILED (exit 1)", file=file)
    for fixture in report.fixtures:
        if fixture.passing and fixture.correct_action:
            continue
        print(f"    {fixture.fixture_id} {fixture.title} — {_failure_reason(fixture)}", file=file)


def _failure_reason(fixture: FixtureFixResult) -> str:
    if fixture.error:
        return f"error: {fixture.error[:160]}"
    if fixture.action != fixture.expected_action:
        return f"action {fixture.action} ≠ expected {fixture.expected_action}"
    if fixture.applicable is False:
        return "patch does not apply"
    if fixture.passing is False:
        return "patched test did not pass the verifier"
    return "not passing"
