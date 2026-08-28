"""Repository scanner (build bible §7, §19 S2.1).

Deterministic, LLM-free scan of a local repository: language and framework
detection, test-structure detection, package managers, monorepo signals.
The output (:class:`qa_copilot_domain.RepositoryProfile`) is the shared
contract for the S2.2 convention extractor, the S2.3 automation agent, and
the later ``repositories`` persistence (build bible §10).

Safety: the walk prunes VCS/build/vendor directories, never follows
symlinks, caps the file count, and only *reads* small manifests/configs —
source files are classified by name alone, never read.

CLI::

    python -m qa_copilot_repository.scanner <repo-root>   # JSON profile on stdout
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from qa_copilot_domain import RepositoryProfile

#: Directory names that never carry source code of interest (VCS, deps,
#: build output, caches, IDE noise, test artifacts).
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "bower_components",
        "jspm_packages",
        "vendor",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".nox",
        "site-packages",
        "dist",
        "build",
        "out",
        "target",
        "bin",
        "obj",
        ".next",
        ".nuxt",
        ".output",
        ".svelte-kit",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "coverage",
        "playwright-report",
        "test-results",
    }
)

#: Hard cap on files visited per scan (defence against runaway walks).
MAX_FILES = 50_000

#: Manifests/configs are only read up to this size (larger ones are noted + skipped).
MAX_READ_BYTES = 512 * 1024

#: File extension (lowercase) -> language wire string.
LANGUAGE_EXTENSIONS: dict[str, str] = {
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".less": "css",
    ".sass": "css",
    ".scss": "css",
    ".go": "go",
    ".htm": "html",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".kt": "kotlin",
    ".php": "php",
    ".py": "python",
    ".pyi": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scala": "scala",
    ".sh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".svelte": "javascript",
    ".swift": "swift",
    ".ts": "typescript",
    ".cts": "typescript",
    ".mts": "typescript",
    ".tsx": "typescript",
    ".vue": "javascript",
}

#: Dependency name (lowercase) -> framework wire string (npm manifests).
NODE_FRAMEWORKS: dict[str, str] = {
    "@angular/core": "angular",
    "astro": "astro",
    "@nestjs/core": "nestjs",
    "electron": "electron",
    "express": "express",
    "fastify": "fastify",
    "hapi": "hapi",
    "koa": "koa",
    "next": "next",
    "nuxt": "nuxt",
    "react": "react",
    "react-dom": "react",
    "react-native": "react",
    "remix": "remix",
    "@remix-run/react": "remix",
    "solid-js": "solid",
    "svelte": "svelte",
    "tailwindcss": "tailwind",
    "vite": "vite",
    "vue": "vue",
    "webpack": "webpack",
}

#: Dependency name (lowercase, version stripped) -> framework (Python manifests).
PYTHON_FRAMEWORKS: dict[str, str] = {
    "aiohttp": "aiohttp",
    "bottle": "bottle",
    "django": "django",
    "djangorestframework": "django",
    "falcon": "falcon",
    "fastapi": "fastapi",
    "flask": "flask",
    "sanic": "sanic",
    "starlette": "starlette",
    "tornado": "tornado",
}

#: Manifest filename -> (text marker, framework) pairs (plain-text detection).
MANIFEST_MARKERS: dict[str, tuple[tuple[str, str], ...]] = {
    "go.mod": (
        ("gin-gonic/gin", "gin"),
        ("labstack/echo", "echo"),
        ("go-fiber/fiber", "fiber"),
    ),
    "Gemfile": (
        ('gem "rails"', "rails"),
        ("gem 'rails'", "rails"),
        ('gem "sinatra"', "sinatra"),
        ("gem 'sinatra'", "sinatra"),
    ),
    "Cargo.toml": (
        ("actix-web", "actix-web"),
        ("axum", "axum"),
        ("rocket", "rocket"),
    ),
    "pom.xml": (("spring-boot", "spring-boot"),),
}

#: npm dependency names that imply a JS/TS test framework.
NODE_TEST_FRAMEWORK_DEPS: frozenset[str] = frozenset(
    {"jest", "mocha", "playwright", "@playwright/test", "vitest"}
)

_TEST_JS_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


@dataclass
class _Accumulator:
    """Mutable detection state shared by the per-file handlers."""

    frameworks: set[str] = field(default_factory=set)
    test_frameworks: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    monorepo: bool = False
    skipped_manifests: int = 0


def _note(acc: _Accumulator, text: str) -> None:
    if text not in acc.notes:
        acc.notes.append(text)


def read_text_capped(path: Path) -> str | None:
    """Read a manifest/config with a size cap; never raises.

    Returns ``None`` when the file exceeds :data:`MAX_READ_BYTES` or cannot be
    read — callers count the miss (e.g. ``acc.skipped_manifests``).
    """
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _is_requirements_txt(name: str) -> bool:
    return name == "requirements.txt" or (
        name.startswith("requirements-") and name.endswith(".txt")
    )


def _dep_name(raw: str) -> str:
    """Strip extras and version specifiers: ``uvicorn[standard]>=0.30`` -> ``uvicorn``."""
    name = raw.strip().lower()
    for sep in ("[", "(", ";", " ", "\t"):
        idx = name.find(sep)
        if idx != -1:
            name = name[:idx]
    for op in (">=", "<=", "==", "!=", "~=", ">", "<", "="):
        idx = name.find(op)
        if idx != -1:
            name = name[:idx]
    return name.strip()


def _config_framework(name: str) -> str | None:
    """Framework implied by a config-file name (``vite.config.ts`` -> ``vite``)."""
    if name.startswith("vite.config"):
        return "vite"
    if name.startswith("webpack.config"):
        return "webpack"
    if name.startswith("tailwind.config"):
        return "tailwind"
    if name.startswith("svelte.config"):
        return "svelte"
    if name.startswith("next.config"):
        return "next"
    if name.startswith("nuxt.config"):
        return "nuxt"
    if name.startswith("astro.config"):
        return "astro"
    if name == "angular.json":
        return "angular"
    return None


def is_test_file(name: str, parent_dirname: str) -> bool:
    """Test-file conventions: pytest (``test_*.py`` / ``*_test.py``), Go
    (``*_test.go``), JS/TS (``*.test.*`` / ``*.spec.*`` or ``__tests__/``)."""
    lower = name.lower()
    if not lower.endswith(_TEST_JS_EXTS):
        if lower.endswith(".py"):
            stem = lower[: -len(".py")]
            return stem.startswith("test_") or stem.endswith("_test")
        return lower.endswith(".go") and lower.endswith("_test.go")
    if parent_dirname == "__tests__":
        return True
    stem = lower[: lower.rfind(".")]
    return stem.endswith(".test") or stem.endswith(".spec")


def _apply_playwright_testdir(
    root: Path,
    config_path: Path,
    test_dirs: set[str],
    acc: _Accumulator,
) -> None:
    """Pick up ``testDir`` from a Playwright config (relative to that config)."""
    text = read_text_capped(config_path)
    if text is None:
        acc.skipped_manifests += 1
        return
    match = re.search(r"testDir\s*[:=]\s*['\"]([^'\"]+)['\"]", text)
    if match is None:
        return
    try:
        target = (config_path.parent / match.group(1)).resolve()
        rel = target.relative_to(root).as_posix()
    except (OSError, ValueError):
        return
    test_dirs.add(rel)
    _note(acc, f"playwright testDir: {rel} (from {config_path.name})")


def _apply_text_markers(
    path: Path, markers: tuple[tuple[str, str], ...], acc: _Accumulator
) -> None:
    text = read_text_capped(path)
    if text is None:
        acc.skipped_manifests += 1
        return
    for marker, framework in markers:
        if marker in text:
            acc.frameworks.add(framework)


def _test_framework_for_dep(dep_lower: str) -> str | None:
    if dep_lower not in NODE_TEST_FRAMEWORK_DEPS:
        return None
    return "playwright" if dep_lower == "@playwright/test" else dep_lower


def _apply_package_json(path: Path, acc: _Accumulator) -> None:
    text = read_text_capped(path)
    if text is None:
        acc.skipped_manifests += 1
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        acc.skipped_manifests += 1
        return
    if not isinstance(data, dict):
        return
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(key)
        if not isinstance(value, dict):
            continue
        for dep in value:
            dep_lower = str(dep).lower()
            framework = NODE_FRAMEWORKS.get(dep_lower)
            if framework is not None:
                acc.frameworks.add(framework)
            test_fw = _test_framework_for_dep(dep_lower)
            if test_fw is not None:
                acc.test_frameworks.add(test_fw)
    if isinstance(data.get("jest"), dict):
        acc.test_frameworks.add("jest")
    workspaces = data.get("workspaces")
    if isinstance(workspaces, list):
        acc.monorepo = True
        _note(acc, f"package.json workspaces: {', '.join(str(w) for w in workspaces)}")
    elif isinstance(workspaces, dict):
        acc.monorepo = True
        _note(acc, f"package.json workspaces: {', '.join(str(k) for k in workspaces)}")


def _pyproject_dep_names(data: object) -> list[str]:
    """Dependency names from ``[project]``, poetry, ``[dependency-groups]`` and uv."""
    names: list[str] = []
    if not isinstance(data, dict):
        return names
    project = data.get("project")
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list):
            names.extend(str(d) for d in deps)
    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict):
            deps = poetry.get("dependencies")
            if isinstance(deps, dict):
                names.extend(str(k) for k in deps)
            group = poetry.get("group")
            if isinstance(group, dict):
                for member in group.values():
                    if isinstance(member, dict):
                        gdeps = member.get("dependencies")
                        if isinstance(gdeps, dict):
                            names.extend(str(k) for k in gdeps)
        uv = tool.get("uv")
        if isinstance(uv, dict):
            dev = uv.get("dev-dependencies")
            if isinstance(dev, list):
                names.extend(str(d) for d in dev)
    groups = data.get("dependency-groups")
    if isinstance(groups, dict):
        for member in groups.values():
            if isinstance(member, list):
                names.extend(str(d) for d in member)
    return names


def _apply_pyproject(path: Path, acc: _Accumulator) -> None:
    text = read_text_capped(path)
    if text is None:
        acc.skipped_manifests += 1
        return
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        acc.skipped_manifests += 1
        return
    if not isinstance(data, dict):
        return
    if "[tool.pytest.ini_options]" in text:
        acc.test_frameworks.add("pytest")
    for dep in _pyproject_dep_names(data):
        name = _dep_name(dep)
        framework = PYTHON_FRAMEWORKS.get(name)
        if framework is not None:
            acc.frameworks.add(framework)
        if name == "pytest":
            acc.test_frameworks.add("pytest")
    tool = data.get("tool")
    if isinstance(tool, dict):
        uv = tool.get("uv")
        if isinstance(uv, dict):
            workspace = uv.get("workspace")
            if isinstance(workspace, dict):
                members = workspace.get("members")
                if isinstance(members, list):
                    acc.monorepo = True
                    _note(acc, f"uv workspace members: {', '.join(str(m) for m in members)}")


def _apply_requirements(path: Path, acc: _Accumulator) -> None:
    text = read_text_capped(path)
    if text is None:
        acc.skipped_manifests += 1
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = _dep_name(line)
        framework = PYTHON_FRAMEWORKS.get(name)
        if framework is not None:
            acc.frameworks.add(framework)
        if name == "pytest":
            acc.test_frameworks.add("pytest")


def _root_signals(root: Path, acc: _Accumulator) -> set[str]:
    """Package managers (lockfiles/manifests) + monorepo markers at the repo root."""
    managers: set[str] = set()

    def has(name: str) -> bool:
        return (root / name).is_file()

    if has("pnpm-lock.yaml"):
        managers.add("pnpm")
    if has("package-lock.json"):
        managers.add("npm")
    if has("yarn.lock"):
        managers.add("yarn")
    if has("bun.lockb"):
        managers.add("bun")
    if has("uv.lock"):
        managers.add("uv")
    if has("poetry.lock"):
        managers.add("poetry")
    if has("Pipfile") or has("Pipfile.lock"):
        managers.add("pipenv")
    python_managed = managers & {"uv", "poetry", "pipenv"}
    if not python_managed and (
        has("pyproject.toml") or has("setup.py") or has("setup.cfg") or _has_requirements_txt(root)
    ):
        managers.add("pip")
    pnpm_ws = root / "pnpm-workspace.yaml"
    if pnpm_ws.is_file():
        acc.monorepo = True
        text = read_text_capped(pnpm_ws)
        if text is None:
            acc.skipped_manifests += 1
        globs = _pnpm_workspace_globs(text or "")
        if globs:
            _note(acc, f"pnpm workspaces: {', '.join(globs)}")
    for marker in ("lerna.json", "nx.json", "rush.json"):
        if has(marker):
            acc.monorepo = True
    return managers


def _pnpm_workspace_globs(text: str) -> list[str]:
    """Globs from the ``packages:`` list of a pnpm-workspace.yaml.

    Line-based (no YAML dependency): only list items under the top-level
    ``packages:`` key are collected; the scan stops at the next top-level key
    (so ``onlyBuiltDependencies:`` etc. are ignored). Quotes are stripped.
    """
    globs: list[str] = []
    in_packages = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[:1].isspace():
            in_packages = stripped.startswith("packages:")
            continue
        if in_packages and stripped.startswith("- "):
            globs.append(stripped[2:].strip().strip("'\""))
    return globs


def _has_requirements_txt(root: Path) -> bool:
    try:
        return any(_is_requirements_txt(name) for name in os.listdir(root))
    except OSError:
        return False


def _classify_file(
    root: Path,
    dirpath: Path,
    name: str,
    lang_counts: dict[str, int],
    test_dirs: set[str],
    acc: _Accumulator,
) -> bool:
    """Classify one file (language, frameworks, test structure). Returns True if test file."""
    lower = name.lower()
    language = LANGUAGE_EXTENSIONS.get(os.path.splitext(lower)[1])
    if language is not None:
        lang_counts[language] = lang_counts.get(language, 0) + 1

    config_fw = _config_framework(lower)
    if config_fw is not None:
        acc.frameworks.add(config_fw)

    if lower.startswith("playwright.config"):
        acc.test_frameworks.add("playwright")
        _apply_playwright_testdir(root, dirpath / name, test_dirs, acc)

    markers = MANIFEST_MARKERS.get(lower)
    if markers is not None:
        _apply_text_markers(dirpath / name, markers, acc)

    if lower == "package.json":
        _apply_package_json(dirpath / name, acc)
    elif lower == "pyproject.toml":
        _apply_pyproject(dirpath / name, acc)
    elif _is_requirements_txt(lower):
        _apply_requirements(dirpath / name, acc)

    is_test = is_test_file(name, dirpath.name)
    if is_test:
        test_dirs.add(dirpath.relative_to(root).as_posix() or ".")
    return is_test


def scan_repository(root: Path | str) -> RepositoryProfile:
    """Scan a local repository and return a deterministic
    :class:`qa_copilot_domain.RepositoryProfile`.

    Raises:
        ValueError: if ``root`` does not exist or is not a directory.
    """
    root_path = Path(root).expanduser()
    if not root_path.exists():
        raise ValueError(f"repository root does not exist: {root}")
    if not root_path.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    root_path = root_path.resolve()

    acc = _Accumulator()
    lang_counts: dict[str, int] = {}
    test_dirs: set[str] = set()
    test_file_count = 0
    file_count = 0
    capped = False

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            file_count += 1
            if file_count > MAX_FILES:
                capped = True
                break
            if _classify_file(root_path, Path(dirpath), name, lang_counts, test_dirs, acc):
                test_file_count += 1
        if capped:
            break

    managers = _root_signals(root_path, acc)

    if acc.skipped_manifests:
        _note(acc, f"{acc.skipped_manifests} manifest(s) unreadable or unparseable (skipped)")
    if capped:
        _note(acc, f"file count capped at {MAX_FILES}")
    if not acc.test_frameworks and test_file_count == 0:
        _note(acc, "no test framework detected (manual/smoke scripts, or no tests)")
    elif not acc.test_frameworks:
        _note(acc, "test files present but no test framework detected in manifests/configs")

    return RepositoryProfile(
        languages=sorted(lang_counts, key=lambda lang: (-lang_counts[lang], lang)),
        frameworks=sorted(acc.frameworks),
        test_frameworks=sorted(acc.test_frameworks),
        test_dirs=sorted(test_dirs),
        test_file_count=test_file_count,
        package_managers=sorted(managers),
        monorepo=acc.monorepo,
        file_count=file_count,
        notes=acc.notes,
        scanned_at=datetime.now(UTC),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qa_copilot_repository.scanner",
        description="Scan a local repository: languages, frameworks, test structure (S2.1).",
    )
    parser.add_argument("root", help="path to the repository root")
    args = parser.parse_args(argv)
    profile = scan_repository(args.root)
    print(profile.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
