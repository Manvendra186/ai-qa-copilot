"""Fix Agent (S4.2) unit tests — offline suite (no real LLM, no browser).

Covers:
  - the registered ``fix-agent@2`` prompt (spec values, render, §26 guards);
  - ``parse_fix_proposal`` — the strict ``fix-proposal/v1`` contract
    (patch/decline consistency, ``needs_human_approval`` default,
    prose/fence tolerance, fail-loud schema violations);
  - ``make_patch`` / ``apply_patch`` — the S4.2 "applicable" contract
    (inverse round-trip over all fixable golden fixtures, fail-loud on
    unapplicable patches, tolerance for local-model drift);
  - ``FixerAgent`` — audit payload + rendered prompt (fake transport);
  - ``run_fix_eval`` — oracle model (gate passes 8/10), verifier rejection
    (gate fails), wrong action (gate fails), targets override, and failure
    isolation (one bad fixture, the rest still scored);
  - the CLI contract — JSON report on stdout + ``--report``, human summary
    on stderr, exit codes ``0``/``1``/``2``. The end-to-end CLI test runs
    against an in-process OpenAI-compatible HTTP server answering from the
    *real* registered prompts, with the Playwright verifier replaced by an
    oracle — the same pattern as the S4.1 CLI tests.
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from qa_copilot_ai.agents import (
    Diagnosis,
    FailureInvestigatorAgent,
    FixerAgent,
    FixerInput,
    parse_fix_proposal,
)
from qa_copilot_ai.fixer import cli as fixer_cli
from qa_copilot_ai.fixer.app_context import build_app_context
from qa_copilot_ai.fixer.patch import PatchError, apply_patch, make_patch
from qa_copilot_ai.fixer.runner import FixEvalReport, FixVerifier, run_fix_eval
from qa_copilot_ai.gateway import LLMGateway
from qa_copilot_ai.prompts import (
    FilePromptStore,
    InMemoryPromptStore,
    PromptNotFound,
    PromptSpec,
    render_prompt,
)
from qa_copilot_domain import FailureCategory
from qa_copilot_execution.failure import normalize_failure
from qa_copilot_execution.golden import (
    FixFixture,
    FixGoldenSet,
    FixTargets,
    default_fix_golden_path,
    load_fix_golden_set,
)

_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_DIR = _ROOT / "packages" / "ai" / "prompts"
GOLDEN = load_fix_golden_set(default_fix_golden_path())

MODEL = "local-model"
BASE_URL = "http://llm.test/v1"

# --- in-memory prompt specs for the agent/runner tests (fake-model path) ---
# They carry exactly the variable lines the agents render, so the fake model
# can read the diagnosis / broken test back out of the rendered prompt.

INV_SPEC = PromptSpec(
    name="failure-investigator",
    version=1,
    body=(
        "Investigate the failure.\n"
        "Suggested category (best guess): {{category}}\n"
        "Detected signals: {{signals}}\n"
        "Evidence:\n{{evidence}}\n"
        "HTTP status: {{http_status}}\n"
        "Failing selector: {{selector}}\n"
        "Request endpoint: {{endpoint}}\n"
        "Respond with ONE JSON object only."
    ),
    model_class="reasoner",
    input_budget=60000,
    output_budget=4000,
    schema_ref="failure-analysis/v1",
    temperature=0.2,
)

FIX_SPEC = PromptSpec(
    name="fix-agent",
    version=1,
    body=(
        "Fix one failed test.\n"
        "Diagnosis category: {{category}}\n"
        "Diagnosis root cause: {{root_cause}}\n"
        "Suggested fix (prior): {{suggested_fix}}\n"
        "Confidence: {{confidence}}\n"
        "Evidence:\n{{evidence}}\n"
        "Signals: {{signals}}\n"
        "HTTP status: {{http_status}}\n"
        "Selector: {{selector}}\n"
        "Endpoint: {{endpoint}}\n"
        "App context: {{app_context}}\n"
        "Target file: {{file_path}}\n"
        "Broken test file:\n{{test_code}}\n"
        "Respond with ONE JSON object only."
    ),
    model_class="coder",
    input_budget=60000,
    output_budget=40000,
    schema_ref="fix-proposal/v1",
    temperature=0.3,
)

# --- schema-valid proposals (parser fixtures) -------------------------------

VALID_PATCH: dict[str, object] = {
    "action": "patch",
    "target_file": "e2e/cart.spec.js",
    "patch": (
        "--- a/e2e/cart.spec.js\n"
        "+++ b/e2e/cart.spec.js\n"
        "@@ -1,3 +1,3 @@\n"
        " test('adds to cart', async ({ page }) => {\n"
        "-  await page.getByText('Add').click()\n"
        "+  await page.getByRole('button', { name: 'Add to cart' }).click()\n"
        " })"
    ),
    "rationale": "scoped the stale locator to a role-based one",
    "needs_human_approval": True,
}

VALID_DECLINE: dict[str, object] = {
    "action": "decline",
    "target_file": None,
    "patch": None,
    "rationale": "product defect — no safe test-side fix; reproduce and file a ticket",
    "needs_human_approval": True,
}


# --- fake-model harness ------------------------------------------------------

Handler = Callable[[httpx.Request], httpx.Response]

#: Marker the in-memory FIX_SPEC carries (the agent/runner tests).
FIXER_MARKER_FAKE = "Broken test file"
#: Marker the *real* registered fix-agent prompt carries (the CLI e2e test).
FIXER_MARKER_REAL = "Broken test file (your patch must apply to exactly this text)"
#: The suggested-category line of the in-memory INV_SPEC.
INV_CATEGORY_FAKE = re.compile(r"Suggested category \(best guess\):\s*(\w+)")
#: The suggested-category line of the *real* registered investigator prompt.
INV_CATEGORY_REAL = re.compile(r"Suggested category \(strong prior\):\s*(\w+)")


def _assistant(text: str) -> dict[str, object]:
    """An OpenAI-compatible chat-completion payload wrapping *text*."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 210, "total_tokens": 250},
    }


