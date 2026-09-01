"""Change-impact core tests (S6.1, build bible §19).

Exit criterion: the LLM-free ``compute_impact`` core produces the
deterministic :class:`qa_copilot_domain.ImpactSet` contract on the golden
repositories:

- ``packages/repository/samples/sample_repos/js-web-app`` — a source
  change is *referenced* by the Vitest spec; a test change is *direct*;
- ``packages/repository/samples/sample_repos/python-api`` — a test change
  is *direct*; a source change references nothing (the sample tests
  import nothing);
- ``c:\\Users\\manve\\Workspace\\ai-qa-copilot-demo-app`` — the e2e
  fixture change is *referenced* by the Playwright spec, a page change is
  not (skipped when not present on this machine).

Synthetic repos under ``tmp_path`` pin the heuristics: extensionless JS
imports, ``index.js`` resolution, Python package imports
(``__init__.py``), the ``data-testid`` vocabulary, missing changed
files, no-test-file repos, path validation, and determinism. The
ORM-backed helpers (``applied_generated_refs`` / ``impact_from_session``)
run against a fake session; the git-range helper and the CLI finish the
coverage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
import qa_copilot_repository
from qa_copilot_domain import ImpactSet
from qa_copilot_repository import (
    GeneratedTestRef,
    applied_generated_refs,
    changed_files_from_range,
    compute_impact,
    impact_from_session,
    main,
    normalize_changed,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SAMPLE_JS_WEB_APP = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "repository"
    / "samples"
    / "sample_repos"
    / "js-web-app"
)
SAMPLE_PYTHON_API = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "repository"
    / "samples"
    / "sample_repos"
    / "python-api"
)
DEMO_APP = Path(r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app")

needs_demo = pytest.mark.skipif(
    not DEMO_APP.is_dir(), reason="demo app not present on this machine"
)
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _dump(result: ImpactSet) -> dict[str, object]:
    """Golden-comparable dict: every field except the wall-clock ``computed_at``."""
    data = result.model_dump(mode="json")
    data.pop("computed_at", None)
    return data


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- normalize_changed ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/components/Counter.tsx", "src/components/Counter.tsx"),
        ("./src/components/Counter.tsx", "src/components/Counter.tsx"),
        (r"src\components\Counter.tsx", "src/components/Counter.tsx"),
        ("  tests/unit/test_users.py  ", "tests/unit/test_users.py"),
    ],
)
def test_normalize_changed_accepts(raw: str, expected: str) -> None:
    assert normalize_changed(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["/abs/a.py", "C:/Users/x/a.py", r"C:\Users\x\a.py", "src/../a.py", ".."],
)
def test_normalize_changed_rejects_escapes_and_absolute(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_changed(raw)


@pytest.mark.parametrize("raw", ["", "   "])
def test_normalize_changed_rejects_empty(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_changed(raw)


# --- golden 1: js-web-app sample (Vitest + Playwright) --------------------------


def test_golden_js_web_app_source_change_referenced() -> None:
    assert _dump(compute_impact(SAMPLE_JS_WEB_APP, ["src/components/Counter.tsx"])) == {
        "changed": ["src/components/Counter.tsx"],
        "impacted": [
            {
                "path": "src/__tests__/Counter.test.tsx",
                "kinds": ["referenced"],
                "changed_files": ["src/components/Counter.tsx"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["imports src/components/Counter.tsx"],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


def test_golden_js_web_app_test_change_direct() -> None:
    assert _dump(compute_impact(SAMPLE_JS_WEB_APP, ["src/__tests__/Counter.test.tsx"])) == {
        "changed": ["src/__tests__/Counter.test.tsx"],
        "impacted": [
            {
                "path": "src/__tests__/Counter.test.tsx",
                "kinds": ["direct"],
                "changed_files": ["src/__tests__/Counter.test.tsx"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["changed test file"],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


def test_golden_js_web_app_generated_with_test_case() -> None:
    result = compute_impact(
        SAMPLE_JS_WEB_APP,
        ["src/__tests__/Counter.test.tsx"],
        generated=[
            GeneratedTestRef(
                file_path="src/__tests__/Counter.test.tsx",
                test_case_id="tc-1",
                requirement_ids=("req-2", "req-1"),
            )
        ],
    )
    assert _dump(result) == {
        "changed": ["src/__tests__/Counter.test.tsx"],
        "impacted": [
            {
                "path": "src/__tests__/Counter.test.tsx",
                "kinds": ["direct", "generated"],
                "changed_files": ["src/__tests__/Counter.test.tsx"],
                "test_case_ids": ["tc-1"],
                "requirement_ids": ["req-1", "req-2"],
                "signals": [
                    "changed test file",
                    "generated test applied (test case tc-1)",
                ],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


def test_golden_js_web_app_generated_orphan() -> None:
    result = compute_impact(
        SAMPLE_JS_WEB_APP,
        ["src/__tests__/Counter.test.tsx"],
        generated=[GeneratedTestRef(file_path="src/__tests__/Counter.test.tsx")],
    )
    assert _dump(result) == {
        "changed": ["src/__tests__/Counter.test.tsx"],
        "impacted": [
            {
                "path": "src/__tests__/Counter.test.tsx",
                "kinds": ["direct", "generated"],
                "changed_files": ["src/__tests__/Counter.test.tsx"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": [
                    "changed test file",
                    "generated test applied (no test-case link)",
                ],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


def test_golden_js_web_app_direct_generated_referenced_combined() -> None:
    result = compute_impact(
        SAMPLE_JS_WEB_APP,
        ["src/components/Counter.tsx", "src/__tests__/Counter.test.tsx"],
        generated=[
            GeneratedTestRef(
                file_path="src/__tests__/Counter.test.tsx",
                test_case_id="tc-1",
                requirement_ids=("req-1", "req-2"),
            )
        ],
    )
    assert _dump(result) == {
        "changed": ["src/__tests__/Counter.test.tsx", "src/components/Counter.tsx"],
        "impacted": [
            {
                "path": "src/__tests__/Counter.test.tsx",
                "kinds": ["direct", "generated", "referenced"],
                "changed_files": [
                    "src/__tests__/Counter.test.tsx",
                    "src/components/Counter.tsx",
                ],
                "test_case_ids": ["tc-1"],
                "requirement_ids": ["req-1", "req-2"],
                "signals": [
                    "changed test file",
                    "generated test applied (test case tc-1)",
                    "imports src/components/Counter.tsx",
                ],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


# --- golden 2: python-api sample (pytest) --------------------------------------


def test_golden_python_api_test_change_direct() -> None:
    assert _dump(compute_impact(SAMPLE_PYTHON_API, ["tests/unit/test_users.py"])) == {
        "changed": ["tests/unit/test_users.py"],
        "impacted": [
            {
                "path": "tests/unit/test_users.py",
                "kinds": ["direct"],
                "changed_files": ["tests/unit/test_users.py"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["changed test file"],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


def test_golden_python_api_source_change_not_referenced() -> None:
    # The sample tests import nothing — a source change must not be
    # guessed at; it simply has no static references from tests.
    assert _dump(compute_impact(SAMPLE_PYTHON_API, ["src/app/main.py"])) == {
        "changed": ["src/app/main.py"],
        "impacted": [],
        "test_files_scanned": 2,
        "notes": [],
    }


# --- golden 3: demo app (Express + React + Playwright) --------------------------


@needs_demo
def test_golden_demo_app_fixture_change_referenced() -> None:
    assert _dump(compute_impact(DEMO_APP, ["e2e/fixtures.js"])) == {
        "changed": ["e2e/fixtures.js"],
        "impacted": [
            {
                "path": "e2e/demo.spec.js",
                "kinds": ["referenced"],
                "changed_files": ["e2e/fixtures.js"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["imports e2e/fixtures.js"],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


@needs_demo
def test_golden_demo_app_test_change_direct() -> None:
    assert _dump(compute_impact(DEMO_APP, ["e2e/demo.spec.js"])) == {
        "changed": ["e2e/demo.spec.js"],
        "impacted": [
            {
                "path": "e2e/demo.spec.js",
                "kinds": ["direct"],
                "changed_files": ["e2e/demo.spec.js"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["changed test file"],
            }
        ],
        "test_files_scanned": 2,
        "notes": [],
    }


@needs_demo
def test_golden_demo_app_page_change_not_referenced() -> None:
    # A React page is exercised through the UI, not imported by tests —
    # S6.1 reports no static reference (that is by design, LLM-free).
    assert _dump(compute_impact(DEMO_APP, ["client/src/pages/Cart.jsx"])) == {
        "changed": ["client/src/pages/Cart.jsx"],
        "impacted": [],
        "test_files_scanned": 2,
        "notes": [],
    }


# --- synthetic repos (tmp_path) --------------------------------------------------


def test_synthetic_js_extensionless_and_index_resolution(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "lib" / "counter.js", "export function bump() { return 1; }\n")
    _write(tmp_path / "src" / "lib" / "index.js", "export function reset() {}\n")
    _write(
        tmp_path / "tests" / "counter.test.js",
        'import { bump } from "../src/lib/counter";\nimport { reset } from "../src/lib";\n',
    )
    assert _dump(compute_impact(tmp_path, ["src/lib/counter.js", "src/lib/index.js"])) == {
        "changed": ["src/lib/counter.js", "src/lib/index.js"],
        "impacted": [
            {
                "path": "tests/counter.test.js",
                "kinds": ["referenced"],
                "changed_files": ["src/lib/counter.js", "src/lib/index.js"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["imports src/lib/counter.js", "imports src/lib/index.js"],
            }
        ],
        "test_files_scanned": 1,
        "notes": [],
    }


def test_synthetic_python_package_imports(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "pkg" / "__init__.py", "helper = 1\n")
    _write(tmp_path / "src" / "pkg" / "core.py", "def run() -> int: return 2\n")
    _write(
        tmp_path / "tests" / "test_pkg.py",
        "from src.pkg import helper\nfrom src.pkg.core import run\n",
    )
    assert _dump(compute_impact(tmp_path, ["src/pkg/__init__.py", "src/pkg/core.py"])) == {
        "changed": ["src/pkg/__init__.py", "src/pkg/core.py"],
        "impacted": [
            {
                "path": "tests/test_pkg.py",
                "kinds": ["referenced"],
                "changed_files": ["src/pkg/__init__.py", "src/pkg/core.py"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["imports src/pkg/__init__.py", "imports src/pkg/core.py"],
            }
        ],
        "test_files_scanned": 1,
        "notes": [],
    }


def test_synthetic_data_testid_reference(tmp_path: Path) -> None:
    _write(
        tmp_path / "src" / "Button.jsx",
        '<button type="button" data-testid="primary-action">Go</button>\n',
    )
    _write(
        tmp_path / "tests" / "button.test.js",
        'import { screen } from "testing-library";\nscreen.getByTestId("primary-action");\n',
    )
    assert _dump(compute_impact(tmp_path, ["src/Button.jsx"])) == {
        "changed": ["src/Button.jsx"],
        "impacted": [
            {
                "path": "tests/button.test.js",
                "kinds": ["referenced"],
                "changed_files": ["src/Button.jsx"],
                "test_case_ids": [],
                "requirement_ids": [],
                "signals": ["uses data-testid 'primary-action' from src/Button.jsx"],
            }
        ],
        "test_files_scanned": 1,
        "notes": [],
    }


def test_synthetic_changed_file_missing_noted(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_a.py", "def test_a() -> None:\n    assert True\n")
    assert _dump(compute_impact(tmp_path, ["src/gone.ts"])) == {
        "changed": ["src/gone.ts"],
        "impacted": [],
        "test_files_scanned": 1,
        "notes": ["changed file not present at repo root (deleted or moved): src/gone.ts"],
    }


def test_synthetic_no_test_files_noted(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "app.py", "VALUE = 1\n")
    assert _dump(compute_impact(tmp_path, ["src/app.py"])) == {
        "changed": ["src/app.py"],
        "impacted": [],
        "test_files_scanned": 0,
        "notes": ["no test files detected in the repository (S2.1 heuristics)"],
    }


def test_synthetic_dedup_and_determinism() -> None:
    once = compute_impact(SAMPLE_JS_WEB_APP, ["src/components/Counter.tsx"])
    twice = compute_impact(
        SAMPLE_JS_WEB_APP,
        ["./src/components/Counter.tsx", "src/components/Counter.tsx"],
    )
    assert _dump(once) == _dump(twice)


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a directory"):
        compute_impact(tmp_path / "missing", ["a.py"])


def test_generated_ref_path_validated(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_a.py", "def test_a() -> None:\n    assert True\n")
    with pytest.raises(ValueError, match="escapes the repository"):
        compute_impact(tmp_path, ["src/a.py"], generated=[GeneratedTestRef(file_path="../evil.py")])


# --- ORM-backed helpers (fake session) ------------------------------------------


class _FakeScalars:
    """Stands in for ``sqlalchemy.scalars``: only ``.all()`` is used."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Stands in for ``sqlalchemy.orm.Session``: only ``.scalars()`` is used."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self, statement: object) -> _FakeScalars:
        return _FakeScalars(self._rows)


