"""Execution-worker CLI (build bible §15, §31.11; S3.1).

Usage::

    python -m qa_copilot_execution <target-dir> [--filter TEXT] [--timeout S]
                                   [--store PATH] [--run-id ID] [--json]

Runs the target repository's Playwright suite (``playwright test
--reporter=json``, resolved through the target's ``node_modules/.bin``),
captures the §15 artifacts into the §31.11 store layout, and prints a
summary. The CLI is database-free — persistence is the repository
package's job (``qa_copilot_repository.runs.persist_run``), wired in by
the caller (the S3.2 API job / verification scripts).

Exit codes:

- ``0`` — run completed and every test passed
- ``1`` — run completed but at least one test failed
- ``2`` — usage error (argparse)
- ``3`` — the worker itself failed (spawn error, timeout, no JSON report)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from qa_copilot_domain.enums import RunStatus

from .report import RunReport
from .runner import PlaywrightConfig, run_playwright

EXIT_OK = 0
EXIT_TESTS_FAILED = 1
EXIT_USAGE = 2
EXIT_WORKER_FAILED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qa_copilot_execution",
        description="Run a target repo's Playwright suite and capture §15 artifacts.",
    )
    parser.add_argument("target", help="target repository directory (has the Playwright tests)")
    parser.add_argument("--filter", default=None, help="Playwright test filter (positional arg)")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="subprocess timeout in seconds (default: worker default of 600)",
    )
    parser.add_argument(
        "--store",
        default=None,
        help="artifact store root (default: <cwd>/data/artifacts)",
    )
    parser.add_argument("--run-id", default=None, help="run id (default: a fresh UUID)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full RunReport as JSON (machine-readable)",
    )
    return parser


def _print_summary(report: RunReport, run_id: str) -> None:
    t = report.totals
    print(f"run {run_id}: {report.status.value}")
    print(
        f"  tests: total={t.total} passed={t.passed} failed={t.failed} "
        f"flaky={t.flaky} skipped={t.skipped}"
    )
    for result in report.results:
        kinds = ", ".join(a.type.value for a in result.artifacts) or "-"
        print(
            f"  [{result.status.value}] {result.title} ({result.duration_ms} ms) artifacts: {kinds}"
        )
    if report.error:
        print(f"  error: {report.error}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    target = Path(args.target)
    if not target.is_dir():
        print(f"error: target directory not found: {target}", file=sys.stderr)
        return EXIT_USAGE

    run_id = args.run_id or str(uuid4())
    config = PlaywrightConfig(
        target_dir=target,
        store_root=Path(args.store) if args.store else None,
        timeout_s=args.timeout if args.timeout is not None else 600.0,
        test_filter=args.filter,
    )
    report = run_playwright(config, run_id)

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        _print_summary(report, run_id)

    if report.status is RunStatus.FAILED:
        return EXIT_WORKER_FAILED
    if report.totals.failed > 0:
        return EXIT_TESTS_FAILED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