def _gateway(handler: Handler) -> LLMGateway:
    return LLMGateway(BASE_URL, MODEL, transport=httpx.MockTransport(handler))


def _oracle_reply(
    content: str, *, fixer_marker: str, investigator_pattern: re.Pattern[str]
) -> dict[str, object]:
    """The oracle model: correct action per fixture, ground-truth patch.

    The patch is generated with :func:`make_patch` from the fixture's
    (test_code, fixed_code) pair — the same diff the model is asked to emit.
    """
    if fixer_marker in content:
        fixture = next(f for f in GOLDEN.fixtures if f.file_path in content)
        if fixture.has_fix:
            fixed_code = fixture.fixed_code
            assert fixed_code is not None  # has_fix ⇒ fixed_code (golden contract)
            return {
                "action": "patch",
                "target_file": fixture.file_path,
                "patch": make_patch(fixture.test_code, fixed_code, fixture.file_path),
                "rationale": "oracle: the fixture's known-good test-side fix",
                "needs_human_approval": True,
            }
        return {
            "action": "decline",
            "target_file": None,
            "patch": None,
            "rationale": "oracle: no safe test-side fix exists for this defect",
            "needs_human_approval": True,
        }
    match = investigator_pattern.search(content)
    assert match is not None, "investigator prompt must carry the suggested category"
    return {
        "category": match.group(1),
        "root_cause": "oracle: evidence-driven root cause",
        "confidence": 0.9,
        "evidence": ["oracle cited the captured evidence line"],
        "suggested_fix": "oracle: apply the fixture's known-good fix",
        "needs_human_approval": True,
    }


def _oracle_handler(request: httpx.Request) -> httpx.Response:
    """Fake transport: the oracle model over the in-memory prompt specs."""
    content = json.loads(request.content)["messages"][0]["content"]
    reply = _oracle_reply(
        content, fixer_marker=FIXER_MARKER_FAKE, investigator_pattern=INV_CATEGORY_FAKE
    )
    return httpx.Response(200, json=_assistant(json.dumps(reply)))


