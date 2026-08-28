"""S2.3 live eval CLI (build bible §19 S2.3 / §21).

Usage:
    python -m qa_copilot_ai.automation.cli --base-url http://localhost:8080/v1 \
        --model qwen3-8b [--repo js-web-app=C:\\path\\to\\repo] [--report out.json]

Runs the S2.3 golden set against a local OpenAI-compatible LLM and prints
the §21 gate report (schema + conventions + real tsc/ESLint per fixture,
lint+type pass fraction vs the 95% exit threshold).

The shared S2.x contract for each fixture's repo is built here with the
S2.1 scanner + S2.2 extractor (``qa_copilot_repository`` — a runtime-only
dependency: the ``qa-copilot-ai`` package itself does not depend on it).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from ..agents import AutomationAgent
from ..gateway import LLMError, LLMGateway
from ..prompts import FilePromptStore
from .checker import find_toolchain
from .golden import (
    AutomationGoldenSet,
    AutomationGoldenSetError,
    default_golden_path,
    load_automation_golden_set,
)
from .runner import RepoContext, report_to_json, run_automation_eval

PKG_ROOT = Path(__file__).resolve().parents[3]  # packages/ai
PROMPTS_DIR = PKG_ROOT / "prompts"
REPO_ROOT = PKG_ROOT.parents[1]
SAMPLES_DIR = REPO_ROOT / "packages" / "repository" / "samples" / "sample_repos"


class ConfigError(RuntimeError):
    """A bad CLI/config value (fail loud, with an actionable message)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qa_copilot_ai.automation",
        description="S2.3 automation eval: golden set vs a local LLM, §21 lint/type gate",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Local OpenAI-compatible server root (default: $AI_BASE_URL or http://localhost:8080/v1)",
    )
    parser.add_argument(
        "--model", default=None, help="Model name (default: $AI_MODEL or 'qwen3-8b')"
    )
    parser.add_argument(
        "--extra-body",
        default=None,
        metavar="JSON",
        help=(
            "Extra JSON object merged into every chat-completions body — "
            'server-specific fields, e.g. \'{"chat_template_kwargs": '
            '{"enable_thinking": false}}\' for Qwen3 thinking models on LM Studio'
        ),
    )
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Override a sample repo location (repeatable; default: packages/repository/samples)",
    )
    parser.add_argument(
        "--golden",
        default=None,
        type=Path,
        help="Golden set JSON (default: packages/ai/golden/automation_v1.json)",
    )
    parser.add_argument(
        "--sandbox-dir", default=None, type=Path, help="Sandbox root for tsc/ESLint"
    )
    parser.add_argument("--report", default=None, type=Path, help="Write the JSON report here")
    return parser


def _repo_overrides(pairs: Sequence[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ConfigError(f"--repo expects NAME=PATH, got: {pair!r}")
        name, _, raw = pair.partition("=")
        if not name.strip() or not raw.strip():
            raise ConfigError(f"--repo expects NAME=PATH, got: {pair!r}")
        overrides[name.strip()] = Path(raw.strip()).expanduser().resolve()
    return overrides


def _parse_extra_body(raw: str | None) -> dict[str, object]:
    """Validate ``--extra-body``: a JSON object (or nothing), else fail loud."""
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"--extra-body must be a JSON object, got {raw!r} ({exc.msg})") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"--extra-body must be a JSON object, got {type(value).__name__}")
    return value


def _build_contexts(
    golden: AutomationGoldenSet, repo_overrides: dict[str, Path]
) -> dict[str, RepoContext]:
    """Scan each fixture's sample repo (S2.1) + extract conventions (S2.2)."""
    try:
        from qa_copilot_repository import extract_conventions, scan_repository
    except ImportError as exc:
        raise ConfigError(
            "qa-copilot-repository is not installed — run the S2.3 eval from the "
            "monorepo root environment (uv run …)"
        ) from exc
    contexts: dict[str, RepoContext] = {}
    for repo_name in sorted({fixture.repo for fixture in golden.fixtures}):
        root = repo_overrides.get(repo_name, SAMPLES_DIR / repo_name)
        if not root.is_dir():
            raise ConfigError(f"sample repo for {repo_name!r} not found at: {root}")
        profile = scan_repository(root)
        conventions = extract_conventions(root, profile)
        contexts[repo_name] = RepoContext(profile=profile, conventions=conventions)
    return contexts


def main(argv: Sequence[str] | None = None) -> int:
    """Run the S2.3 eval; 0 = exit gate passed, 1 = failed, 2 = config error."""
    args = build_parser().parse_args(argv)
    base_url = args.base_url or os.environ.get("AI_BASE_URL") or "http://localhost:8080/v1"
    model = args.model or os.environ.get("AI_MODEL") or "qwen3-8b"
    try:
        golden = load_automation_golden_set(
            Path(args.golden) if args.golden else default_golden_path()
        )
        contexts = _build_contexts(golden, _repo_overrides(args.repo))
        store = FilePromptStore(PROMPTS_DIR)
        toolchain = find_toolchain(REPO_ROOT)
        if toolchain is None:
            raise ConfigError(
                "workspace toolchain not found — the S2.3 lint/type gate needs node on PATH, "
                "apps/web/node_modules (pnpm install in apps/web), "
                "and tests/unit/support/playwright-test"
            )
        sandbox_root = (
            Path(args.sandbox_dir)
            if args.sandbox_dir
            else Path(tempfile.gettempdir()) / "qa-copilot-automation"
        )
        agent = AutomationAgent(
            store, LLMGateway(base_url, model, extra_body=_parse_extra_body(args.extra_body))
        )
        report = asyncio.run(
            run_automation_eval(
                golden,
                agent=agent,
                model=model,
                prompt_ref=store.get("test-automator").ref,
                contexts=contexts,
                toolchain=toolchain,
                sandbox_root=sandbox_root,
            )
        )
    except (ConfigError, AutomationGoldenSetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LLMError as exc:
        print(f"LLM error: {exc}", file=sys.stderr)
        return 1

    print(report_to_json(report))
    if args.report is not None:
        Path(args.report).write_text(report_to_json(report) + "\n", encoding="utf-8")
        print(f"report written to: {args.report}", file=sys.stderr)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
