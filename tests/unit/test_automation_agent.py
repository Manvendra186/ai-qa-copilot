"""S2.3 — Automation Agent + local lint/type gate (offline: no real LLM).

Covers the S2.3 exit criteria (build bible §19 S2.3 — "generated code
passes lint + type ≥ 95%") and the S2.3 test plan:

- the prompt file — ``test-automator@1`` is registered with stable
  metadata (§31.6); all three context variables render; a missing
  variable fails loud (``PromptRenderError``);
- the agent against a fake ``httpx`` transport (same pattern as
  ``tests/unit/test_test_design_agent.py``) — the contract output
  (one JSON metadata line + one fenced code block) comes back as a
  schema-valid :class:`GeneratedTest` with stable ``prompt_ref`` and
  ``agent`` audit metadata; the rendered repository context is what is
  actually sent; a missing prompt, a non-contract output, and an LLM
  error all fail loud;
- :func:`parse_generated_test` — strict about the contract, tolerant of
  surrounding prose, loud on everything else (no metadata, invalid
  JSON, missing keys, unterminated object, no code);
- :class:`GeneratedTest` validation — absolute paths, backslashes,
  directory escapes, non-test files, unknown or mismatched
  language/framework pairs, and empty content are all rejected;
- the golden set — v1 loads (two fixtures, stable target); a missing
  file / invalid JSON / invalid schema / duplicate ids all fail loud
  (``AutomationGoldenSetError``);
- the convention expectations — ``conventions_respected()`` enforces
  the file path, language, framework, and must-use / must-not-use
  tokens;
- the local gate (skipped when the workspace toolchain is absent) —
  the golden fixtures pass ESLint + strict ``tsc`` in an isolated
  sandbox with the real ``apps/web`` toolchain and the type-only
  Playwright stub, while negative probes (unknown ``page`` method,
  unknown ``expect`` matcher — the Cypress-ism ``toHaveTextContent``,
  intentionally absent from the stub exactly as from real Playwright —
  implicit-``any`` binding, unused binding) are caught on the right
  axis (lint vs type);
- the evaluation runner — a full golden run on the fake gateway
  reports per-fixture results and stable totals; failures are
  isolated to their own fixture; a missing repository context fails
  the fixture;
- the CLI contract — JSON report on stdout (also written to ``--report``,
  with a write notice on stderr), exit ``0`` (targets met) / ``1``
  (targets missed) / ``2`` (configuration error, loud on stderr), and
  per-fixture isolation end to end. The CLI end-to-end tests use an
  in-process OpenAI-compatible HTTP server — still no real LLM.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import pathlib
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import ValidationError
from qa_copilot_ai import (
    AutomationAgent,
    AutomationAgentResult,
    AutomationInput,
    FilePromptStore,
    GeneratedTest,
    InMemoryPromptStore,
    LLMError,
    LLMGateway,
    PromptNotFound,
    PromptRenderError,
    PromptSpec,
    parse_generated_test,
)
from qa_copilot_ai.automation import (
    AutomationGoldenSetError,
    default_golden_path,
    load_automation_golden_set,
)
from qa_copilot_ai.automation import cli as automation_cli
from qa_copilot_ai.automation.checker import (
    Toolchain,
    check_generated_file,
    find_toolchain,
    prepare_sandbox,
)
from qa_copilot_ai.automation.runner import (
    AutomationReport,
    RepoContext,
    conventions_respected,
    run_automation_eval,
)
from qa_copilot_ai.prompts import render_prompt
from qa_copilot_domain import LocatorStyle, RepositoryProfile, TestConventions

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "packages" / "ai" / "prompts"
SAMPLE_JS_REPO = REPO_ROOT / "packages" / "repository" / "samples" / "sample_repos" / "js-web-app"

# The real gate toolchain of this workspace (node on PATH +
# ``apps/web/node_modules`` + the type-only Playwright stub). When it is
# not available (fresh clone without installed deps) the subprocess
# tests skip; everything else still runs.
TOOLCHAIN = find_toolchain(REPO_ROOT)
GATE_SKIP = pytest.mark.skipif(
    TOOLCHAIN is None, reason="workspace node/ESLint/tsc toolchain not available"
)

# The same golden set the S2.3 live eval (``python -m
# qa_copilot_ai.automation``) scores a real local LLM against.
GOLDEN = load_automation_golden_set(default_golden_path())
MODEL_OUTPUTS = {fixture.id: fixture.model_output for fixture in GOLDEN.fixtures}

# In-memory spec for the agent/runner tests — the same three variables,
# mirroring the prompt file's wire values (temperature 0.2, output budget
# 40000, build bible §9).
PROMPT_SPEC = PromptSpec(
    name="test-automator",
    version=1,
    body=(
        "Repository: {{repository_profile}} | Conventions: {{conventions}} | "
        "Automate this approved test case: {{test_case}}"
    ),
    model_class="coder",
    input_budget=60000,
    output_budget=40000,
    schema_ref="generated-test/v1",
    temperature=0.2,
)


def _assistant(content: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 210},
    }


Handler = Callable[[httpx.Request], httpx.Response]


class _AsyncMockTransport(httpx.AsyncBaseTransport):
    """Async-transport shim so ``AsyncClient`` accepts a sync fake handler."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._handler(request)