def _decliner_handler(request: httpx.Request) -> httpx.Response:
    """Fake transport: the model always declines (wrong action on 8/10)."""
    content = json.loads(request.content)["messages"][0]["content"]
    if FIXER_MARKER_FAKE in content:
        reply: dict[str, object] = dict(VALID_DECLINE)
    else:
        match = INV_CATEGORY_FAKE.search(content)
        assert match is not None
        reply = {
            "category": match.group(1),
            "root_cause": "decliner: no opinion",
            "confidence": 0.5,
            "evidence": ["decliner evidence line"],
            "suggested_fix": "decliner: no fix",
            "needs_human_approval": True,
        }
    return httpx.Response(200, json=_assistant(json.dumps(reply)))


async def _oracle_verifier(fixture: FixFixture, patched: str) -> bool:
    """Offline oracle gate: the patched file equals the known-good fix."""
    return patched.strip() == (fixture.fixed_code or "").strip()


async def _failing_verifier(fixture: FixFixture, patched: str) -> bool:
    return False


def _agents(handler: Handler) -> tuple[FailureInvestigatorAgent, FixerAgent, LLMGateway]:
    store = InMemoryPromptStore([INV_SPEC, FIX_SPEC])
    gateway = _gateway(handler)
    return FailureInvestigatorAgent(store, gateway), FixerAgent(store, gateway), gateway


def _run(
    handler: Handler,
    verifier: FixVerifier,
    golden: FixGoldenSet = GOLDEN,
) -> FixEvalReport:
    """One full ``run_fix_eval`` over the golden set (fake model, fake verifier)."""
    investigator, fixer, gateway = _agents(handler)

    async def _go() -> FixEvalReport:
        try:
            return await run_fix_eval(
                golden,
                investigator=investigator,
                fixer=fixer,
                model=MODEL,
                fixer_prompt_ref="fix-agent@1",
                verifier=verifier,
            )
        finally:
            await gateway.aclose()

    return asyncio.run(_go())


# --- the registered prompt (packages/ai/prompts/fix-agent.v1.md) -------------


def test_prompt_file_registered() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("fix-agent")
    assert spec.ref == "fix-agent@2"
    assert spec.model_class == "coder"
    assert spec.input_budget == 60000
    assert spec.output_budget == 40000
    assert spec.schema_ref == "fix-proposal/v1"
    assert spec.temperature == 0.3
    variables = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", spec.body))
    assert variables == {
        "category",
        "root_cause",
        "suggested_fix",
        "confidence",
        "evidence",
        "signals",
        "http_status",
        "selector",
        "endpoint",
        "file_path",
        "test_code",
        "app_context",
    }
    # §26 category guard + no auto-heal must be in the prompt.
    assert "NEVER flip or loosen an assertion" in spec.body
    assert "needs_human_approval" in spec.body
    assert "decline" in spec.body


def test_prompt_renders_full_context() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("fix-agent")
    rendered = render_prompt(
        spec,
        category="automation_defect",
        root_cause="stale_locator",
        suggested_fix="scope the locator to the first card",
        confidence="0.90",
        evidence="- locator resolved to 4 elements",
        signals="locator_timeout, strict_mode",
        http_status="n/a",
        selector=".cart-add",
        endpoint="n/a",
        app_context="### client/src/testids.js\nexport const TESTIDS = {};",
        file_path="e2e/cart.spec.js",
        test_code="test('adds to cart', async ({ page }) => {})",
    )
    assert "{{" not in rendered
    assert "### client/src/testids.js" in rendered
    assert "stale_locator" in rendered
    assert "e2e/cart.spec.js" in rendered
    assert "test('adds to cart'" in rendered


# --- read-only application context (v2 app_context) ---------------------------


def test_build_app_context_includes_priority_files(tmp_path: pathlib.Path) -> None:
    (tmp_path / "client" / "src").mkdir(parents=True)
    (tmp_path / "client" / "src" / "testids.js").write_text(
        "export const TESTIDS = { add: 'cart-add' };\n", encoding="utf-8"
    )
    (tmp_path / "client" / "src" / "extra.js").write_text("export const X = 1;\n", encoding="utf-8")
    context = build_app_context(tmp_path)
    assert "Read-only source of the application under test" in context
    assert "your patch may only touch the target test file" in context
    assert "### client/src/testids.js" in context
    assert "### client/src/extra.js" in context
    # Priority order: the curated file precedes the walked one.
    assert context.index("### client/src/testids.js") < context.index("### client/src/extra.js")


