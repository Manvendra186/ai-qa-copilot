"""Repository scanner tests (S2.1, build bible §19).

Exit criterion: the scanner is correct on 3 sample repositories. The golden
sample set lives in ``packages/repository/samples/sample_repos`` (same
version-controlled golden-set precedent as S1.4's ``packages/ai/golden``):

- ``js-web-app``  — React + Vite + TypeScript, vitest unit tests + Playwright e2e
- ``python-api``  — FastAPI + uv, pytest (``tests/unit`` + ``tests/integration``)
- ``js-monorepo`` — pnpm workspaces (React client + Express server), NO test framework
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qa_copilot_domain import RepositoryProfile
from qa_copilot_repository import scan_repository

SAMPLES = (
    Path(__file__).resolve().parents[2] / "packages" / "repository" / "samples" / "sample_repos"
)
SAMPLE_NAMES = ("js-web-app", "python-api", "js-monorepo")


def _dump(profile: RepositoryProfile) -> dict[str, object]:
    data = profile.model_dump(mode="json")
    data.pop("scanned_at", None)
    return data


# --- golden sample 1: JS web app with full test stack ------------------------


def test_js_web_app_profile() -> None:
    profile = scan_repository(SAMPLES / "js-web-app")
    # languages are ordered by descending file count, then name (deterministic)
    assert profile.languages == ["typescript", "html", "javascript"]
    assert profile.frameworks == ["react", "tailwind", "vite"]
    assert profile.test_frameworks == ["playwright", "vitest"]
    assert profile.test_dirs == ["e2e", "src/__tests__"]
    assert profile.test_file_count == 2
    assert profile.package_managers == ["pnpm"]
    assert profile.monorepo is False
    assert profile.file_count == 12
    assert profile.scanned_at is not None
    assert any("playwright testDir: e2e" in note for note in profile.notes)


# --- golden sample 2: Python API with pytest ---------------------------------


def test_python_api_profile() -> None:
    profile = scan_repository(SAMPLES / "python-api")
    assert profile.languages == ["python"]
    assert profile.frameworks == ["fastapi"]
    assert profile.test_frameworks == ["pytest"]
    assert profile.test_dirs == ["tests/integration", "tests/unit"]
    assert profile.test_file_count == 2  # conftest.py is not a test file
    assert profile.package_managers == ["uv"]
    assert profile.monorepo is False
    assert profile.file_count == 8
    assert profile.notes == []


# --- golden sample 3: JS monorepo without a test framework -------------------


def test_js_monorepo_profile() -> None:
    profile = scan_repository(SAMPLES / "js-monorepo")
    assert profile.languages == ["javascript", "html"]
    assert profile.frameworks == ["express", "react", "vite"]
    assert profile.test_frameworks == []
    assert profile.test_dirs == []
    assert profile.test_file_count == 0
    assert profile.package_managers == ["pnpm"]
    assert profile.monorepo is True
    assert profile.file_count == 11
    assert any("pnpm workspaces: client, server" in note for note in profile.notes)
    assert any("no test framework detected" in note for note in profile.notes)


# --- invariants ---------------------------------------------------------------


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_profiles_are_deterministic(name: str) -> None:
    first = _dump(scan_repository(SAMPLES / name))
    second = _dump(scan_repository(SAMPLES / name))
    assert first == second


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_lists_are_sorted_and_unique(name: str) -> None:
    profile = scan_repository(SAMPLES / name)
    for field_name in ("frameworks", "test_frameworks", "test_dirs", "package_managers"):
        values = getattr(profile, field_name)
        assert values == sorted(set(values)), field_name


def test_scan_accepts_str_root() -> None:
    profile = scan_repository(str(SAMPLES / "js-web-app"))
    assert profile.frameworks == ["react", "tailwind", "vite"]


# --- edge cases ---------------------------------------------------------------


def test_missing_root_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        scan_repository(tmp_path / "nope")


def test_non_directory_root_raises_value_error(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        scan_repository(target)


def test_empty_repo_yields_empty_profile(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# empty\n", encoding="utf-8")
    profile = scan_repository(tmp_path)
    assert isinstance(profile, RepositoryProfile)
    assert profile.languages == []
    assert profile.frameworks == []
    assert profile.test_frameworks == []
    assert profile.test_dirs == []
    assert profile.test_file_count == 0
    assert profile.package_managers == []
    assert profile.monorepo is False
    assert profile.file_count == 1
    assert any("no test framework detected" in note for note in profile.notes)


def test_node_modules_and_vcs_are_pruned(tmp_path: Path) -> None:
    (tmp_path / "node_modules" / "junk").mkdir(parents=True)
    (tmp_path / "node_modules" / "junk" / "fake.test.js").write_text("x", encoding="utf-8")
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "bogus_test.py").write_text("x", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real.test.js").write_text("x", encoding="utf-8")
    profile = scan_repository(tmp_path)
    assert profile.file_count == 1
    assert profile.languages == ["javascript"]
    assert profile.test_file_count == 1
    assert profile.test_dirs == ["src"]


def test_pnpm_workspace_ignores_other_keys(tmp_path: Path) -> None:
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "# pnpm workspace\n"
        "packages:\n"
        '  - "client"\n'
        '  - "server"\n'
        "onlyBuiltDependencies:\n"
        "  - esbuild\n",
        encoding="utf-8",
    )
    profile = scan_repository(tmp_path)
    assert profile.monorepo is True
    assert any("pnpm workspaces: client, server" in note for note in profile.notes)
    assert not any("esbuild" in note for note in profile.notes)


def test_go_and_pytest_conventions(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "main_test.go").write_text("package main\n", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "build.py").write_text("print('build')\n", encoding="utf-8")
    (tmp_path / "tools" / "test_build.py").write_text(
        "def test_ok() -> None:\n    assert True\n", encoding="utf-8"
    )
    profile = scan_repository(tmp_path)
    assert profile.languages == ["go", "python"]
    assert profile.test_frameworks == []
    assert profile.test_file_count == 2
    assert profile.test_dirs == [".", "tools"]
