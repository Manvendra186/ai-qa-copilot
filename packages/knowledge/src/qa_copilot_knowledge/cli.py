"""Knowledge CLI (build bible §19 S5.1; mirrors the execution/loop CLI shape).

Subcommands
    golden   run the retrieval golden gate (deterministic; no LLM, no network)
    index    build the index report for a local repository (MVP: "a repository
             can be indexed")
    search   lexical search over a local repository

Exit codes: 0 = success / gate met, 1 = golden gate not met, 2 = usage or
environment error. JSON payloads go to stdout, human summaries to stderr
(cp1252-safe).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from .golden import (
    GoldenReport,
    KnowledgeGoldenSetError,
    default_golden_path,
    run_golden_set,
)
from .models import IndexReport, SearchResult
from .search import KnowledgeIndex
from .sources import repository_file_documents

EXIT_OK = 0
EXIT_GATE_MISSED = 1
EXIT_USAGE = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (KnowledgeGoldenSetError, NotADirectoryError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa_copilot_knowledge",
        description="AI QA Copilot - knowledge core (deterministic retrieval, no LLM)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    golden = sub.add_parser("golden", help="run the retrieval golden gate")
    golden.add_argument("--golden-path", type=Path, default=None, help="golden set JSON path")
    golden.set_defaults(handler=_cmd_golden)

    index = sub.add_parser("index", help="build the index report for a local repository")
    index.add_argument("root", type=Path, help="repository root directory")
    index.set_defaults(handler=_cmd_index)

    search = sub.add_parser("search", help="lexical search over a local repository")
    search.add_argument("root", type=Path, help="repository root directory")
    search.add_argument("query", help="search query")
    search.add_argument("--top-k", type=int, default=5, help="max hits (hard-capped at 5)")
    search.set_defaults(handler=_cmd_search)
    return parser


def _cmd_golden(args: argparse.Namespace) -> int:
    path = args.golden_path or default_golden_path()
    report = run_golden_set(path)
    _print_json(report)
    _print_golden_summary(report)
    return EXIT_OK if report.gate_met else EXIT_GATE_MISSED


def _cmd_index(args: argparse.Namespace) -> int:
    documents, capped = repository_file_documents(args.root)
    index = KnowledgeIndex(documents, capped=capped)
    _print_json(index.report)
    _print_index_summary(index.report)
    return EXIT_OK


def _cmd_search(args: argparse.Namespace) -> int:
    if not args.query.strip():
        print("error: query must be non-blank", file=sys.stderr)
        return EXIT_USAGE
    documents, capped = repository_file_documents(args.root)
    index = KnowledgeIndex(documents, capped=capped)
    result = index.search(args.query, top_k=args.top_k)
    _print_json(result)
    _print_search_summary(result)
    return EXIT_OK


def _print_json(model: IndexReport | SearchResult | GoldenReport) -> None:
    print(model.model_dump_json(indent=2))


def _print_golden_summary(report: GoldenReport) -> None:
    verdict = "PASS" if report.gate_met else "FAIL"
    print(
        f"golden gate [{verdict}] {report.name} v{report.version}: "
        f"{report.passed}/{report.total} top-1 ok (gate >= {report.gate_top1_min:.2f})",
        file=sys.stderr,
    )
    for result in report.results:
        mark = "ok  " if (result.top1_ok and result.topk_ok) else "MISS"
        line = f"  {mark} {result.id}: expected {result.expected_top1}, got {result.actual_top1}"
        print(line, file=sys.stderr)


def _print_index_summary(report: IndexReport) -> None:
    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(report.source_breakdown.items())) or "none"
    capped = " (capped)" if report.capped else ""
    print(
        f"index: {report.document_count} documents, {report.chunk_count} chunks, "
        f"max_chunk_chars={report.max_chunk_chars} [{breakdown}]{capped}",
        file=sys.stderr,
    )


def _print_search_summary(result: SearchResult) -> None:
    print(
        f"search: {len(result.hits)} hits of {result.total_candidates} candidates "
        f"({'truncated' if result.truncated else 'complete'})",
        file=sys.stderr,
    )
    for hit in result.hits:
        matched = ", ".join(hit.matched_terms)
        print(f"  {hit.score:8.4f}  {hit.chunk.document_ref}  matched: {matched}", file=sys.stderr)