def _gateway(handler: Handler) -> LLMGateway:
    return LLMGateway(
        "http://llm.test/v1",
        "fake-model",
        max_retries=0,
        transport=_AsyncMockTransport(handler),
    )


def _repo_context() -> RepoContext:
    """The shared S2.x contract for the golden set's ``js-web-app`` repo."""
    profile = RepositoryProfile(
        languages=["typescript"],
        frameworks=["react"],
        test_frameworks=["playwright"],
        test_dirs=["e2e"],
        test_file_count=1,
        package_managers=["npm"],
        file_count=12,
    )
    conventions = TestConventions(
        test_file_patterns=["*.spec.ts"],
        locator_styles=[LocatorStyle(api="getByRole", framework="playwright", count=3)],
        test_configs=["playwright.config.ts"],
        base_url="http://localhost:5173",
    )
    return RepoContext(profile=profile, conventions=conventions)


def _automation_input(fixture_id: str = "AUTO-001") -> AutomationInput:
    fixture = next(f for f in GOLDEN.fixtures if f.id == fixture_id)
    context = _repo_context()
    return AutomationInput(
        test_case=fixture.test_case,
        repository_profile=context.profile,
        conventions=context.conventions,
    )


def _agent_run(
    handler: Handler,
    input_: AutomationInput,
    specs: list[PromptSpec] | None = None,
) -> AutomationAgentResult:
    """Run the agent once against the fake gateway; return the result."""

    async def _do() -> AutomationAgentResult:
        store = InMemoryPromptStore(specs if specs is not None else [PROMPT_SPEC])
        gateway = _gateway(handler)
        try:
            return await AutomationAgent(store, gateway).run(input_)
        finally:
            await gateway.aclose()

    return asyncio.run(_do())


# --- Prompt file + rendering ---------------------------------------------------


def test_prompt_file_registered() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("test-automator")
    assert spec.ref == "test-automator@1"
    assert spec.model_class == "coder"
    assert spec.temperature == 0.2
    assert spec.input_budget == 60000
    assert spec.output_budget == 40000
    assert spec.schema_ref == "generated-test/v1"
    for variable in ("repository_profile", "conventions", "test_case"):
        assert "{{" + variable + "}}" in spec.body


def test_prompt_renders_repository_context() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("test-automator")
    fixture = GOLDEN.fixtures[0]
    context = _repo_context()
    rendered = render_prompt(
        spec,
        repository_profile=json.dumps(context.profile.model_dump(mode="json")),
        conventions=json.dumps(context.conventions.model_dump(mode="json")),
        test_case=json.dumps(fixture.test_case.model_dump(mode="json")),
    )
    assert fixture.test_case.title in rendered
    assert "typescript" in rendered
    assert "getByRole" in rendered
    assert "{{" not in rendered


def test_prompt_render_missing_variable_fails_loud() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("test-automator")
    with pytest.raises(PromptRenderError, match="test_case"):
        render_prompt(
            spec,
            repository_profile="{}",
            conventions="{}",
        )


# --- Agent execution (fake gateway) ---------------------------------------------


def test_agent_run_returns_generated_test() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant(MODEL_OUTPUTS["AUTO-001"]))

    result = _agent_run(handler, _automation_input("AUTO-001"))
    assert result.prompt_ref == "test-automator@1"
    assert result.call.agent == "test-automator"
    assert result.call.usage.tokens_in == 40
    assert result.test.file_path == "e2e/counter-increment.spec.ts"
    assert result.test.language == "typescript"
    assert result.test.framework == "playwright"
    assert "@playwright/test" in result.test.content
    assert "getByRole" in result.test.content
    assert "```" not in result.test.content  # fences stripped, code is raw
    assert result.test.notes == []


