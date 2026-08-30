"""``loop run`` CLI (S4.3) — one failing test through the full loop.

S3 run (broken spec, live) → S3.3 normalize → S4.1 diagnose → S4.2 propose
→ **human approval** (§26: no auto-heal) → apply → S3 re-run — against the
demo app, with the real registered prompts and a live local LLM.

Approval (the S4.3 gate):

* ``--approve`` — apply the patch and re-run (automation/CI path);
* ``--reject``  — decline the patch: nothing applied, no re-run;
* (neither) — interactive ``y/n`` on a TTY (the patch is shown for
  review); with piped stdin (no TTY) the loop **fail-safes to reject** —
  a patch is never auto-applied (§26).

The JSON report (the S4.3 artifact) goes to **stdout** and optionally to
``--report PATH``; the human summary goes to **stderr**, so
``loop ... > report.json`` stays clean.

Exit codes: ``0`` loop closed (``fixed`` · ``declined`` · ``passing``) ·
``1`` loop ran but did not close (``rejected`` · ``not_fixed``) ·
``2`` configuration/usage/LLM/patch error.
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
    FixFixture,
    FixGoldenSet,
    FixGoldenSetError,
    default_fix_golden_path,
    load_fix_golden_set,
)

from ..agents import FIXER_NAME, INVESTIGATOR_NAME, FailureInvestigatorAgent, FixerAgent
from ..config import load_model_settings
from ..fixer.app_context import build_app_context
from ..fixer.live import PlaywrightVerifier
from ..gateway import DEFAULT_TIMEOUT_S, LLMError, LLMGateway
from ..prompts import FilePromptStore, PromptNotFound
from .approval import APPROVE, REJECT
from .live import PlaywrightLoopRunner
from .runner import LoopReport, LoopTarget, exit_code_for, run_fix_loop

#: Where the live loop runs the broken/patched spec (never a real test).
_PROBE_SPEC = "e2e/loop_probe.spec.js"

# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/loop/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PKG_ROOT / "prompts"
_REPO_ROOT = _PKG_ROOT.parent.parent
_DEFAULT_DEMO_APP = _REPO_ROOT.parent / "ai-qa-copilot-demo-app"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-loop",
        description=(
            "Run one failing test through the full S3 -> S4 -> approval -> re-run "
            "loop (S4.3). JSON report on stdout; human summary on stderr."
        ),
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help=(
            "golden fixture id (e.g. FIX-002) — defaults to the first fixable clean-stack fixture"
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="fix golden set JSON (default: packages/execution/golden/fix_v1.json)",
    )
    parser.add_argument(
        "--demo-app",
        type=Path,
        default=None,
        help=(
            "demo app dir with Playwright + node_modules "
            "(default: $DEMO_APP_DIR or the sibling ai-qa-copilot-demo-app)"
        ),
    )
    parser.add_argument(
        "--base-url", default=None, help="OpenAI-compatible base URL (default: $LLM_BASE_URL)"
    )
    parser.add_argument("--model", default=None, help="model id (default: $LLM_MODEL)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help=f"per-call timeout in seconds (default {DEFAULT_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="approve the patch (apply + re-run) — the automation/CI path",
    )
    parser.add_argument(
        "--reject",
        action="store_true",
        help="reject the patch — nothing is applied, no re-run",
    )
    parser.add_argument("--report", default=None, help="also write the JSON report to this file")
    return parser


def _pick_target(golden: FixGoldenSet, fixture_id: str | None) -> LoopTarget:
    fixture = _resolve_fixture(golden, fixture_id)
    return LoopTarget(
        fixture_id=fixture.id,
        title=fixture.title,
        file_path=fixture.file_path,
        test_code=fixture.test_code,
        app_env=dict(fixture.app_env),
        spec_name=_PROBE_SPEC,
    )


def _resolve_fixture(golden: FixGoldenSet, fixture_id: str | None) -> FixFixture:
    if fixture_id:
        for fixture in golden.fixtures:
            if fixture.id == fixture_id:
                return fixture
        available = ", ".join(f.id for f in golden.fixtures)
        raise ValueError(f"unknown --fixture {fixture_id!r} (available: {available})")
    for fixture in golden.fixtures:
        if fixture.has_fix and not fixture.app_env:
            return fixture
    raise ValueError("no fixable clean-stack fixture found in the golden set")


def _harden_streams() -> None:
    """Keep report/summary prints from crashing on a lossy console encoding.

    The JSON report carries arbitrary model text; on Windows the default
    cp1252 stdout cannot encode e.g. ``→`` or CJK. Replacing the few
    undecodable characters is far better than a UnicodeEncodeError
    mid-report (the ``--report`` file is always UTF-8 regardless).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main(
    argv: Sequence[str] | None = None,
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> int:
    """CLI entry point (module docstring: approval, exit codes, output contract)."""
    _harden_streams()
    args = build_parser().parse_args(argv)
    if args.approve and args.reject:
        print("error: --approve and --reject are mutually exclusive", file=sys.stderr)
        return 2
    base = (args.base_url or base_url or os.environ.get("LLM_BASE_URL") or "").strip()
    model_id = (args.model or model or os.environ.get("LLM_MODEL") or "").strip()
    if not base or not model_id:
        print(
            "error: LLM endpoint not configured — set LLM_BASE_URL and LLM_MODEL "
            "(or pass --base-url/--model; scripts/loop_run.py reads them from .env)",
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
        target = _pick_target(golden, args.fixture)
    except (FixGoldenSetError, ValueError) as exc:
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
    # app under test (§23). Opt out with LOOP_NO_APP_CONTEXT=1.
    opt_out = (os.environ.get("LOOP_NO_APP_CONTEXT") or "").strip().lower()
    app_context = "" if opt_out in {"1", "true", "yes", "on"} else build_app_context(demo_app)

    store = FilePromptStore(_PROMPTS_DIR)
    try:
        store.get(FIXER_NAME)
        store.get(INVESTIGATOR_NAME)  # the loop runs S4.1 first — fail loud if absent
    except PromptNotFound as exc:
        print(f"error: prompt not registered in {_PROMPTS_DIR}: {exc}", file=sys.stderr)
        return 2

    gateway = LLMGateway(
        base_url=base_url,
        model=model,
        timeout=max(args.timeout, load_model_settings().timeout_s),
    )
    verifier = PlaywrightLoopRunner(PlaywrightVerifier(demo_app))
    try:
        report = await run_fix_loop(
            target,
            investigator=FailureInvestigatorAgent(store, gateway),
            fixer=FixerAgent(store, gateway),
            spec_runner=verifier,
            decision=APPROVE if args.approve else REJECT if args.reject else None,
            is_tty=sys.stdin.isatty(),
            app_context=app_context or None,
            model=model,
        )
    except (ValueError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        await verifier.aclose()
        await gateway.aclose()

    _emit(args, report)
    return exit_code_for(report.outcome)


def _emit(args: argparse.Namespace, report: LoopReport) -> None:
    """JSON report → stdout (+ ``--report`` file); human summary → stderr."""
    payload = report.to_json()
    print(payload)
    if args.report:
        out_path = Path(args.report)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
    _print_summary(report, file=sys.stderr)


def _print_summary(report: LoopReport, *, file: TextIO) -> None:
    verdict = {
        "fixed": "closed — the fixed test passes (exit 0)",
        "declined": "closed — the failure was correctly declined (exit 0)",
        "passing": "closed — the test was already passing (exit 0)",
        "rejected": "not closed — the patch was rejected, nothing applied (exit 1)",
        "not_fixed": "not closed — the re-run still fails (exit 1)",
        "error": "error — see report (exit 2)",
    }
    print(
        f"loop {report.fixture_id} · model {report.model} · target {report.target_file}",
        file=file,
    )
    print(
        f"  initial run  {'PASSED' if report.initial_run_ok else 'FAILED'}",
        file=file,
    )
    if report.category is not None:
        print(
            f"  diagnosis    {report.category}"
            + (f" (confidence {report.confidence})" if report.confidence is not None else ""),
            file=file,
        )
    if report.action is not None:
        approval = f" · approval {report.approval.decided_by}" if report.approval else ""
        print(f"  proposal     {report.action}{approval}", file=file)
    if report.re_run_ok is not None:
        print(f"  re-run       {'PASSED' if report.re_run_ok else 'FAILED'}", file=file)
    if report.error:
        print(f"  error        {report.error[:200]}", file=file)
    print(f"  result       {report.outcome} — {verdict.get(report.outcome, '')}", file=file)


if __name__ == "__main__":
    sys.exit(main())
