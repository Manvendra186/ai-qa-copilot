"""``python -m qa_copilot_integrations.github`` CLI (S7.1, §19 S7.1).

Subcommands (all JSON on **stdout**; human summary on **stderr**):

- ``repo OWNER REPO`` — ``resolve_repository`` → the §10 ``repositories``
  fields (``url`` / ``default_branch`` + identity);
- ``pr-files OWNER REPO NUMBER`` — ``fetch_pull_request`` → head/base SHAs
  + ``files`` in the exact S6.1 ``files[]`` shape (the contract S7.2 feeds
  into the impact core);
- ``golden`` — replays the S7.1 golden set (default
  ``packages/integrations/golden/github_v1.json``) against in-process fake
  GitHub servers and emits the §31.7 gate report.

Auth: PAT from ``--token`` or ``$GITHUB_TOKEN`` (V1 — PAT only, §19 S7.1;
never printed, redacted out of every error path, §17). Base URL from
``--base-url`` or ``$GITHUB_BASE_URL`` (defaults to github.com).

Exit codes: ``0`` success / gate met · ``1`` API error (401/404/5xx — a
redacted JSON error object on stdout) · ``2`` configuration/usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .client import (
    DEFAULT_BASE_URL,
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubNotFoundError,
)
from .golden import (
    GitHubGoldenSetError,
    default_golden_path,
    load_github_golden_set,
)
from .runner import run_github_eval

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa-copilot-integrations.github",
        description=(
            "S7.1 GitHub core (LLM-free): resolve a repository, fetch a PR's "
            "changed files (S6.1 files[] shape), or replay the golden gate."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"GitHub API base URL (default: $GITHUB_BASE_URL or {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="GitHub PAT (default: $GITHUB_TOKEN); never printed (§17)",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request timeout (s)")

    sub = parser.add_subparsers(dest="command", required=True)

    repo_p = sub.add_parser("repo", help="resolve_repository → repositories-compatible fields")
    repo_p.add_argument("owner")
    repo_p.add_argument("repo")

    pr_p = sub.add_parser(
        "pr-files", help="fetch_pull_request → head/base SHAs + S6.1 files[] JSON"
    )
    pr_p.add_argument("owner")
    pr_p.add_argument("repo")
    pr_p.add_argument("number", type=int)

    golden_p = sub.add_parser("golden", help="replay the S7.1 golden set (offline gate)")
    golden_p.add_argument(
        "--golden",
        type=Path,
        default=None,
        help="golden set JSON (default: packages/integrations/golden/github_v1.json)",
    )
    golden_p.add_argument("--report", type=Path, default=None, help="also write the report here")
    return parser


def _resolve_base_url(cli_value: str | None) -> str:
    return cli_value or os.environ.get("GITHUB_BASE_URL") or DEFAULT_BASE_URL


def _resolve_token(cli_value: str | None) -> str | None:
    """PAT from the flag or ``$GITHUB_TOKEN`` (optional for public repos)."""
    return cli_value or os.environ.get("GITHUB_TOKEN") or None


def _error_payload(exc: GitHubError) -> dict[str, object]:
    """Redacted JSON error object (stdout contract on failure)."""
    kind = "auth" if isinstance(exc, GitHubAuthError) else (
        "not_found" if isinstance(exc, GitHubNotFoundError) else "http"
    )
    return {"error": kind, "status": exc.status, "message": str(exc)}


async def _run_repo(
    base_url: str, token: str | None, timeout_s: float, owner: str, repo: str
) -> dict[str, object]:
    async with GitHubClient(base_url=base_url, token=token, timeout_s=timeout_s) as client:
        info = await client.resolve_repository(owner, repo)
    return {
        "owner": info.owner,
        "name": info.name,
        "full_name": info.full_name,
        "html_url": info.html_url,
        "url": info.url,
        "default_branch": info.default_branch,
    }


async def _run_pr_files(
    base_url: str, token: str | None, timeout_s: float, owner: str, repo: str, number: int
) -> dict[str, object]:
    async with GitHubClient(base_url=base_url, token=token, timeout_s=timeout_s) as client:
        info = await client.fetch_pull_request(owner, repo, number)
    return {
        "number": info.number,
        "title": info.title,
        "state": info.state,
        "html_url": info.html_url,
        "head_sha": info.head_sha,
        "head_ref": info.head_ref,
        "base_sha": info.base_sha,
        "base_ref": info.base_ref,
        "files": list(info.changed_files),  # exact S6.1 files[] shape
    }


def _emit_json(
    payload: dict[str, object],
    *,
    report_path: Path | None = None,
    stdout: TextIO | None = None,
) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    print(text, file=stdout if stdout is not None else sys.stdout)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")


def _print_summary(line: str, stderr: TextIO | None = None) -> None:
    print(line, file=stderr if stderr is not None else sys.stderr)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """CLI entry point (module docstring: contract + exit codes)."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    base_url = _resolve_base_url(args.base_url)
    token = _resolve_token(args.token)

    if args.command == "golden":
        golden_path = args.golden or default_golden_path()
        try:
            golden = load_github_golden_set(golden_path)
        except (OSError, GitHubGoldenSetError, ValueError) as exc:
            _print_summary(f"golden load error: {exc}", stderr)
            return 2
        report = run_github_eval(golden)
        _emit_json(report.model_dump(), report_path=args.report, stdout=stdout)
        _print_summary(
            f"github S7.1 · golden {report.golden_name} {report.golden_version} "
            f"({report.fixtures} fixtures) · deterministic core",
            stderr,
        )
        _print_summary(
            f"  match  {report.passed}/{report.fixtures} "
            f"({report.pass_fraction * 100:.1f}%)  "
            f"target ≥ {report.targets['pass_min'] * 100:.1f}%",
            stderr,
        )
        for case in report.cases:
            if not case.passed:
                _print_summary(f"    {case.fixture_id} {case.title} — {case.error}", stderr)
        _print_summary(
            f"  result         {'PASSED (exit 0)' if report.gate_passed else 'FAILED (exit 1)'}",
            stderr,
        )
        return 0 if report.gate_passed else 1

    call = (
        _run_repo(base_url, token, args.timeout, args.owner, args.repo)
        if args.command == "repo"
        else _run_pr_files(base_url, token, args.timeout, args.owner, args.repo, args.number)
    )
    try:
        payload = asyncio.run(call)
    except GitHubError as exc:
        _emit_json(_error_payload(exc), stdout=stdout)
        _print_summary(f"github {args.command} failed: {exc}", stderr)
        return 1
    except ValueError as exc:
        _print_summary(f"usage error: {exc}", stderr)
        return 2
    _emit_json(payload, stdout=stdout)
    _print_summary(f"github {args.command} ok", stderr)
    return 0
