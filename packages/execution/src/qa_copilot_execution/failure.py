"""S3.3 failure normalizer — raw failure text → structured taxonomy fields.

Build bible §15 ("Normalize failures so the AI sees consistent structures,
not raw logs only"), §16 (failure taxonomy), §19 S3.3 (exit: 30 broken tests
normalize 100%). Deterministic and LLM-free: the same raw text always yields
the same :class:`NormalizedFailure`, so the S4.1 Failure Investigator (AI)
reasons over a stable shape (text-first, §16 v1.1) and the S3.3 golden gate
stays reproducible.

Input: one raw failure text — the shape of ``TestResultReport.error``
(message + snippet, ``runner._error_text``). Output: the §16 category (best
guess; ``unknown`` when no signal matches), the matched rule names (the
deterministic "why"), supporting raw lines, and the structural facts found
in the text (``http_status`` / ``selector`` / ``endpoint``).

Usage::

    python -m qa_copilot_execution.failure <failure-file> [--json]
    python -m qa_copilot_execution.failure --golden [--golden-path PATH] [--json]

Exit codes:

- ``0`` — normalized, or the golden gate is met
- ``1`` — golden run completed but the §19 S3.3 gate is missed
- ``2`` — usage error (argparse / unreadable input)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from qa_copilot_domain.entities import NormalizedFailure
from qa_copilot_domain.enums import FailureCategory

from .golden import (
    FailureFixture,
    FailureGoldenSet,
    FailureGoldenSetError,
    GoldenMismatch,
    GoldenReport,
    default_golden_path,
    load_failure_golden_set,
)

__all__ = [
    "EXIT_GATE_MISSED",
    "EXIT_OK",
    "EXIT_USAGE",
    "FailureRule",
    "build_parser",
    "main",
    "mismatches",
    "normalize_failure",
    "run_golden_set",
]

EXIT_OK = 0
EXIT_GATE_MISSED = 1
EXIT_USAGE = 2

#: Evidence lines per failure (keep the AI context small — §15: no raw log dumps).
MAX_EVIDENCE_LINES = 10
#: Each evidence line is truncated to this many characters.
MAX_EVIDENCE_CHARS = 300


@dataclass(frozen=True, slots=True)
class FailureRule:
    """One classification rule (build bible §16): a named, prioritised regex.

    Lower ``priority`` is more decisive — the category of the highest-priority
    matched rule wins; every matched rule still lands in ``category_signals``.
    """

    category: FailureCategory
    name: str
    priority: int
    pattern: re.Pattern[str]


# --- rule table (build bible §16 examples → Playwright error signatures) -----

_ENV_NET = re.compile(
    r"net::ERR_[A-Z0-9_]+|\bECONNREFUSED\b|\bECONNRESET\b|\bETIMEDOUT\b|socket hang up",
    re.IGNORECASE,
)
_ENV_LAUNCH = re.compile(
    r"Executable doesn't exist|browserType\.launch|chromium\.launch", re.IGNORECASE
)
_ENV_CRASH = re.compile(
    r"Target (?:page|context|browser) has been closed|browser has been closed|Target closed",
    re.IGNORECASE,
)
_ENV_CREDENTIALS = re.compile(r"(?<![:\d])\b401\b|(?<![:\d])\b403\b")
_ENV_SERVICE = re.compile(
    r"(?<![:\d])\b502\b|(?<![:\d])\b503\b|(?<![:\d])\b504\b"
    r"|Bad Gateway|Service Unavailable|Gateway Timeout",
    re.IGNORECASE,
)
_ENV_NAV = re.compile(r"page\.goto\(?[^)]*: Timeout|navigating to", re.IGNORECASE)
_DATA_MISSING = re.compile(
    r"(?<![:\d])\b404\b|RecordNotFound|record not found|no rows?\b|0 rows?\b"
    r"|no matching (?:records?|rows?|users?)"
    r"|empty (?:result set|list|table|dataset)",
    re.IGNORECASE,
)
_FLAKY_TIMEOUT = re.compile(r"Test timeout of \d+ ?ms? exceeded|test timed out", re.IGNORECASE)
_FLAKY_RETRY = re.compile(
    r"passed on (?:retry|re-run)|retr(?:y|ied)[^\n]*passed|attempt \d+ passed", re.IGNORECASE
)
_FLAKY_TIMING = re.compile(r"page\.waitForResponse\(?[^)]*: Timeout|waitForEvent\(?[^)]*: Timeout")
_PRODUCT_ASSERTION = re.compile(
    r"expect\([^)]*\)\s*\.\s*(?:toBe|toEqual|toStrictEqual|toContain|toContainEqual"
    r"|toHaveText|toHaveTitle|toHaveURL|toHaveValue|toBeVisible|toBeEnabled|toBeDisabled"
    r"|toHaveAttribute|toMatch|toHaveCount|toHaveLength)\s*\(",
    re.IGNORECASE,
)
_PRODUCT_API = re.compile(
    r"status code is 500|Internal Server Error|(?<![:\d])\b500\b(?!\d)", re.IGNORECASE
)
_AUTO_STRICT = re.compile(r"strict mode violation|locator resolved to \d+ elements", re.IGNORECASE)
_AUTO_TIMING = re.compile(r"waiting for (?:locator|selector)|locator\.\w+: Timeout", re.IGNORECASE)
_AUTO_DOM = re.compile(
    r"Element is not (?:visible|stable|enabled|attached)|not attached to the DOM"
    r"|intercepts pointer events|is outside of the (?:visible )?viewport",
    re.IGNORECASE,
)
_AUTO_TIMEOUT = re.compile(r"Timeout \d+ ms? exceeded", re.IGNORECASE)

#: Priority order encodes env > data > flaky > product > automation (plus
#: ``unknown`` when nothing matches); every match is still reported.
_RULES: tuple[FailureRule, ...] = (
    FailureRule(FailureCategory.ENVIRONMENT_DEFECT, "env.net", 10, _ENV_NET),
    FailureRule(FailureCategory.ENVIRONMENT_DEFECT, "env.launch", 10, _ENV_LAUNCH),
    FailureRule(FailureCategory.ENVIRONMENT_DEFECT, "env.crash", 11, _ENV_CRASH),
    FailureRule(FailureCategory.ENVIRONMENT_DEFECT, "env.credentials", 12, _ENV_CREDENTIALS),
    FailureRule(FailureCategory.ENVIRONMENT_DEFECT, "env.service", 12, _ENV_SERVICE),
    FailureRule(FailureCategory.ENVIRONMENT_DEFECT, "env.nav", 14, _ENV_NAV),
    FailureRule(FailureCategory.TEST_DATA_DEFECT, "data.missing", 20, _DATA_MISSING),
    FailureRule(FailureCategory.FLAKY_BEHAVIOR, "flaky.timeout", 30, _FLAKY_TIMEOUT),
    FailureRule(FailureCategory.FLAKY_BEHAVIOR, "flaky.retry", 30, _FLAKY_RETRY),
    FailureRule(FailureCategory.FLAKY_BEHAVIOR, "flaky.timing", 32, _FLAKY_TIMING),
    FailureRule(FailureCategory.PRODUCT_DEFECT, "product.assertion", 40, _PRODUCT_ASSERTION),
    FailureRule(FailureCategory.PRODUCT_DEFECT, "product.api-status", 40, _PRODUCT_API),
    FailureRule(FailureCategory.AUTOMATION_DEFECT, "auto.strict", 50, _AUTO_STRICT),
    FailureRule(FailureCategory.AUTOMATION_DEFECT, "auto.timing", 50, _AUTO_TIMING),
    FailureRule(FailureCategory.AUTOMATION_DEFECT, "auto.dom", 52, _AUTO_DOM),
    FailureRule(FailureCategory.AUTOMATION_DEFECT, "auto.timeout", 55, _AUTO_TIMEOUT),
)


def normalize_failure(raw: str) -> NormalizedFailure:
    """Normalize one raw failure text onto the §16 taxonomy (S3.3).

    Deterministic: same input → same output (no LLM, no clock, no random).
    Empty/whitespace text → an ``unknown`` failure with no signals/evidence.
    """
    text = raw.strip()
    if not text:
        return NormalizedFailure()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matched = sorted(
        (rule for rule in _RULES if rule.pattern.search(text)),
        key=lambda rule: (rule.priority, rule.name),
    )
    category = matched[0].category if matched else FailureCategory.UNKNOWN
    return NormalizedFailure(
        category=category,
        category_signals=[rule.name for rule in matched],
        evidence=_collect_evidence(lines, matched),
        http_status=_extract_http_status(text),
        selector=_extract_selector(text),
        endpoint=_extract_endpoint(text),
    )


def _collect_evidence(lines: list[str], matched: list[FailureRule]) -> list[str]:
    """Supporting raw lines: the winning rule's matches first, then one per rule.

    Capped at ``MAX_EVIDENCE_LINES`` / ``MAX_EVIDENCE_CHARS`` — §15: the AI
    sees structures, not raw log dumps.
    """
    if not matched:
        evidence: list[str] = lines[:MAX_EVIDENCE_LINES]
    else:
        winner = matched[0]
        seen: set[str] = set()
        evidence = []
        for line in lines:
            if line not in seen and winner.pattern.search(line):
                evidence.append(line)
                seen.add(line)
            if len(evidence) >= MAX_EVIDENCE_LINES:
                break
        for rule in matched[1:]:
            if len(evidence) >= MAX_EVIDENCE_LINES:
                break
            for line in lines:
                if line not in seen and rule.pattern.search(line):
                    evidence.append(line)
                    seen.add(line)
                    break
    return [line[:MAX_EVIDENCE_CHARS] for line in evidence]


_HTTP_STATUS = re.compile(
    r"(?:status code is|status code:|status:|returned|got|failed)\s*:?\s*(\d{3})\b"
    r"|(?<![:\d])\b(\d{3})\s+(?:Not Found|Unauthorized|Forbidden|Bad Gateway"
    r"|Service Unavailable|Gateway Timeout|Internal Server Error)\b"
    r"|\bHTTP\s+(\d{3})\b",
    re.IGNORECASE,
)
_SELECTOR = re.compile(
    r"locator\(\s*[\"'`]([^\"'`\n]+)[\"'`]"
    r"|waiting for (?:locator|selector)\s+[\"'`]([^\"'`\n]+)[\"'`]"
)
_ENDPOINT = re.compile(r"https?://[^\s\"'`<>)\]]+")


def _extract_http_status(text: str) -> int | None:
    match = _HTTP_STATUS.search(text)
    if match is None:
        return None
    return int(next(group for group in match.groups() if group is not None))


def _extract_selector(text: str) -> str | None:
    match = _SELECTOR.search(text)
    if match is None:
        return None
    return next(group for group in match.groups() if group is not None)


def _extract_endpoint(text: str) -> str | None:
    match = _ENDPOINT.search(text)
    return match.group(0) if match is not None else None


# --- golden set runner (build bible §22; the S3.3 gate, §19) -----------------


def mismatches(fixture: FailureFixture, actual: NormalizedFailure) -> list[str]:
    """Mismatch descriptions for one fixture (empty list = normalizes correctly)."""
    expected = fixture.expect
    out: list[str] = []
    if actual.category is not expected.category:
        out.append(f"category: expected {expected.category.value}, got {actual.category.value}")
    if actual.http_status != expected.http_status:
        out.append(f"http_status: expected {expected.http_status}, got {actual.http_status}")
    if actual.selector != expected.selector:
        out.append(f"selector: expected {expected.selector!r}, got {actual.selector!r}")
    if actual.endpoint != expected.endpoint:
        out.append(f"endpoint: expected {expected.endpoint!r}, got {actual.endpoint!r}")
    missing = [name for name in expected.signals if name not in actual.category_signals]
    if missing:
        out.append(f"signals: missing {missing}")
    return out


def run_golden_set(golden: FailureGoldenSet) -> GoldenReport:
    """Score the deterministic normalizer against the golden set (S3.3 gate)."""
    failed: list[GoldenMismatch] = []
    for fixture in golden.fixtures:
        diffs = mismatches(fixture, normalize_failure(fixture.raw))
        if diffs:
            failed.append(GoldenMismatch(id=fixture.id, mismatches=diffs))
    total = len(golden.fixtures)
    passed = total - len(failed)
    fraction = passed / total if total else 1.0
    return GoldenReport(
        total=total,
        passed=passed,
        failed=failed,
        gate=golden.targets.normalize_pass_min,
        gate_met=fraction >= golden.targets.normalize_pass_min,
    )


# --- CLI ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m qa_copilot_execution.failure",
        description=(
            "S3.3 failure normalizer: raw failure text → structured §16 taxonomy "
            "fields (deterministic, LLM-free)."
        ),
    )
    parser.add_argument(
        "failure",
        nargs="?",
        default=None,
        help="raw failure text file ('-' = stdin)",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument(
        "--golden",
        action="store_true",
        help="run the S3.3 golden set (§22) instead of one file",
    )
    parser.add_argument(
        "--golden-path",
        type=Path,
        default=None,
        help="golden set JSON (default: packages/execution/golden/failure_v1.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point (module docstring: usage + exit codes)."""
    args = build_parser().parse_args(argv)
    if args.golden:
        return _run_golden(args)
    if args.failure is None:
        print("error: a failure file is required (or use --golden)", file=sys.stderr)
        return EXIT_USAGE
    raw = _read_input(args.failure)
    if raw is None:
        return EXIT_USAGE
    normalized = normalize_failure(raw)
    if args.json:
        print(json.dumps(normalized.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        _print_normalized(normalized)
    return EXIT_OK


def _run_golden(args: argparse.Namespace) -> int:
    path: Path = args.golden_path or default_golden_path()
    try:
        golden = load_failure_golden_set(path)
    except FailureGoldenSetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    report = run_golden_set(golden)
    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False))
    else:
        _print_golden_report(report, path)
    return EXIT_OK if report.gate_met else EXIT_GATE_MISSED


def _read_input(ref: str) -> str | None:
    if ref == "-":
        return sys.stdin.read()
    try:
        return Path(ref).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read failure file {ref}: {exc}", file=sys.stderr)
        return None


def _print_normalized(normalized: NormalizedFailure) -> None:
    print(f"category: {normalized.category.value}")
    print(f"signals:  {', '.join(normalized.category_signals) or '-'}")
    print("evidence:")
    for line in normalized.evidence or ["-"]:
        print(f"  - {line}")
    print(f"http_status: {normalized.http_status if normalized.http_status is not None else '-'}")
    print(f"selector: {normalized.selector or '-'}")
    print(f"endpoint: {normalized.endpoint or '-'}")


def _print_golden_report(report: GoldenReport, path: Path) -> None:
    verdict = "met" if report.gate_met else "MISSED"
    print(f"golden set: {path}")
    print(f"fixtures: {report.total} · passed: {report.passed} · failed: {len(report.failed)}")
    print(f"gate normalize_pass_min={report.gate:g} → {verdict}")
    for item in report.failed:
        print(f"  {item.id}: " + "; ".join(item.mismatches))


if __name__ == "__main__":
    sys.exit(main())
