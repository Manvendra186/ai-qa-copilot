"""Lint/type gate for generated tests (build bible §19 S2.3 / §21).

The S2.3 exit gate — "generated code must pass lint + type checks at ≥ 95%"
— runs the **real workspace toolchain**: the TypeScript compiler and ESLint
already installed under ``apps/web/node_modules`` (TypeScript 5.8, ESLint 9,
``typescript-eslint``), exactly the stack the web app lints with.

The sample repositories are intentionally not installable (stub lockfiles —
see ``packages/repository/README.md``), so the generated file is copied into
a disposable sandbox that gets one extra piece of context: a faithful
``@playwright/test`` type stub (``tests/unit/support/playwright-test``) —
the only npm package the generated specs import. ``tsc --strict`` type-checks
the generated code against those types; ESLint checks it with the same
``typescript-eslint recommended`` ruleset the web app itself uses.

This module is deliberately pure stdlib (``subprocess`` + ``shutil``) so the
gate has no dependency of its own.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..agents.automation import GeneratedTest


@dataclass(frozen=True, slots=True)
class Toolchain:
    """A resolvable node + tsc + ESLint toolchain (all absolute paths)."""

    node: Path
    tsc: Path
    eslint: Path
    eslint_config: Path
    playwright_stub: Path


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Outcome of the lint/type gate for one generated file."""

    lint_ok: bool
    type_ok: bool
    lint_output: str
    type_output: str

    @property
    def ok(self) -> bool:
        return self.lint_ok and self.type_ok


def find_toolchain(repo_root: Path) -> Toolchain | None:
    """Locate the workspace toolchain under ``<repo_root>/apps/web``.

    Returns ``None`` when the web app's dependencies are not installed —
    callers skip the gate loudly instead of failing on missing tooling.
    """
    web = repo_root / "apps" / "web"
    node = shutil.which("node")
    candidates = (
        web / "node_modules" / "typescript" / "lib" / "tsc.js",
        web / "node_modules" / "eslint" / "bin" / "eslint.js",
        web / "eslint.generated.config.js",
        repo_root / "tests" / "unit" / "support" / "playwright-test",
    )
    if node is None or not all(candidate.exists() for candidate in candidates):
        return None
    return Toolchain(
        node=Path(node),
        tsc=candidates[0],
        eslint=candidates[1],
        eslint_config=candidates[2],
        playwright_stub=candidates[3],
    )


def prepare_sandbox(sandbox_root: Path, generated: GeneratedTest, toolchain: Toolchain) -> Path:
    """Materialize the generated file (repo-relative) + the stub in a sandbox.

    The sandbox mirrors the target repository's layout for the generated
    file and carries a stubbed ``node_modules/@playwright/test`` so strict
    ``tsc`` can resolve the import without installing the real package.
    """
    target = sandbox_root / generated.file_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generated.content, encoding="utf-8")
    stub_dest = sandbox_root / "node_modules" / "@playwright" / "test"
    stub_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(toolchain.playwright_stub, stub_dest, dirs_exist_ok=True)
    return target


def _run_tool(node: Path, sandbox_root: Path, args: list[str]) -> tuple[bool, str]:
    """Run one toolchain binary (no shell); (ok, combined output)."""
    command = [str(node), *args]
    try:
        proc = subprocess.run(
            command,
            cwd=sandbox_root,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"toolchain error: {exc}"
    output = proc.stdout or ""
    if proc.stderr:
        output += ("\n" if output else "") + proc.stderr
    return proc.returncode == 0, output.strip()


def check_generated_file(sandbox_root: Path, file_path: str, toolchain: Toolchain) -> CheckResult:
    """Run the §21 gate on one generated file: ESLint + strict tsc.

    ``file_path`` is the repo-relative path of the generated file inside
    ``sandbox_root``. Both tools must exit 0 for the file to pass.
    """
    target = sandbox_root / file_path
    type_ok, type_output = _run_tool(
        toolchain.node,
        sandbox_root,
        [
            str(toolchain.tsc),
            "--noEmit",
            "--strict",
            "--target",
            "es2022",
            "--module",
            "esnext",
            "--moduleResolution",
            "bundler",
            "--skipLibCheck",
            str(target),
        ],
    )
    lint_ok, lint_output = _run_tool(
        toolchain.node,
        sandbox_root,
        [
            str(toolchain.eslint),
            "--no-config-lookup",
            "--config",
            str(toolchain.eslint_config),
            "--format",
            "stylish",
            str(target),
        ],
    )
    return CheckResult(
        lint_ok=lint_ok, type_ok=type_ok, lint_output=lint_output, type_output=type_output
    )
