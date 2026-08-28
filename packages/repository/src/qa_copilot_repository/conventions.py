"""Convention extractor (build bible §7, §19 S2.2).

Deterministic, LLM-free extraction of the target repo's *test conventions*
on top of the S2.1 scanner (:class:`qa_copilot_domain.RepositoryProfile`):
locator APIs in use, page objects, fixtures, helpers, test-runner configs,
the ``data-testid`` vocabulary, base URL, test-file naming patterns, and
the ``package.json`` scripts that launch tests.

The output (:class:`qa_copilot_domain.TestConventions`) is the shared
contract the S2.3 automation agent consumes to generate code that matches
how the repo already tests.

Safety reuses the scanner's rules: pruned walk, file-count cap, capped
reads, symlinks never followed. Only test files, small configs, and
``data-testid`` markers in app source are read.

CLI::

    python -m qa_copilot_repository.conventions <repo-root>   # JSON on stdout
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from qa_copilot_domain import LocatorStyle, RepositoryProfile, TestConventions, TestScript

from .scanner import MAX_FILES, SKIP_DIRS, is_test_file, read_text_capped, scan_repository

#: Playwright locator APIs recognized in test code (wire strings, §19 S2.2).
PLAYWRIGHT_LOCATOR_APIS: tuple[str, ...] = (
    "getByAltText",
    "getByLabel",
    "getByPlaceholder",
    "getByRole",
    "getByTestId",
    "getByText",
    "getByTitle",
    "locator",
)

#: Per-API occurrence patterns (``locator`` needs the dot to skip bare vars).
_LOCATOR_RES: dict[str, re.Pattern[str]] = {
    api: re.compile(r"\.locator\s*\(") if api == "locator" else re.compile(rf"\b{api}\s*\(")
    for api in PLAYWRIGHT_LOCATOR_APIS
}

_TEST_EXT: tuple[str, ...] = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
_HELPER_EXT: tuple[str, ...] = _TEST_EXT + (".py",)
_APP_SRC_EXT: tuple[str, ...] = _TEST_EXT + (".htm", ".html", ".vue")

#: Test-runner config files (lowercase names).
_TEST_CONFIG_NAMES: frozenset[str] = frozenset(
    {
        ".mocharc.js",
        ".mocharc.json",
        ".mocharc.yaml",
        ".mocharc.yml",
        "jest.config.cjs",
        "jest.config.js",
        "jest.config.json",
        "jest.config.mjs",
        "jest.config.ts",
        "karma.conf.js",
        "playwright.config.cjs",
        "playwright.config.js",
        "playwright.config.mjs",
        "playwright.config.mts",
        "playwright.config.ts",
        "playwright.config.cts",
        "pytest.ini",
        "vitest.config.cjs",
        "vitest.config.js",
        "vitest.config.mjs",
        "vitest.config.ts",
        "vitest.workspace.cjs",
        "vitest.workspace.js",
        "vitest.workspace.mjs",
        "vitest.workspace.ts",
    }
)

#: Files that are fixtures by name (lowercase), anywhere in the repo.
_FIXTURE_NAMES: frozenset[str] = frozenset(
    {
        "conftest.py",
        "fixtures.cjs",
        "fixtures.js",
        "fixtures.mjs",
        "fixtures.ts",
        "jest.setup.js",
        "jest.setup.ts",
        "setup_tests.js",
        "setup_tests.jsx",
        "setup_tests.ts",
        "setup_tests.tsx",
        "vitest.setup.js",
        "vitest.setup.ts",
    }
)

#: Directory names whose files are page-object candidates.
_PAGE_OBJECT_DIRS: frozenset[str] = frozenset(
    {"page-objects", "page_objects", "pagemodels", "pages", "pageobjects"}
)

#: package.json script names that launch tests (lowercase keys).
_TEST_SCRIPT_KEYS: frozenset[str] = frozenset({"cypress", "e2e", "playwright", "smoke", "spec"})

#: Command tokens that identify a test runner regardless of script name.
_TEST_RUNNER_TOKENS: tuple[str, ...] = (
    "cypress",
    "jest",
    "mocha",
    "playwright test",
    "pytest",
    "vitest",
)

_DATA_TESTID_RE = re.compile(r"data-testid\s*=\s*[\"']([A-Za-z0-9][A-Za-z0-9_-]*)[\"']")
_BASE_URL_RE = re.compile(r"baseURL\s*[:=]\s*[\"']([^\"']+)[\"']")
_TEST_EXTEND_RE = re.compile(r"\b(?:test|base)\s*\.\s*extend\s*\(")

#: Directory names that mark an ancestor dir as part of the test tree
#: (so ``tests/helpers.py`` counts when tests live in ``tests/unit``).
_TESTISH_DIR_NAMES: frozenset[str] = frozenset(
    {"cypress", "e2e", "features", "spec", "specs", "test", "tests", "__tests__"}
)


def _rel_dir(rel: str) -> str:
    """Repo-relative POSIX dir of ``rel`` (``"."`` at the root)."""
    return rel.rsplit("/", 1)[0] if "/" in rel else "."


def _in_test_tree(rel_dir: str, test_dirs: frozenset[str]) -> bool:
    """True when ``rel_dir`` is a test dir, under one, or a testish ancestor.

    The ancestor rule is name-gated: ``src`` (holding ``src/__tests__``) is
    app code, while ``tests`` (holding ``tests/unit``) is test tree.
    """
    if "." in test_dirs:
        return True
    for td in test_dirs:
        if rel_dir == td or rel_dir.startswith(td + "/"):
            return True
        if td.startswith(rel_dir + "/") and rel_dir.rsplit("/", 1)[-1] in _TESTISH_DIR_NAMES:
            return True
    return False


def _pattern_for(name: str, parent_dirname: str) -> str:
    """The observed test-file naming convention as a glob (e.g. ``*.spec.ts``)."""
    lower = name.lower()
    if lower.endswith(_TEST_EXT):
        ext = lower[lower.rfind(".") :]
        stem = lower[: lower.rfind(".")]
        if stem.endswith(".spec"):
            return f"*.spec{ext}"
        if stem.endswith(".test"):
            return f"*.test{ext}"
        if parent_dirname == "__tests__":
            return "__tests__/*"
    elif lower.endswith(".py"):
        stem = lower[:-3]
        if stem.startswith("test_"):
            return "test_*.py"
        if stem.endswith("_test"):
            return "*_test.py"
    elif lower.endswith("_test.go"):
        return "*_test.go"
    return "*"


def _framework_of(text: str) -> str:
    """Attribute a test file's locator usage to a toolkit via its imports."""
    if "@playwright/test" in text:
        return "playwright"
    if "@testing-library/" in text:
        return "testing-library"
    return "generic"