def test_applied_generated_refs_maps_rows() -> None:
    # Deliberately unsorted input: the helper must sort by file path.
    rows = [
        SimpleNamespace(file_path="tests/unit/test_orphan.py", test_case_id=None, test_case=None),
        SimpleNamespace(
            file_path="tests/unit/test_gen.py",
            test_case_id="tc-1",
            test_case=SimpleNamespace(
                requirements=[SimpleNamespace(id="req-2"), SimpleNamespace(id="req-1")],
            ),
        ),
    ]
    session = cast("Session", _FakeSession(rows))
    assert applied_generated_refs(session, "proj-1") == [
        GeneratedTestRef(
            file_path="tests/unit/test_gen.py",
            test_case_id="tc-1",
            requirement_ids=("req-1", "req-2"),
        ),
        GeneratedTestRef(file_path="tests/unit/test_orphan.py"),
    ]


def test_applied_generated_refs_empty() -> None:
    session = cast("Session", _FakeSession([]))
    assert applied_generated_refs(session, "proj-1") == []


def test_impact_from_session(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests" / "unit" / "test_gen.py", "def test_gen() -> None:\n    assert True\n"
    )
    rows = [
        SimpleNamespace(
            file_path="tests/unit/test_gen.py",
            test_case_id="tc-9",
            test_case=SimpleNamespace(requirements=[SimpleNamespace(id="req-9")]),
        ),
    ]
    session = cast("Session", _FakeSession(rows))
    result = impact_from_session(session, "proj-1", tmp_path, ["tests/unit/test_gen.py"])
    assert _dump(result) == {
        "changed": ["tests/unit/test_gen.py"],
        "impacted": [
            {
                "path": "tests/unit/test_gen.py",
                "kinds": ["direct", "generated"],
                "changed_files": ["tests/unit/test_gen.py"],
                "test_case_ids": ["tc-9"],
                "requirement_ids": ["req-9"],
                "signals": [
                    "changed test file",
                    "generated test applied (test case tc-9)",
                ],
            }
        ],
        "test_files_scanned": 1,
        "notes": [],
    }