def test_agent_run_sends_rendered_prompt_and_file_settings() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_assistant(MODEL_OUTPUTS["AUTO-001"]))

    _agent_run(handler, _automation_input("AUTO-001"))
    assert seen["model"] == "fake-model"
    assert seen["temperature"] == 0.2
    assert seen["max_tokens"] == 40000
    messages = seen["messages"]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "user"
    prompt = str(messages[0]["content"])
    # The shared S2.x contract is actually rendered into the prompt.
    assert "Counter increments once per click" in prompt
    assert "typescript" in prompt
    assert "getByRole" in prompt
    assert "{{" not in prompt


def test_agent_run_missing_prompt_fails_loud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the gateway must not be called without a prompt")

    with pytest.raises(PromptNotFound, match="test-automator"):
        _agent_run(handler, _automation_input("AUTO-001"), specs=[])


def test_agent_run_invalid_output_fails_loud() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant("Sorry, I cannot automate that."))

    with pytest.raises(ValueError, match="no JSON metadata"):
        _agent_run(handler, _automation_input("AUTO-001"))


def test_agent_run_llm_error_propagates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    with pytest.raises(LLMError):
        _agent_run(handler, _automation_input("AUTO-001"))


# --- parse_generated_test -------------------------------------------------------


def test_parse_valid_contract_output() -> None:
    test = parse_generated_test(MODEL_OUTPUTS["AUTO-001"])
    assert test.file_path == "e2e/counter-increment.spec.ts"
    assert test.language == "typescript"
    assert test.framework == "playwright"
    assert test.content.startswith('import { expect, test } from "@playwright/test";')
    assert "getByRole" in test.content
    assert "```" not in test.content
    assert test.notes == []


def test_parse_preserves_notes() -> None:
    raw = (
        '{"file_path": "e2e/notes.spec.ts", "language": "typescript", '
        '"framework": "playwright", "notes": ["assumed base url from profile"]}\n'
        "\n```ts\nconst x = 1;\n```"
    )
    test = parse_generated_test(raw)
    assert test.notes == ["assumed base url from profile"]
    assert test.content == "const x = 1;"


def test_parse_accepts_raw_code_without_fences() -> None:
    raw = (
        '{"file_path": "e2e/raw.spec.ts", "language": "typescript", '
        '"framework": "playwright"}\n\nconst x = 1;'
    )
    assert parse_generated_test(raw).content == "const x = 1;"


def test_parse_tolerates_surrounding_prose() -> None:
    raw = "Here is your test:\n" + MODEL_OUTPUTS["AUTO-001"] + "\nHope that helps!"
    test = parse_generated_test(raw)
    assert test.file_path == "e2e/counter-increment.spec.ts"
    assert "```" not in test.content


def test_parse_no_metadata_fails_loud() -> None:
    with pytest.raises(ValueError, match="no JSON metadata"):
        parse_generated_test("here is your test")


def test_parse_invalid_json_fails_loud() -> None:
    raw = "{file_path: bad}\n\n```ts\ncode\n```"
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_generated_test(raw)


def test_parse_missing_key_fails_loud() -> None:
    raw = '{"file_path": "e2e/x.spec.ts", "language": "typescript"}\n\n```ts\ncode\n```'
    with pytest.raises(ValueError, match="missing key"):
        parse_generated_test(raw)


def test_parse_unterminated_metadata_fails_loud() -> None:
    raw = '{"file_path": "e2e/x.spec.ts",\n\n```ts\ncode\n```'
    with pytest.raises(ValueError, match="unterminated"):
        parse_generated_test(raw)


def test_parse_no_code_fails_loud() -> None:
    raw = (
        '{"file_path": "e2e/x.spec.ts", "language": "typescript", '
        '"framework": "playwright"}\n\n```\n```'
    )
    with pytest.raises(ValueError, match="no generated code"):
        parse_generated_test(raw)


# --- GeneratedTest validation ---------------------------------------------------


def _generated(
    file_path: str,
    content: str = "const x = 1;",
    language: str = "typescript",
    framework: str = "playwright",
) -> GeneratedTest:
    return GeneratedTest(
        file_path=file_path,
        language=language,
        framework=framework,
        content=content,
    )