def test_build_app_context_respects_size_cap(tmp_path: pathlib.Path) -> None:
    (tmp_path / "client" / "src").mkdir(parents=True)
    (tmp_path / "client" / "src" / "a.js").write_text("A" * 300 + "\n", encoding="utf-8")
    (tmp_path / "client" / "src" / "b.js").write_text("B" * 300 + "\n", encoding="utf-8")
    context = build_app_context(tmp_path, max_chars=700)
    assert len(context) <= 700
    assert "### client/src/a.js" in context  # first file kept
    assert "### client/src/b.js" not in context  # second dropped by the cap
    assert "omitted for size" in context


def test_build_app_context_missing_or_empty_dir(tmp_path: pathlib.Path) -> None:
    assert build_app_context(tmp_path / "no-such-dir") == ""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert build_app_context(empty) == ""
    (empty / "x.js").write_text("x\n", encoding="utf-8")
    # A cap smaller than the header alone also means "no context".
    assert build_app_context(empty, max_chars=10) == ""


# --- parse_fix_proposal (fix-proposal/v1) -------------------------------------


def test_parse_valid_patch() -> None:
    proposal = parse_fix_proposal(json.dumps(VALID_PATCH))
    assert proposal.action == "patch"
    assert proposal.target_file == "e2e/cart.spec.js"
    assert proposal.patch is not None
    assert proposal.patch.startswith("--- a/")
    assert proposal.needs_human_approval is True
    assert proposal.rationale


def test_parse_valid_decline() -> None:
    proposal = parse_fix_proposal(json.dumps(VALID_DECLINE))
    assert proposal.action == "decline"
    assert proposal.patch is None
    assert proposal.target_file is None
    assert proposal.needs_human_approval is True


def test_parse_defaults_needs_human_approval_to_true() -> None:
    payload = dict(VALID_PATCH)
    del payload["needs_human_approval"]
    assert parse_fix_proposal(json.dumps(payload)).needs_human_approval is True


@pytest.mark.parametrize(
    "text",
    [
        "Here is the proposal:\n" + json.dumps(VALID_DECLINE),
        "```json\n" + json.dumps(VALID_PATCH) + "\n```",
    ],
)
def test_parse_tolerates_prose_and_fences(text: str) -> None:
    assert parse_fix_proposal(text).action in {"patch", "decline"}


