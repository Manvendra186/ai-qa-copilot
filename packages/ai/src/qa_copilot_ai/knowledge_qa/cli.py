"""``knowledge-qa run`` CLI (S5.4) — golden Q&A set vs live local LLM.

The JSON report (the S5.4 artifact, §19/§22/§31.7) goes to **stdout** and
optionally to ``--report PATH``; the human summary goes to **stderr**, so
``knowledge-qa ... > report.json`` stays clean.

Exit codes: ``0`` §31.7 targets met (≥ 80% in-scope grounded, 100%
out-of-scope refused) · ``1`` run completed, targets missed · ``2``
configuration/usage error (no LLM call attempted).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from qa_copilot_knowledge import (
    QAGoldenSetError,
    default_qa_golden_path,
    load_qa_golden_set,
)

from ..agents import KNOWLEDGE_QA_NAME, KnowledgeQAAgent
from ..config import load_model_settings
from ..gateway import DEFAULT_TIMEOUT_S, LLMGateway
from ..prompts import FilePromptStore, PromptNotFound
from .runner import QAReport, run_qa_eval

# ``packages/ai`` — this file lives in ``packages/ai/src/qa_copilot_ai/knowledge_qa/``.
_PKG_ROOT = Path(__file__).resolve().parents[3]
_PROMPTS_DIR = _PKG_ROOT / "prompts"

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-knowledge-qa",
        description=(
            "S5.4 eval runner: runs the Knowledge Q&A agent over the golden "
            "Q&A set (§19 S5.4, §31.7: ≥ 80% in-scope grounded, 100% "
            "out-of-scope refused) and emits a JSON report."
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="golden Q&A set JSON (default: packages/knowledge/golden/qa_v1.json)",
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
            "(or pass --base-url/--model; scripts/knowledge_qa_run.py reads them from .env)",
            file=sys.stderr,
        )
        return 2
    if args.timeout <= 0:
        print("error: --timeout must be > 0 seconds", file=sys.stderr)
        return 2
    return asyncio.run(_execute(args, base, model_id))


async def _execute(args: argparse.Namespace, base_url: str, model: str) -> int:
    golden_path = args.golden or default_qa_golden_path()
    try:
        golden = load_qa_golden_set(golden_path)
    except QAGoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    store = FilePromptStore(_PROMPTS_DIR)
    try:
        spec = store.get(KNOWLEDGE_QA_NAME)
    except PromptNotFound as exc:
        print(
            f"error: prompt {KNOWLEDGE_QA_NAME} is not registered in {_PROMPTS_DIR}: {exc}",
            file=sys.stderr,
        )
        return 2

    gateway = LLMGateway(
        base_url=base_url,
        model=model,
        timeout=max(args.timeout, load_model_settings().timeout_s),
    )
    agent = KnowledgeQAAgent(store, gateway)
    try:
        report = await run_qa_eval(golden, agent=agent, model=model, prompt_ref=spec.ref)
    finally:
        await gateway.aclose()
    return _emit(report, report_path=args.report)


def _emit(
    report: QAReport,
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


def _print_summary(report: QAReport, *, file: TextIO) -> None:
    totals = report.totals
    in_scope_min = report.targets["in_scope_min"] * 100
    oos_min = report.targets["out_of_scope_refuse_min"] * 100
    schema_valid = sum(1 for question in report.questions if question.schema_valid)

    print(
        f"knowledge-qa {report.agent} · model {report.model} · "
        f"golden {report.golden_name} {report.golden_version} "
        f"({totals.questions} questions)",
        file=file,
    )
    print(
        f"  in-scope grounded     {totals.in_scope_passed}/{totals.in_scope_questions} "
        f"({totals.in_scope_fraction * 100:.1f}%)  target ≥ {in_scope_min:.1f}%",
        file=file,
    )
    print(
        f"  out-of-scope refused  {totals.out_of_scope_refused}/{totals.out_of_scope_questions} "
        f"({totals.out_of_scope_fraction * 100:.1f}%)  target ≥ {oos_min:.1f}%",
        file=file,
    )
    print(
        f"  schema-valid   {schema_valid}/{totals.questions} "
        f"({schema_valid / totals.questions * 100:.1f}%)",
        file=file,
    )
    if report.passed:
        print("  result         PASSED (exit 0)", file=file)
        return
    print("  result         FAILED (exit 1)", file=file)
    for question in report.questions:
        if question.passed:
            continue
        if not question.schema_valid:
            reason = f"error: {(question.error or 'contract-invalid output')[:160]}"
        elif question.expected_in_scope:
            misses = []
            if not question.grounded:
                misses.append("not grounded on expected facts")
            if not question.citations_ok:
                misses.append("citations missing/invalid")
            if question.answered_in_scope is False:
                misses.append("refused instead of answering")
            reason = ", ".join(misses) or "gate miss"
        else:
            reason = "answered instead of refusing"
        print(f"    {question.id} {question.question[:60]} — {reason}", file=file)