def test_generated_test_accepts_valid_file() -> None:
    assert _generated("e2e/counter-increment.spec.ts").file_path == "e2e/counter-increment.spec.ts"


def test_generated_test_rejects_absolute_path() -> None:
    with pytest.raises(ValidationError, match="POSIX"):
        _generated("/abs/e2e/x.spec.ts")


def test_generated_test_rejects_backslashes() -> None:
    with pytest.raises(ValidationError, match="POSIX"):
        _generated("e2e\\x.spec.ts")


def test_generated_test_rejects_directory_escape() -> None:
    with pytest.raises(ValidationError, match="escape"):
        _generated("e2e/../x.spec.ts")


def test_generated_test_rejects_non_test_file() -> None:
    with pytest.raises(ValidationError, match="test file"):
        _generated("e2e/util.ts")


def test_generated_test_rejects_unknown_language() -> None:
    with pytest.raises(ValidationError, match="unknown language"):
        _generated("e2e/x.spec.ts", language="rust")


def test_generated_test_rejects_unknown_framework() -> None:
    with pytest.raises(ValidationError, match="unknown framework"):
        _generated("e2e/x.spec.ts", framework="cypress")


def test_generated_test_rejects_mismatched_pair() -> None:
    with pytest.raises(ValidationError, match="does not run in"):
        _generated("e2e/x.spec.ts", language="python", framework="playwright")


def test_generated_test_rejects_empty_content() -> None:
    with pytest.raises(ValidationError):
        _generated("e2e/x.spec.ts", content="")


# --- Golden set ---------------------------------------------------------------


def test_golden_set_loads_v1() -> None:
    assert GOLDEN.name == "S2.3 test automation golden set"
    assert GOLDEN.version == "v1"
    assert GOLDEN.source.step == "S2.3"
    assert GOLDEN.targets.lint_type_pass_min == 0.95
    assert [fixture.id for fixture in GOLDEN.fixtures] == ["AUTO-001", "AUTO-002"]
    for fixture in GOLDEN.fixtures:
        # Every fixture's recorded output is a valid contract output.
        test = parse_generated_test(fixture.model_output)
        expected = fixture.expectations
        if expected.file_path is not None:
            assert test.file_path == expected.file_path
        else:
            assert expected.file_path_pattern is not None
            assert fnmatch.fnmatchcase(test.file_path, expected.file_path_pattern)
        assert test.language == expected.language
        assert test.framework == fixture.expectations.framework
        ok, problems = conventions_respected(test, fixture.expectations)
        assert ok, problems


def test_golden_set_missing_file_fails_loud(tmp_path: pathlib.Path) -> None:
    with pytest.raises(AutomationGoldenSetError, match="not found"):
        load_automation_golden_set(tmp_path / "nope.json")