def test_parse_no_json_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        parse_fix_proposal("I would patch the locator if I could.")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({**VALID_PATCH, "action": "banana"}, "schema validation"),
        ({**VALID_PATCH, "patch": "   "}, "non-empty unified-diff patch"),
        ({**VALID_PATCH, "target_file": ""}, "non-empty target_file"),
        ({**VALID_DECLINE, "patch": "@@ -1,1 +1,1 @@\n+a"}, "must not carry a patch"),
        ({"action": "decline"}, "schema validation"),
    ],
)
def test_parse_schema_violations_raise(payload: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_fix_proposal(json.dumps(payload))


# --- make_patch / apply_patch (the "applicable" contract) ----------------------


def test_make_patch_identical_returns_empty() -> None:
    assert make_patch("same\n", "same\n", "e2e/x.spec.js") == ""


def test_make_patch_round_trip_over_all_golden_fixtures() -> None:
    """``apply_patch(old, make_patch(old, new)) == new`` — the §19 S4.2 oracle."""
    checked = 0
    for fixture in GOLDEN.fixtures:
        if not fixture.has_fix:
            continue
        checked += 1
        fixed_code = fixture.fixed_code
        assert fixed_code is not None  # has_fix ⇒ fixed_code (golden contract)
        patch = make_patch(fixture.test_code, fixed_code, fixture.file_path)
        assert patch, fixture.id
        assert apply_patch(fixture.test_code, patch) == fixed_code, fixture.id
    assert checked == 8


def test_make_patch_git_shape_and_context_lines() -> None:
    old = "a\nb\nc\nd\ne\nf\n"
    new = "a\nB\nc\nd\ne\nf\n"
    lines = make_patch(old, new, "e2e/x.spec.js").splitlines()
    assert lines[0] == "--- a/e2e/x.spec.js"
    assert lines[1] == "+++ b/e2e/x.spec.js"
    assert any(line.startswith("@@ ") for line in lines)
    assert " a" in lines and " c" in lines and " d" in lines  # context preserved


def test_apply_patch_rejects_empty_patch() -> None:
    with pytest.raises(PatchError, match="empty patch"):
        apply_patch("a\n", "")
    with pytest.raises(PatchError, match="empty patch"):
        apply_patch("a\n", "   \n  ")


def test_apply_patch_rejects_hunkless_patch() -> None:
    with pytest.raises(PatchError, match="no @@ hunks"):
        apply_patch("a\n", "not a unified diff at all")


def test_apply_patch_rejects_unlocatable_hunk() -> None:
    patch = "@@ -1,3 +1,3 @@\n context that is\n-wrong line\n+new line\n not in the file"
    with pytest.raises(PatchError, match="does not apply"):
        apply_patch("a\nb\nc\n", patch)


def test_apply_patch_tolerates_context_drift() -> None:
    """Trailing-whitespace drift on context lines still applies."""
    original = "a\nb\nc\n"
    patch = "@@ -1,3 +1,3 @@\na   \nb\t\n-c\n+c2"
    assert apply_patch(original, patch) == "a\nb\nc2\n"


def test_apply_patch_insertion_hunk_uses_header_anchor() -> None:
    original = "a\nb\nc\n"
    patch = "@@ -2,0 +3 @@\n+INSERT"
    assert apply_patch(original, patch) == "a\nINSERT\nb\nc\n"


def test_apply_patch_context_only_hunk_is_noop() -> None:
    assert apply_patch("a\nb\n", "@@ -1,2 +1,2 @@\n a\n b") == "a\nb\n"


# --- FixerAgent (fake transport) ----------------------------------------------


def _sample_fix_input(app_context: str | None = None) -> FixerInput:
    normalized = normalize_failure("Test failed: locator resolved to 4 elements")
    diagnosis = Diagnosis(
        category=FailureCategory.AUTOMATION_DEFECT,
        root_cause="stale_locator",
        confidence=0.9,
        evidence=["locator resolved to 4 elements"],
        suggested_fix="scope the locator",
    )
    return FixerInput(
        failure=normalized,
        diagnosis=diagnosis,
        file_path="e2e/cart.spec.js",
        test_code="test('adds to cart', async ({ page }) => {})",
        app_context=app_context,
    )


def test_agent_returns_proposal_and_audit() -> None:
    store = InMemoryPromptStore([FIX_SPEC])
    gateway = _gateway(
        lambda request: httpx.Response(200, json=_assistant(json.dumps(VALID_PATCH)))
    )
    agent = FixerAgent(store, gateway)
    try:
        result = asyncio.run(agent.run(_sample_fix_input()))
    finally:
        asyncio.run(gateway.aclose())
    assert result.proposal.action == "patch"
    assert result.proposal.needs_human_approval is True
    assert result.prompt_ref == "fix-agent@1"
    assert result.call.usage.tokens_in == 40
    assert result.call.usage.tokens_out == 210
    assert result.call.latency_ms >= 0


def test_agent_prompt_carries_diagnosis_and_test_code() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json=_assistant(json.dumps(VALID_PATCH)))

    store = InMemoryPromptStore([FIX_SPEC])
    gateway = _gateway(handler)
    agent = FixerAgent(store, gateway)
    try:
        asyncio.run(agent.run(_sample_fix_input()))
    finally:
        asyncio.run(gateway.aclose())
    rendered = seen[0]
    assert "stale_locator" in rendered
    assert "scope the locator" in rendered
    assert "e2e/cart.spec.js" in rendered
    assert "test('adds to cart'" in rendered
    assert "{{" not in rendered