def _is_test_script(name: str, command: str) -> bool:
    """True when a package.json script launches tests (name or runner token)."""
    key = name.lower()
    if key in _TEST_SCRIPT_KEYS or key.startswith("test"):
        return True
    cmd = command.lower()
    return any(token in cmd for token in _TEST_RUNNER_TOKENS)


def _test_scripts_from(path: Path) -> list[TestScript] | None:
    """Test-launching scripts of one package.json (``None`` when unreadable)."""
    text = read_text_capped(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    scripts = data.get("scripts") if isinstance(data, dict) else None
    if not isinstance(scripts, dict):
        return []
    out: list[TestScript] = []
    for name, command in scripts.items():
        if (
            isinstance(name, str)
            and isinstance(command, str)
            and name.strip()
            and command.strip()
            and _is_test_script(name, command)
        ):
            out.append(TestScript(name=name.strip(), command=command.strip()))
    return out


def _is_page_object(name: str, parent_dirname: str, text: str) -> bool:
    """Page-object heuristic: name/dir signal, or dense locator usage."""
    lower = name.lower()
    if not lower.endswith(_TEST_EXT):
        return False
    if "page" in lower or parent_dirname.lower() in _PAGE_OBJECT_DIRS:
        return True
    distinct = sum(1 for pattern in _LOCATOR_RES.values() if pattern.search(text))
    return distinct >= 2


def _walk(root: Path) -> tuple[list[Path], bool]:
    """All files under ``root`` (pruned, capped, deterministic order)."""
    files: list[Path] = []
    capped = False
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            count += 1
            if count > MAX_FILES:
                capped = True
                break
            files.append(Path(dirpath) / name)
        if capped:
            break
    return files, capped


def extract_conventions(
    root: Path | str, profile: RepositoryProfile | None = None
) -> TestConventions:
    """Extract the target repo's test conventions (build bible §19 S2.2).

    Reuses ``profile`` (S2.1 scanner output) when given — otherwise the repo
    is scanned first. Deterministic: the same repo yields the same
    conventions; only ``scanned_at`` varies.

    Raises:
        ValueError: if ``root`` does not exist or is not a directory.
    """
    root_path = Path(root).expanduser()
    if not root_path.exists():
        raise ValueError(f"repository root does not exist: {root}")
    if not root_path.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    root_path = root_path.resolve()
    prof = profile if profile is not None else scan_repository(root_path)

    # --- pass 1: locate test files (name-only, no reads) ----------------------
    files, capped = _walk(root_path)
    test_file_rels = [
        path.relative_to(root_path).as_posix()
        for path in files
        if is_test_file(path.name, path.parent.name)
    ]
    test_dirs: frozenset[str] = frozenset(
        {_rel_dir(rel) for rel in test_file_rels} | set(prof.test_dirs)
    )

    # --- pass 2: classify (bounded reads) --------------------------------------
    patterns: set[str] = set()
    locator_counts: dict[tuple[str, str], int] = {}
    fixtures: set[str] = set()
    page_objects: set[str] = set()
    helpers: set[str] = set()
    test_configs: set[str] = set()
    test_ids: set[str] = set()
    scripts: list[TestScript] = []
    seen_scripts: set[tuple[str, str]] = set()
    base_url: str | None = None
    unreadable = 0
    test_file_count = 0

    for path in files:
        rel = path.relative_to(root_path).as_posix()
        name = path.name
        lower = name.lower()
        rel_dir = _rel_dir(rel)
        parent = path.parent.name

        if lower == "package.json":
            found = _test_scripts_from(path)
            if found is None:
                unreadable += 1
            else:
                for script in found:
                    key = (script.name, script.command)
                    if key not in seen_scripts:
                        seen_scripts.add(key)
                        scripts.append(script)
            continue

        if lower in _TEST_CONFIG_NAMES:
            test_configs.add(rel)
            if lower.startswith("playwright.config") and base_url is None:
                text = read_text_capped(path)
                if text is None:
                    unreadable += 1
                else:
                    match = _BASE_URL_RE.search(text)
                    if match is not None:
                        base_url = match.group(1)
            continue

        if lower in ("pyproject.toml", "setup.cfg"):
            text = read_text_capped(path)
            if text is None:
                unreadable += 1
            elif (lower == "pyproject.toml" and "[tool.pytest.ini_options]" in text) or (
                lower == "setup.cfg" and "[tool:pytest]" in text
            ):
                test_configs.add(rel)
            continue

        if is_test_file(name, parent):
            test_file_count += 1
            patterns.add(_pattern_for(name, parent))
            text = read_text_capped(path)
            if text is None:
                unreadable += 1
            else:
                fw = _framework_of(text)
                for api, pattern in _LOCATOR_RES.items():
                    hits = len(pattern.findall(text))
                    if hits:
                        locator_counts[(api, fw)] = locator_counts.get((api, fw), 0) + hits
            continue

        if lower in _FIXTURE_NAMES or ("fixture" in lower and lower.endswith(_HELPER_EXT)):
            fixtures.add(rel)
            continue

        if lower.endswith(_HELPER_EXT) and _in_test_tree(rel_dir, test_dirs):
            text = read_text_capped(path)
            if text is None:
                unreadable += 1
            elif _TEST_EXTEND_RE.search(text) is not None:
                fixtures.add(rel)
            elif _is_page_object(name, parent, text or ""):
                page_objects.add(rel)
            else:
                helpers.add(rel)
            continue

        if lower.endswith(_APP_SRC_EXT):
            text = read_text_capped(path)
            if text is not None:
                test_ids.update(_DATA_TESTID_RE.findall(text))

    styles = [
        LocatorStyle(api=api, framework=framework, count=count)
        for (api, framework), count in locator_counts.items()
    ]
    styles.sort(key=lambda style: (-style.count, style.api, style.framework))
    scripts.sort(key=lambda script: script.name)

    notes: list[str] = []
    if not prof.test_frameworks:
        notes.append("no test framework detected in manifests/configs")
    if test_file_count == 0:
        notes.append("no test files found (conventions limited to scripts/configs)")
    elif not styles:
        notes.append("no UI locators found in test files (API-level tests, or no UI tests)")
    if capped:
        notes.append(f"file count capped at {MAX_FILES}")
    if unreadable:
        notes.append(f"{unreadable} file(s) exceeded read cap or unreadable (skipped)")

    return TestConventions(
        test_file_patterns=sorted(patterns),
        locator_styles=styles,
        page_object_files=sorted(page_objects),
        fixture_files=sorted(fixtures),
        helper_files=sorted(helpers),
        test_configs=sorted(test_configs),
        test_ids=sorted(test_ids),
        base_url=base_url,
        test_scripts=scripts,
        notes=notes,
        scanned_at=datetime.now(UTC),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qa_copilot_repository.conventions",
        description=(
            "Extract a repository's test conventions: locators, page objects, "
            "fixtures, helpers (S2.2)."
        ),
    )
    parser.add_argument("root", help="path to the repository root")
    args = parser.parse_args(argv)
    print(extract_conventions(args.root).model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