def _write(tmp_path: pathlib.Path, data: object) -> pathlib.Path:
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_golden_set_invalid_json_fails_loud(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "golden.json"
    path.write_text("not json {", encoding="utf-8")
    with pytest.raises(AutomationGoldenSetError, match="invalid automation golden set"):
        load_automation_golden_set(path)


def test_golden_set_invalid_schema_fails_loud(tmp_path: pathlib.Path) -> None:
    data = json.loads(GOLDEN.model_dump_json())
    del data["source"]  # required key
    with pytest.raises(AutomationGoldenSetError, match="invalid automation golden set"):
        load_automation_golden_set(_write(tmp_path, data))


def test_golden_set_duplicate_ids_fail_loud(tmp_path: pathlib.Path) -> None:
    data = json.loads(GOLDEN.model_dump_json())
    data["fixtures"][1]["id"] = "AUTO-001"
    with pytest.raises(AutomationGoldenSetError, match="unique"):
        load_automation_golden_set(_write(tmp_path, data))


# --- Convention expectations ----------------------------------------------------


def _auto001_test() -> GeneratedTest:
    return parse_generated_test(MODEL_OUTPUTS["AUTO-001"])


def test_conventions_respected_ok() -> None:
    expected = GOLDEN.fixtures[0].expectations
    ok, problems = conventions_respected(_auto001_test(), expected)
    assert ok
    assert problems == []


def test_conventions_respected_flags_missing_must_use_token() -> None:
    expected = GOLDEN.fixtures[0].expectations
    test = _auto001_test().model_copy(update={"content": "await page.click('button');"})
    ok, problems = conventions_respected(test, expected)
    assert not ok
    assert any("must not use" in p for p in problems) or any("missing" in p for p in problems)
    assert any("getByRole" in p or "toHaveText" in p for p in problems)


def test_conventions_respected_flags_forbidden_token() -> None:
    expected = GOLDEN.fixtures[0].expectations
    test = _auto001_test().model_copy(update={"content": "el.querySelector('#counter');"})
    ok, problems = conventions_respected(test, expected)
    assert not ok
    assert any("querySelector" in p and "must not use" in p for p in problems)


def test_conventions_respected_flags_wrong_file_path() -> None:
    """A path outside the expected test dir + pattern is a convention break."""
    expected = GOLDEN.fixtures[0].expectations
    test = _auto001_test().model_copy(update={"file_path": "tests/other.spec.ts"})
    ok, problems = conventions_respected(test, expected)
    assert not ok
    assert any("file_path" in p for p in problems)


def test_conventions_respected_allows_any_conforming_name() -> None:
    """test-automator@1 rule 1 leaves ``<name>`` to the model (test dir +
    pattern), so any name inside the expected pattern conforms — the
    2026-08-28 live eval's ``e2e/counter.spec.ts`` choice was correct and
    must not be flagged."""
    expected = GOLDEN.fixtures[0].expectations
    test = _auto001_test().model_copy(update={"file_path": "e2e/counter.spec.ts"})
    ok, problems = conventions_respected(test, expected)
    assert ok, problems


# --- Local lint/type gate (checker.py) -----------------------------------------
#
# The S2.3 exit gate runs the real workspace toolchain (node + the
# TypeScript / ESLint installs under ``apps/web/node_modules`` + the
# type-only Playwright stub). These tests skip — loudly, by name — when
# that toolchain is not installed on this machine.


@GATE_SKIP
def test_find_toolchain_resolves_workspace_toolchain() -> None:
    assert TOOLCHAIN is not None
    assert TOOLCHAIN.node.name.lower() in ("node", "node.exe")
    assert TOOLCHAIN.tsc.is_file()
    assert TOOLCHAIN.eslint.is_file()
    assert TOOLCHAIN.eslint_config.is_file()
    assert TOOLCHAIN.playwright_stub.is_dir()


@GATE_SKIP
def test_golden_fixtures_pass_local_gate(tmp_path: pathlib.Path) -> None:
    assert TOOLCHAIN is not None
    for fixture in GOLDEN.fixtures:
        generated = parse_generated_test(fixture.model_output)
        sandbox = tmp_path / fixture.id
        prepare_sandbox(sandbox, generated, TOOLCHAIN)
        result = check_generated_file(sandbox, generated.file_path, TOOLCHAIN)
        assert result.ok, f"{fixture.id}: lint={result.lint_output!r} type={result.type_output!r}"


def test_prepare_sandbox_materializes_file_and_stub(tmp_path: pathlib.Path) -> None:
    generated = parse_generated_test(MODEL_OUTPUTS["AUTO-001"])
    toolchain = Toolchain(
        node=pathlib.Path("node"),
        tsc=pathlib.Path("tsc.js"),
        eslint=pathlib.Path("eslint.js"),
        eslint_config=pathlib.Path("eslint.config.js"),
        playwright_stub=REPO_ROOT / "tests" / "unit" / "support" / "playwright-test",
    )
    target = prepare_sandbox(tmp_path / "sandbox", generated, toolchain)
    assert target == tmp_path / "sandbox" / "e2e" / "counter-increment.spec.ts"
    assert target.read_text(encoding="utf-8") == generated.content
    stub = tmp_path / "sandbox" / "node_modules" / "@playwright" / "test"
    assert (stub / "package.json").is_file()
    assert (stub / "index.d.ts").is_file()
    assert (stub / "index.js").is_file()


# Negative probes: code the gate must REJECT. Each probe fails on exactly
# one axis so the assertions pin down WHERE the gate caught it (lint vs
# type) — the scratch-gate session (2026-08-27) verified this behavior.

_PROBE_INVALID_API = GeneratedTest(
    file_path="e2e/probe-invalid-api.spec.ts",
    language="typescript",
    framework="playwright",
    content=(
        'import { expect, test } from "@playwright/test";\n'
        "\n"
        'test("invalid API probe", async ({ page }) => {\n'
        '  await page.goto("/");\n'
        '  await page.doesNotExist("not a real method");\n'
        '  await expect(page.getByRole("button", { name: "Go" })).toHaveTextContent("hi");\n'
        "});\n"
    ),
)


@GATE_SKIP
def test_gate_catches_unknown_page_method_and_matcher(tmp_path: pathlib.Path) -> None:
    """``page.doesNotExist`` and the Cypress-only ``toHaveTextContent``
    are both rejected on the TYPE axis: the stub declares the real
    Playwright matcher surface (``toHaveText`` / ``toContainText``) and
    deliberately omits ``toHaveTextContent`` — real Playwright has no
    such assertion, so the gate must reject it too."""
    assert TOOLCHAIN is not None
    sandbox = tmp_path / "probe"
    prepare_sandbox(sandbox, _PROBE_INVALID_API, TOOLCHAIN)
    result = check_generated_file(sandbox, _PROBE_INVALID_API.file_path, TOOLCHAIN)
    assert not result.type_ok
    assert "doesNotExist" in result.type_output
    assert "toHaveTextContent" in result.type_output
    assert result.lint_ok  # the probe is lint-clean: the failure must be a type error


_PROBE_IMPLICIT_ANY = GeneratedTest(
    file_path="e2e/probe-implicit-any.spec.ts",
    language="typescript",
    framework="playwright",
    content=(
        'import { expect, test } from "@playwright/test";\n'
        "\n"
        'test("implicit-any probe", async ({ page }) => {\n'
        "  const run = (fn) => fn({ page: page });\n"
        '  run((arg) => arg.page.getByRole("button").click());\n'
        "  expect(1).toBe(1);\n"
        "});\n"
    ),
)


@GATE_SKIP
def test_gate_catches_implicit_any_binding(tmp_path: pathlib.Path) -> None:
    assert TOOLCHAIN is not None
    sandbox = tmp_path / "probe"
    prepare_sandbox(sandbox, _PROBE_IMPLICIT_ANY, TOOLCHAIN)
    result = check_generated_file(sandbox, _PROBE_IMPLICIT_ANY.file_path, TOOLCHAIN)
    assert not result.type_ok
    assert "implicitly has an 'any' type" in result.type_output
    assert result.lint_ok


_PROBE_UNUSED = GeneratedTest(
    file_path="e2e/probe-unused.spec.ts",
    language="typescript",
    framework="playwright",
    content=(
        'import { expect, test } from "@playwright/test";\n'
        "\n"
        "const unusedHelper = (value: string) => value.length;\n"
        "\n"
        'test("unused-binding probe", async ({ page }) => {\n'
        '  await page.goto("/");\n'
        '  await expect(page.getByRole("button", { name: "Go" })).toHaveText("hi");\n'
        "});\n"
    ),
)


@GATE_SKIP
def test_gate_catches_unused_binding(tmp_path: pathlib.Path) -> None:
    assert TOOLCHAIN is not None
    sandbox = tmp_path / "probe"
    prepare_sandbox(sandbox, _PROBE_UNUSED, TOOLCHAIN)
    result = check_generated_file(sandbox, _PROBE_UNUSED.file_path, TOOLCHAIN)
    assert not result.lint_ok
    assert "unusedHelper" in result.lint_output
    assert result.type_ok  # the probe type-checks: the failure must be a lint error


# --- Evaluation runner (runner.py) ---------------------------------------------
#
# Full golden-set runs against the fake gateway; the real lint/type gate
# runs for every generated fixture (see the gate section above).


def _golden_handler(broken: frozenset[str] = frozenset()) -> Handler:
    """Fake LLM: per-fixture golden outputs, keyed off the test case title."""
    by_title = {fixture.test_case.title: fixture.model_output for fixture in GOLDEN.fixtures}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = str(body["messages"][0]["content"])
        match = re.search(r'"title":\s*"([^"]+)"', prompt)
        assert match is not None, f"rendered prompt has no test case title: {prompt[:120]!r}"
        title = match.group(1)
        content = "not a generated test at all" if title in broken else by_title[title]
        return httpx.Response(200, json=_assistant(content))

    return handler


def _eval_run(
    tmp_path: pathlib.Path,
    contexts: dict[str, RepoContext],
    *,
    broken: frozenset[str] = frozenset(),
    toolchain: Toolchain | None = None,
) -> AutomationReport:
    """One full golden-set run on the fake gateway (the real gate included)."""
    resolved = toolchain if toolchain is not None else TOOLCHAIN
    assert resolved is not None, "the gate requires a toolchain"
    store = InMemoryPromptStore([PROMPT_SPEC])
    gateway = _gateway(_golden_handler(broken))
    agent = AutomationAgent(store, gateway)

    async def _do() -> AutomationReport:
        try:
            return await run_automation_eval(
                GOLDEN,
                agent=agent,
                model="fake-model",
                prompt_ref="test-automator@1",
                contexts=contexts,
                toolchain=resolved,
                sandbox_root=tmp_path / "sandbox",
            )
        finally:
            await gateway.aclose()

    return asyncio.run(_do())


@GATE_SKIP
def test_run_automation_eval_passes_golden_set(tmp_path: pathlib.Path) -> None:
    report = _eval_run(tmp_path, {"js-web-app": _repo_context()})
    assert report.passed
    assert report.schema_version == 1
    assert report.agent == "test-automator"
    assert report.model == "fake-model"
    assert report.prompt_ref == "test-automator@1"
    assert report.golden_name == GOLDEN.name
    assert report.golden_version == GOLDEN.version
    assert report.golden_fixtures == len(GOLDEN.fixtures)
    assert report.repos == ["js-web-app"]
    assert report.targets == {"lint_type_pass_min": 0.95}
    totals = report.totals
    assert totals.fixtures == len(GOLDEN.fixtures)
    assert (totals.passed, totals.failed) == (len(GOLDEN.fixtures), 0)
    assert totals.lint_type_pass_fraction == 1.0
    assert totals.schema_valid_fraction == 1.0
    assert totals.conventions_respected_fraction == 1.0
    for result in report.fixtures:
        assert (result.schema_valid, result.conventions_respected) == (True, True)
        assert (result.lint_ok, result.type_ok, result.passed) == (True, True, True)
        assert result.error is None
        assert result.tokens_in == 40 and result.tokens_out == 210
        assert result.latency_ms is not None and result.latency_ms >= 0


@GATE_SKIP
def test_run_automation_eval_isolates_fixture_failure(tmp_path: pathlib.Path) -> None:
    """A broken fixture fails by itself — the others still pass (§19:
    per-fixture isolation, no cross-fixture contamination)."""
    broken = frozenset({GOLDEN.fixtures[1].test_case.title})
    report = _eval_run(tmp_path, {"js-web-app": _repo_context()}, broken=broken)
    assert not report.passed
    assert report.totals.failed == 1
    assert report.totals.passed == 1
    assert report.totals.lint_type_pass_fraction == 0.5
    by_id = {result.fixture_id: result for result in report.fixtures}
    assert by_id["AUTO-001"].passed
    failed = by_id["AUTO-002"]
    assert not failed.schema_valid
    assert failed.error is not None
    assert "no JSON metadata" in failed.error
    assert failed.tokens_in is None  # never reached the LLM
    assert failed.tokens_out is None


def test_run_automation_eval_without_context_fails_fixture(tmp_path: pathlib.Path) -> None:
    """No repository context → every fixture for that repo fails with a
    loud error (the fixture's own repo is 'js-web-app')."""
    dummy = Toolchain(
        node=pathlib.Path("node"),
        tsc=pathlib.Path("tsc.js"),
        eslint=pathlib.Path("eslint.js"),
        eslint_config=pathlib.Path("eslint.config.js"),
        playwright_stub=pathlib.Path("playwright-test"),
    )
    report = _eval_run(tmp_path, {}, toolchain=dummy)
    assert not report.passed
    assert report.totals.passed == 0
    assert report.totals.failed == len(GOLDEN.fixtures)
    assert report.totals.lint_type_pass_fraction == 0.0
    for result in report.fixtures:
        assert not result.passed
        assert result.error is not None
        assert "no repository context for 'js-web-app'" in result.error


# --- CLI contract (cli.py) ------------------------------------------------------
#
# Config-error paths run the real repository scanner (no gate needed); the
# end-to-end tests stand up an in-process OpenAI-compatible HTTP server, so
# the CLI still never talks to a real LLM — only the gate is real.


def test_cli_rejects_malformed_repo_override(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = automation_cli.main(["--repo", "badpair"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "--repo expects NAME=PATH" in err


def test_cli_rejects_missing_sample_repo(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = automation_cli.main(["--repo", "js-web-app=C:\\nonexistent\\qa-s23"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "sample repo for 'js-web-app' not found at" in err


def test_cli_rejects_malformed_extra_body(capsys: pytest.CaptureFixture[str]) -> None:
    for bad in ("{not json", "[1, 2]", '"just a string"'):
        exit_code = automation_cli.main(["--extra-body", bad])
        err = capsys.readouterr().err
        assert exit_code == 2
        assert "--extra-body must be a JSON object" in err


def test_cli_missing_toolchain_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(automation_cli, "find_toolchain", lambda repo_root: None)
    exit_code = automation_cli.main(["--repo", f"js-web-app={SAMPLE_JS_REPO}"])
    err = capsys.readouterr().err
    assert exit_code == 2
    assert "workspace toolchain not found" in err


class _FakeLLMServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` carrying the captured request bodies."""

    seen_bodies: list[dict[str, object]]


def _fake_llm_server(broken: frozenset[str] = frozenset()) -> _FakeLLMServer:
    """In-process OpenAI-compatible endpoint (POST /chat/completions).

    Every request body is captured on ``server.seen_bodies`` for assertions."""
    by_title = {fixture.test_case.title: fixture.model_output for fixture in GOLDEN.fixtures}
    seen_bodies: list[dict[str, object]] = []

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # http.server API name
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            seen_bodies.append(body)
            prompt = str(body["messages"][0]["content"])
            match = re.search(r'"title":\s*"([^"]+)"', prompt)
            if match is None:
                raise AssertionError(f"rendered prompt has no test case title: {prompt[:120]!r}")
            content = (
                "not a generated test at all"
                if match.group(1) in broken
                else by_title[match.group(1)]
            )
            data = json.dumps(_assistant(content)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt: str, *args: object) -> None:  # http.server API
            """Keep the test output clean."""

    server = _FakeLLMServer(("127.0.0.1", 0), _Handler)
    server.seen_bodies = seen_bodies
    return server


@GATE_SKIP
def test_cli_end_to_end_passes_and_writes_report(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    server = _fake_llm_server()
    try:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        report_path = tmp_path / "report.json"
        exit_code = automation_cli.main(
            [
                "--base-url",
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--model",
                "fake-model",
                "--repo",
                f"js-web-app={SAMPLE_JS_REPO}",
                "--report",
                str(report_path),
                "--sandbox-dir",
                str(tmp_path / "sandbox"),
                "--extra-body",
                '{"chat_template_kwargs": {"enable_thinking": false}}',
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert report["agent"] == "test-automator"
    assert report["model"] == "fake-model"
    assert report["prompt_ref"] == "test-automator@1"
    assert report["golden_name"] == GOLDEN.name
    assert report["golden_version"] == GOLDEN.version
    assert report["repos"] == ["js-web-app"]
    assert report["targets"] == {"lint_type_pass_min": 0.95}
    assert report["passed"] is True
    assert report["totals"]["fixtures"] == len(GOLDEN.fixtures)
    assert report["totals"]["lint_type_pass_fraction"] == 1.0
    assert [fixture["fixture_id"] for fixture in report["fixtures"]] == [
        fixture.id for fixture in GOLDEN.fixtures
    ]
    for fixture in report["fixtures"]:
        assert fixture["passed"] is True
        assert fixture["error"] is None
    assert f"report written to: {report_path}" in captured.err
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    assert server.seen_bodies, "fake server saw no chat-completions request"
    assert all(
        body.get("chat_template_kwargs") == {"enable_thinking": False}
        for body in server.seen_bodies
    )


@GATE_SKIP
def test_cli_end_to_end_failure_isolated_and_exits_1(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One broken fixture → exit 1, and the healthy fixture still passes."""
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    broken = frozenset({GOLDEN.fixtures[1].test_case.title})
    server = _fake_llm_server(broken)
    try:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        exit_code = automation_cli.main(
            [
                "--base-url",
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--model",
                "fake-model",
                "--repo",
                f"js-web-app={SAMPLE_JS_REPO}",
                "--sandbox-dir",
                str(tmp_path / "sandbox"),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    captured = capsys.readouterr()
    assert exit_code == 1, captured.err
    report = json.loads(captured.out)
    assert report["passed"] is False
    assert report["totals"]["passed"] == 1
    assert report["totals"]["failed"] == 1
    by_id = {fixture["fixture_id"]: fixture for fixture in report["fixtures"]}
    assert by_id["AUTO-001"]["passed"] is True
    assert by_id["AUTO-002"]["passed"] is False
    assert "no JSON metadata" in by_id["AUTO-002"]["error"]