def test_agent_app_context_fallback_when_absent() -> None:
    """No app context → the prompt renders the explicit fallback line."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json=_assistant(json.dumps(VALID_PATCH)))

    store = InMemoryPromptStore([FIX_SPEC])
    gateway = _gateway(handler)
    agent = FixerAgent(store, gateway)
    try:
        asyncio.run(agent.run(_sample_fix_input()))
    finally:
        asyncio.run(gateway.aclose())
    assert "Not available for this run" in seen[0]


def test_agent_app_context_reaches_prompt() -> None:
    """A supplied app context lands in the rendered prompt."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return httpx.Response(200, json=_assistant(json.dumps(VALID_PATCH)))

    store = InMemoryPromptStore([FIX_SPEC])
    gateway = _gateway(handler)
    agent = FixerAgent(store, gateway)
    try:
        asyncio.run(agent.run(_sample_fix_input(app_context="### client/src/testids.js\nX=1;")))
    finally:
        asyncio.run(gateway.aclose())
    assert "### client/src/testids.js" in seen[0]


def test_agent_invalid_model_output_raises_value_error() -> None:
    store = InMemoryPromptStore([FIX_SPEC])
    gateway = _gateway(
        # Valid JSON, but it violates the fix-proposal schema.
        lambda request: httpx.Response(200, json=_assistant(json.dumps({"action": "refactor"})))
    )
    agent = FixerAgent(store, gateway)
    try:
        with pytest.raises(ValueError, match="schema validation"):
            asyncio.run(agent.run(_sample_fix_input()))
    finally:
        asyncio.run(gateway.aclose())


def test_agent_prompt_not_found_raises() -> None:
    gateway = _gateway(
        lambda request: httpx.Response(200, json=_assistant(json.dumps(VALID_PATCH)))
    )
    agent = FixerAgent(InMemoryPromptStore(), gateway)
    try:
        with pytest.raises(PromptNotFound):
            asyncio.run(agent.run(_sample_fix_input()))
    finally:
        asyncio.run(gateway.aclose())


# --- run_fix_eval (the gate) ---------------------------------------------------


def test_oracle_model_passes_the_gate() -> None:
    report = _run(_oracle_handler, _oracle_verifier)
    assert report.passed is True
    assert report.agent == "fix-agent"
    assert report.fixer_prompt_ref == "fix-agent@1"
    assert report.golden_fixtures == 10
    assert report.targets == {"passing_min": 0.5}
    assert report.totals.fixtures == 10
    assert report.totals.passed == 8
    assert report.totals.failed == 2
    assert report.totals.passing_fraction == 0.8
    assert report.totals.applicable == 8
    assert report.totals.declined == 2
    assert report.totals.correct_action == 10
    by_id = {result.fixture_id: result for result in report.fixtures}
    for fixture in GOLDEN.fixtures:
        result = by_id[fixture.id]
        assert result.error is None, (fixture.id, result.error)
        assert result.correct_action is True
        if fixture.has_fix:
            assert result.action == "patch"
            assert result.applicable is True
            assert result.passing is True
        else:
            assert result.action == "decline"
            assert result.applicable is None
            assert result.passing is None


def test_verifier_rejection_fails_the_gate() -> None:
    report = _run(_oracle_handler, _failing_verifier)
    assert report.passed is False
    assert report.totals.passed == 0
    assert report.totals.passing_fraction == 0.0
    assert report.totals.correct_action == 10  # actions right — fixes just didn't pass
    for result in report.fixtures:
        if result.expected_action == "patch":
            assert result.passing is False
            assert "verifier" in (result.error or "")


def test_wrong_action_fails_the_gate() -> None:
    report = _run(_decliner_handler, _oracle_verifier)
    assert report.passed is False
    assert report.totals.passed == 0
    assert report.totals.declined == 10
    assert report.totals.correct_action == 2  # only the 2 true declines
    by_id = {result.fixture_id: result for result in report.fixtures}
    for fixture in GOLDEN.fixtures:
        result = by_id[fixture.id]
        if fixture.has_fix:
            assert result.correct_action is False
            assert result.action == "decline"
        else:
            assert result.correct_action is True


def test_targets_override_loosens_the_gate() -> None:
    golden = GOLDEN.model_copy(update={"targets": FixTargets(passing_min=0.0)})
    report = _run(_decliner_handler, _oracle_verifier, golden=golden)
    assert report.passed is True
    assert report.totals.passed == 0
    assert report.targets == {"passing_min": 0.0}


