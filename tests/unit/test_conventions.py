"""Convention extractor tests (S2.2, build bible §19).

Exit criterion: the extractor produces the deterministic
:class:`qa_copilot_domain.TestConventions` contract on the two golden
repositories:

- ``packages/repository/samples/sample_repos/js-web-app`` —
  Playwright e2e + Vitest unit + testing-library (S2.1 golden sample);
- ``c:\\Users\\manve\\Workspace\\ai-qa-copilot-demo-app`` —
  Express + React demo app under test: no test files, so conventions fall
  back to scripts/configs only (skipped when not present on this machine).

Synthetic repos under ``tmp_path`` pin the heuristics: page objects,
fixtures, helpers, pytest conventions, the ``data-testid`` vocabulary,
``package.json`` script filtering, and edge cases.

Goldens compare every field of ``TestConventions`` except the wall-clock
``scanned_at``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qa_copilot_domain import TestConventions, TestScript
from qa_copilot_repository import extract_conventions

SAMPLE_JS_WEB_APP = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "repository"
    / "samples"
    / "sample_repos"
    / "js-web-app"
)
DEMO_APP = Path(r"c:\Users\manve\Workspace\ai-qa-copilot-demo-app")


def _dump(conventions: TestConventions) -> dict[str, object]:
    """Golden-comparable dict: every field except the wall-clock ``scanned_at``."""
    data = conventions.model_dump(mode="json")
    data.pop("scanned_at", None)
    return data


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- golden 1: js-web-app sample (Playwright + Vitest + testing-library) ------


def test_golden_js_web_app() -> None:
    assert _dump(extract_conventions(SAMPLE_JS_WEB_APP)) == {
        "test_file_patterns": ["*.spec.ts", "*.test.tsx"],
        "locator_styles": [
            {"api": "getByRole", "framework": "playwright", "count": 1},
            {"api": "getByRole", "framework": "testing-library", "count": 1},
        ],
        "page_object_files": [],
        "fixture_files": [],
        "helper_files": [],
        "test_configs": ["playwright.config.ts"],
        "test_ids": [],
        "base_url": "http://localhost:5173",
        "test_scripts": [
            {"name": "e2e", "command": "playwright test"},
            {"name": "test", "command": "vitest run"},
        ],
        "notes": [],
    }


# --- golden 2: demo app (Playwright E2E, added by S3.1) -------------------------


@pytest.mark.skipif(not DEMO_APP.is_dir(), reason="demo app not present on this machine")
def test_golden_demo_app() -> None:
    assert _dump(extract_conventions(DEMO_APP)) == {
        "test_file_patterns": ["*.spec.js"],
        "locator_styles": [
            {"api": "getByTestId", "framework": "generic", "count": 3},
            {"api": "getByRole", "framework": "generic", "count": 2},
            {"api": "locator", "framework": "generic", "count": 2},
        ],
        "page_object_files": [],
        "fixture_files": ["e2e/fixtures.js"],
        "helper_files": [],
        "test_configs": ["playwright.config.js"],
        "test_ids": [],
        "base_url": None,
        "test_scripts": [
            {"name": "smoke", "command": "node scripts/smoke.mjs"},
            {"name": "test:e2e", "command": "playwright test"},
            {"name": "test:e2e:headed", "command": "playwright test --headed"},
        ],
        "notes": [],
    }


# --- golden determinism --------------------------------------------------------


def test_golden_repos_are_deterministic() -> None:
    for root in (SAMPLE_JS_WEB_APP, DEMO_APP):
        if not root.is_dir():
            continue
        assert _dump(extract_conventions(root)) == _dump(extract_conventions(root))


def test_extract_accepts_str_root() -> None:
    conventions = extract_conventions(str(SAMPLE_JS_WEB_APP))
    assert conventions.base_url == "http://localhost:5173"


def test_reuses_scanner_profile_when_given() -> None:
    from qa_copilot_repository import scan_repository

    profile = scan_repository(SAMPLE_JS_WEB_APP)
    assert _dump(extract_conventions(SAMPLE_JS_WEB_APP, profile)) == _dump(
        extract_conventions(SAMPLE_JS_WEB_APP)
    )


# --- synthetic: Playwright repo (page objects, fixtures, helpers) --------------


def test_playwright_page_objects_fixtures_helpers(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        '{"name": "app", "devDependencies": {"@playwright/test": "1.48.0"},'
        ' "scripts": {"e2e": "playwright test"}}\n',
    )
    _write(
        tmp_path / "playwright.config.ts",
        'export default { use: { baseURL: "https://staging.example.com" } };\n',
    )
    _write(
        tmp_path / "e2e" / "login.spec.ts",
        'import { test } from "@playwright/test";\n'
        'test("login", async ({ page }) => {\n'
        '  await page.getByRole("button", { name: "Sign in" }).click();\n'
        "});\n",
    )
    _write(
        tmp_path / "e2e" / "pages" / "LoginPage.ts",
        "export class LoginPage {\n"
        "  constructor(private page: Page) {}\n"
        '  signIn = () => page.getByRole("button");\n'
        "}\n",
    )
    _write(
        tmp_path / "e2e" / "fixtures.ts",
        'import { test } from "@playwright/test";\n'
        "export const fixtures = test.extend({} as never);\n",
    )
    _write(
        tmp_path / "e2e" / "helpers.ts",
        "export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));\n",
    )
    _write(
        tmp_path / "src" / "App.tsx",
        'export const App = () => <main data-testid="app-root" />;\n',
    )

    conventions = extract_conventions(tmp_path)
    assert conventions.page_object_files == ["e2e/pages/LoginPage.ts"]
    assert conventions.fixture_files == ["e2e/fixtures.ts"]
    assert conventions.helper_files == ["e2e/helpers.ts"]
    assert conventions.test_configs == ["playwright.config.ts"]
    assert conventions.base_url == "https://staging.example.com"
    assert conventions.test_ids == ["app-root"]
    assert conventions.test_file_patterns == ["*.spec.ts"]
    assert ("getByRole", "playwright") in {
        (style.api, style.framework) for style in conventions.locator_styles
    }
    assert conventions.test_scripts == [TestScript(name="e2e", command="playwright test")]
    assert conventions.notes == []


def test_test_extend_file_is_a_fixture(tmp_path: Path) -> None:
    _write(
        tmp_path / "e2e" / "login.spec.ts",
        'import { test } from "@playwright/test";\n'
        'test("login", async ({ auth }) => { await auth; });\n',
    )
    _write(
        tmp_path / "e2e" / "auth.ts",
        'import { test as base } from "@playwright/test";\n'
        'export const test = base.extend({ auth: async ({}, use) => use({ token: "t" }); });\n',
    )
    conventions = extract_conventions(tmp_path)
    assert conventions.fixture_files == ["e2e/auth.ts"]
    assert conventions.page_object_files == []
    assert conventions.helper_files == []


def test_locator_density_marks_page_object(tmp_path: Path) -> None:
    _write(
        tmp_path / "e2e" / "cart.spec.ts",
        'import { test } from "@playwright/test";\n'
        'test("cart", async ({ page }) => {\n'
        '  await page.getByText("Add").click();\n'
        "});\n",
    )
    _write(
        tmp_path / "e2e" / "flows.ts",
        "export const addItems = async (page: Page) => {\n"
        '  await page.getByRole("button").click();\n'
        '  await page.getByText("Add").click();\n'
        "};\n",
    )
    _write(tmp_path / "e2e" / "util.ts", "export const norm = (s: string) => s.trim();\n")
    conventions = extract_conventions(tmp_path)
    assert conventions.page_object_files == ["e2e/flows.ts"]
    assert conventions.helper_files == ["e2e/util.ts"]


def test_locator_styles_sorted_by_count_then_api(tmp_path: Path) -> None:
    _write(
        tmp_path / "test" / "app.spec.js",
        "test('a', () => {\n"
        '  screen.getByRole("button").click();\n'
        '  screen.getByRole("link").click();\n'
        '  screen.getByText("Save");\n'
        "});\n",
    )
    conventions = extract_conventions(tmp_path)
    assert [(style.api, style.framework, style.count) for style in conventions.locator_styles] == [
        ("getByRole", "generic", 2),
        ("getByText", "generic", 1),
    ]


# --- synthetic: pytest repo, testid vocabulary, script filtering --------------


def test_pytest_repo_conventions(tmp_path: Path) -> None:
    _write(
        tmp_path / "conftest.py",
        "import pytest\n\n@pytest.fixture\ndef client() -> object:\n    return object()\n",
    )
    _write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "api"\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    )
    _write(
        tmp_path / "tests" / "unit" / "test_math.py",
        "def test_add() -> None:\n    assert 1 + 1 == 2\n",
    )
    _write(
        tmp_path / "tests" / "integration" / "test_api.py",
        "def test_health(client: object) -> None:\n    assert True\n",
    )
    _write(
        tmp_path / "tests" / "helpers.py",
        'def make_payload() -> dict[str, str]:\n    return {"id": "1"}\n',
    )
    conventions = extract_conventions(tmp_path)
    assert conventions.fixture_files == ["conftest.py"]
    assert conventions.helper_files == ["tests/helpers.py"]
    assert conventions.test_configs == ["pyproject.toml"]
    assert conventions.test_file_patterns == ["test_*.py"]
    assert conventions.locator_styles == []
    assert conventions.notes == [
        "no UI locators found in test files (API-level tests, or no UI tests)"
    ]


def test_data_testid_vocabulary_from_app_source(tmp_path: Path) -> None:
    _write(
        tmp_path / "e2e" / "app.spec.ts",
        'import { test } from "@playwright/test";\ntest("x", async ({ page }) => {});\n',
    )
    _write(
        tmp_path / "src" / "App.tsx",
        '<div data-testid="app-root">\n  <nav data-testid="nav-main" />\n</div>\n',
    )
    _write(
        tmp_path / "src" / "Card.jsx",
        'import { TEST_ID } from "./testids";\n\n'
        "export const Card = () => <div data-testid={TEST_ID.card} />;\n",
    )
    _write(tmp_path / "index.html", '<html><body data-testid="page"></body></html>\n')
    conventions = extract_conventions(tmp_path)
    assert conventions.test_ids == ["app-root", "nav-main", "page"]


def test_package_json_script_filtering(tmp_path: Path) -> None:
    _write(
        tmp_path / "package.json",
        "{\n"
        '  "name": "app",\n'
        '  "scripts": {\n'
        '    "dev": "vite",\n'
        '    "build": "tsc && vite build",\n'
        '    "lint": "eslint .",\n'
        '    "typecheck": "tsc --noEmit",\n'
        '    "test": "vitest run",\n'
        '    "e2e": "playwright test",\n'
        '    "coverage": "pytest --cov=app",\n'
        '    "serve": "npm run dev -- --host"\n'
        "  }\n"
        "}\n",
    )
    conventions = extract_conventions(tmp_path)
    assert [script.name for script in conventions.test_scripts] == ["coverage", "e2e", "test"]


# --- edge cases -----------------------------------------------------------------


def test_missing_root_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        extract_conventions(tmp_path / "nope")


def test_non_directory_root_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        extract_conventions(tmp_path / "file.txt")


def test_empty_repo_yields_empty_conventions(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# empty\n", encoding="utf-8")
    conventions = extract_conventions(tmp_path)
    assert isinstance(conventions, TestConventions)
    assert conventions.test_file_patterns == []
    assert conventions.locator_styles == []
    assert conventions.page_object_files == []
    assert conventions.fixture_files == []
    assert conventions.helper_files == []
    assert conventions.test_configs == []
    assert conventions.test_ids == []
    assert conventions.base_url is None
    assert conventions.test_scripts == []
    assert conventions.notes == [
        "no test framework detected in manifests/configs",
        "no test files found (conventions limited to scripts/configs)",
    ]


def test_node_modules_are_pruned(tmp_path: Path) -> None:
    _write(tmp_path / "node_modules" / "junk" / "fake.test.js", "test('x', () => {});\n")
    _write(tmp_path / "src" / "real.test.js", "test('x', () => {});\n")
    conventions = extract_conventions(tmp_path)
    assert conventions.test_file_patterns == ["*.test.js"]
    assert conventions.locator_styles == []
    assert conventions.test_ids == []
    assert conventions.helper_files == []


def test_invalid_package_json_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")
    conventions = extract_conventions(tmp_path)
    assert conventions.test_scripts == []
    assert conventions.base_url is None


def test_monorepo_scripts_dedupe_identical_pairs(tmp_path: Path) -> None:
    _write(
        tmp_path / "client" / "package.json",
        '{"name": "client", "scripts": {"test": "vitest run"}}\n',
    )
    _write(
        tmp_path / "server" / "package.json",
        '{"name": "server", "scripts": {"test": "vitest run", "e2e": "playwright test"}}\n',
    )
    conventions = extract_conventions(tmp_path)
    assert [script.name for script in conventions.test_scripts] == ["e2e", "test"]
    assert sum(1 for script in conventions.test_scripts if script.name == "test") == 1