# --- git range -------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_changed_files_from_range_requires_both_refs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="both refs non-empty"):
        changed_files_from_range(tmp_path, "", "HEAD")
    with pytest.raises(ValueError, match="both refs non-empty"):
        changed_files_from_range(tmp_path, "HEAD~1", "   ")


def test_changed_files_from_range_failure_not_a_repo(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "A = 1\n")
    with pytest.raises(ValueError, match="git diff BASE..HEAD failed"):
        changed_files_from_range(tmp_path, "HEAD~1", "HEAD")


@needs_git
def test_changed_files_from_range_lists_diff(tmp_path: Path) -> None:
    _git(["init"], tmp_path)
    _git(["config", "user.name", "test"], tmp_path)
    _git(["config", "user.email", "test@example.com"], tmp_path)
    _write(tmp_path / "a.py", "A = 1\n")
    _git(["add", "a.py"], tmp_path)
    _git(["commit", "-m", "one"], tmp_path)
    _write(tmp_path / "b.py", "B = 2\n")
    _git(["add", "b.py"], tmp_path)
    _git(["commit", "-m", "two"], tmp_path)
    assert changed_files_from_range(tmp_path, "HEAD~1", "HEAD") == ["b.py"]


# --- CLI --------------------------------------------------------------------------


def test_cli_changed_outputs_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(SAMPLE_JS_WEB_APP), "--changed", "src/components/Counter.tsx"])
    out = capsys.readouterr()
    assert rc == 0
    payload = json.loads(out.out)
    assert payload["changed"] == ["src/components/Counter.tsx"]
    assert payload["impacted"][0]["path"] == "src/__tests__/Counter.test.tsx"
    assert payload["impacted"][0]["kinds"] == ["referenced"]


def test_cli_empty_changed(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(SAMPLE_JS_WEB_APP), "--changed", " , "])
    err = capsys.readouterr()
    assert rc == 2
    assert "impact: --changed needs at least one path" in err.err


def test_cli_bad_range(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(SAMPLE_JS_WEB_APP), "--range", "main"])
    err = capsys.readouterr()
    assert rc == 2
    assert "impact: --range must be BASE..HEAD" in err.err


def test_cli_requires_a_source() -> None:
    with pytest.raises(SystemExit):
        main([str(SAMPLE_JS_WEB_APP)])


# --- package exports ----------------------------------------------------------------


def test_package_exports_impact_api() -> None:
    for name in (
        "GeneratedTestRef",
        "applied_generated_refs",
        "changed_files_from_range",
        "compute_impact",
        "impact_from_session",
        "main",
        "normalize_changed",
    ):
        assert name in qa_copilot_repository.__all__
        assert getattr(qa_copilot_repository, name) is not None
    assert qa_copilot_repository.impact is not None