def test_failure_isolated_to_its_fixture() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # First call (first fixture's investigator): schema-invalid prose.
            return httpx.Response(200, json=_assistant("I am not a model."))
        return _oracle_handler(request)

    report = _run(handler, _oracle_verifier)
    first = report.fixtures[0]
    assert first.error is not None
    assert first.action is None
    assert first.correct_action is False
    assert report.totals.fixtures == 10
    assert report.totals.passed == 7


def test_run_fix_eval_forwards_app_context() -> None:
    """The runner passes the app context into every fixer prompt (v2)."""
    seen: list[str] = []

    def fixer_handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][0]["content"])
        return _oracle_handler(request)

    inv_gateway = _gateway(_oracle_handler)
    fix_gateway = _gateway(fixer_handler)
    investigator = FailureInvestigatorAgent(InMemoryPromptStore([INV_SPEC]), inv_gateway)
    fixer = FixerAgent(InMemoryPromptStore([FIX_SPEC]), fix_gateway)

    async def _go() -> FixEvalReport:
        try:
            return await run_fix_eval(
                GOLDEN,
                investigator=investigator,
                fixer=fixer,
                model=MODEL,
                fixer_prompt_ref="fix-agent@1",
                verifier=_oracle_verifier,
                app_context="APP-CTX-MARKER",
            )
        finally:
            await inv_gateway.aclose()
            await fix_gateway.aclose()

    report = asyncio.run(_go())
    assert report.totals.fixtures == 10
    assert len(seen) == 10  # every fixture reached the fixer
    assert all("APP-CTX-MARKER" in content for content in seen)


# --- the fix golden set (S3.3 → S4.2 contract) ---------------------------------


def test_golden_set_shape_and_s33_contract() -> None:
    assert len(GOLDEN.fixtures) == 10
    assert len({fixture.id for fixture in GOLDEN.fixtures}) == 10
    fixable = [fixture for fixture in GOLDEN.fixtures if fixture.has_fix]
    declined = [fixture for fixture in GOLDEN.fixtures if not fixture.has_fix]
    assert len(fixable) == 8
    assert len(declined) == 2
    for fixture in GOLDEN.fixtures:
        assert fixture.id.startswith("FIX-")
        # The raw failure text must normalize to the declared category —
        # the runner's investigator prior comes from S3.3.
        assert normalize_failure(fixture.failure).category == fixture.category, fixture.id
        assert fixture.test_code.strip(), fixture.id
    for fixture in declined:
        assert fixture.category in (
            FailureCategory.PRODUCT_DEFECT,
            FailureCategory.ENVIRONMENT_DEFECT,
        ), fixture.id
        assert fixture.fixed_code is None, fixture.id
    assert GOLDEN.targets.passing_min == 0.5


# --- CLI contract ---------------------------------------------------------------


def test_cli_requires_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert fixer_cli.main([]) == 2


def test_cli_rejects_bad_timeout() -> None:
    argv = ["--base-url", "http://x", "--model", "m", "--timeout", "0"]
    assert fixer_cli.main(argv) == 2


def test_cli_missing_golden_exits_2(tmp_path: pathlib.Path) -> None:
    argv = [
        "--base-url",
        "http://x",
        "--model",
        "m",
        "--golden",
        str(tmp_path / "nope.json"),
    ]
    assert fixer_cli.main(argv) == 2


def test_cli_rejects_unusable_demo_app(tmp_path: pathlib.Path) -> None:
    argv = ["--base-url", "http://x", "--model", "m", "--demo-app", str(tmp_path)]
    assert fixer_cli.main(argv) == 2


def test_emit_writes_json_report_and_summary(tmp_path: pathlib.Path) -> None:
    report = _run(_oracle_handler, _oracle_verifier)
    out, err = io.StringIO(), io.StringIO()
    code = fixer_cli._emit(report, report_path=tmp_path / "report.json", stdout=out, stderr=err)
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["passed"] is True
    assert payload["totals"]["passed"] == 8
    assert len(payload["fixtures"]) == 10
    assert "PASSED (exit 0)" in err.getvalue()
    on_disk = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert on_disk == payload


