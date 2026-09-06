"""``python -m qa_copilot_integrations.jira`` CLI (build bible §19 S7.4).

Subcommands (all JSON on **stdout**; human summary on **stderr**):

- ``map FAILURE_JSON --project-key KEY`` — deterministic failure → Jira
  issue ``fields`` payload (the exact body the client POSTs; the ``map``
  pin in the golden set must match 100%); ``FAILURE_JSON`` is a path to a
  failure JSON object or ``-`` for stdin.
- ``golden [--path FILE]`` — replays the S7.4 golden set (mapping pins +
  fake-server client fixtures, §22) and emits the §31.7 gate report.

Exit codes: 0 success / gate met · 1 gate failed · 2 configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from qa_copilot_integrations.jira.golden import default_golden_path, load_jira_golden_set
from qa_copilot_integrations.jira.issue import build_issue_payload
from qa_copilot_integrations.jira.runner import run_jira_eval


def _emit(obj: object) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _force_utf8_stdio() -> None:
    """Emit UTF-8 regardless of the console code page.

    The golden set and failure text carry non-ASCII (``→``, em-dashes,
    arbitrary user text). On Windows a redirected / piped stdout defaults to
    the ANSI code page (e.g. cp1252), which cannot encode those — the
    ``ensure_ascii=False`` dump would raise ``UnicodeEncodeError``. Forcing
    UTF-8 keeps the output readable *and* always encodable.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            # Not a reconfigurable TextIOWrapper (e.g. detached/patched) —
            # the default encoding stands; ASCII-safe content still works.
            pass


def _load_failure_arg(text: str) -> dict[str, object]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"failure JSON is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("failure JSON must be an object")
    return data


def _cmd_map(args: argparse.Namespace) -> int:
    try:
        if args.failure == "-":
            raw = sys.stdin.read()
        else:
            raw = Path(args.failure).read_text(encoding="utf-8")
        failure = _load_failure_arg(raw)
        payload = build_issue_payload(failure, args.project_key)
    except (OSError, ValueError) as exc:
        print(f"jira map: {exc}", file=sys.stderr)
        return 2
    _emit(payload)
    return 0


def _cmd_golden(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else default_golden_path()
    try:
        golden = load_jira_golden_set(path)
    except (OSError, ValueError) as exc:
        print(f"jira golden: cannot load golden set: {exc}", file=sys.stderr)
        return 2
    report = run_jira_eval(golden)
    _emit(report.model_dump(mode="json"))
    status = "PASS" if report.gate_met else "FAIL"
    print(
        f"jira golden {report.golden_name}@{report.golden_version}: "
        f"{report.passed}/{report.passed + len(report.failures)} cases, "
        f"score={report.score} gate={report.gate} → {status}",
        file=sys.stderr,
    )
    return 0 if report.gate_met else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qa_copilot_integrations.jira",
        description="S7.4 Jira linking integration CLI (deterministic, LLM-free).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_map = sub.add_parser("map", help="deterministic failure → Jira issue fields payload")
    p_map.add_argument("failure", help="path to failure JSON, or '-' for stdin")
    p_map.add_argument("--project-key", required=True, help="Jira project key (e.g. QA)")
    p_map.set_defaults(func=_cmd_map)

    p_golden = sub.add_parser("golden", help="replay the S7.4 golden set (§31.7 gate)")
    p_golden.add_argument(
        "--path",
        help="golden JSON path (default: packages/integrations/golden/jira_v1.json)",
    )
    p_golden.set_defaults(func=_cmd_golden)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


__all__ = ["build_parser", "main"]