def test_emit_failing_report_exits_1() -> None:
    report = _run(_decliner_handler, _oracle_verifier)
    out, err = io.StringIO(), io.StringIO()
    code = fixer_cli._emit(report, report_path=None, stdout=out, stderr=err)
    assert code == 1
    assert "FAILED (exit 1)" in err.getvalue()
    # Every failing fixture is named in the summary with its reason.
    for fixture in report.fixtures:
        if not (fixture.passing and fixture.correct_action):
            assert fixture.fixture_id in err.getvalue()


# --- end-to-end CLI (in-process fake LLM + oracle verifier) ---------------------


class _OracleVerifier:
    """Stands in for the live Playwright verifier (no browser in unit tests)."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, fixture: FixFixture, patched: str) -> bool:
        self.calls += 1
        return patched.strip() == (fixture.fixed_code or "").strip()

    async def aclose(self) -> None:
        pass


def _make_fake_demo_app(directory: pathlib.Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "playwright.config.js").write_text("module.exports = {};\n", encoding="utf-8")
    src = directory / "client" / "src"
    src.mkdir(parents=True)
    (src / "testids.js").write_text(
        "export const TESTIDS = { loginForm: 'login-form' };\n", encoding="utf-8"
    )
    cli_dir = directory / "node_modules" / "@playwright" / "test"
    cli_dir.mkdir(parents=True)
    (cli_dir / "cli.js").write_text("console.log('fake playwright cli');\n", encoding="utf-8")


def _start_fake_server(
    reply_fn: Callable[[str], dict[str, object]],
) -> tuple[str, ThreadingHTTPServer]:
    """In-process OpenAI-compatible server answering from the real prompts."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — http.server API
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            content = body["messages"][0]["content"]
            payload = json.dumps(_assistant(json.dumps(reply_fn(content)))).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}", server


def _run_cli_e2e(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    captured: list[str],
) -> tuple[int, _OracleVerifier]:
    """One full CLI run: fake LLM server, oracle replies, fake demo app."""

    def reply(content: str) -> dict[str, object]:
        captured.append(content)
        return _oracle_reply(
            content, fixer_marker=FIXER_MARKER_REAL, investigator_pattern=INV_CATEGORY_REAL
        )

    base_url, server = _start_fake_server(reply)
    try:
        demo = tmp_path / "demo-app"
        _make_fake_demo_app(demo)
        verifier = _OracleVerifier()
        monkeypatch.setattr(fixer_cli, "PlaywrightVerifier", lambda path: verifier)
        report_path = tmp_path / "reports" / "fixer.json"
        code = fixer_cli.main(
            [
                "--base-url",
                base_url,
                "--model",
                MODEL,
                "--demo-app",
                str(demo),
                "--report",
                str(report_path),
                "--timeout",
                "30",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
    return code, verifier


def _fixer_prompts(captured: list[str]) -> list[str]:
    return [content for content in captured if FIXER_MARKER_REAL in content]


def test_cli_end_to_end_passes_and_writes_report(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []
    code, verifier = _run_cli_e2e(tmp_path, monkeypatch, captured)
    assert code == 0
    payload = json.loads((tmp_path / "reports" / "fixer.json").read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["fixer_prompt_ref"] == "fix-agent@2"
    assert payload["totals"]["passed"] == 8
    assert payload["totals"]["correct_action"] == 10
    assert len(payload["fixtures"]) == 10
    assert verifier.calls == 8  # only the 8 patched fixtures are verified
    # The v2 prompt carries the read-only app context (the fake app's test-ids).
    fixer_prompts = _fixer_prompts(captured)
    assert len(fixer_prompts) == 10
    assert "### client/src/testids.js" in fixer_prompts[0]


def test_cli_app_context_opt_out_via_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FIXER_NO_APP_CONTEXT", "1")
    captured: list[str] = []
    code, _ = _run_cli_e2e(tmp_path, monkeypatch, captured)
    assert code == 0
    fixer_prompts = _fixer_prompts(captured)
    assert len(fixer_prompts) == 10
    assert "### client/src/testids.js" not in fixer_prompts[0]
    assert "Not available for this run" in fixer_prompts[0]
