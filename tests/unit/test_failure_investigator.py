"""S4.1 — Failure Investigator (agent + eval runner + CLI). Offline test suite.

Covers the S4.1 exit criteria (build bible §19 S4.1, §22, §31.7):

- the §12 diagnosis parser — valid JSON (with or without stray prose/fences)
  validates into a :class:`Diagnosis`; invalid category, out-of-range
  confidence, empty evidence, or no JSON at all fail loud (``ValueError``);
- the agent against a fake ``httpx`` transport (same pattern as
  ``tests/unit/test_test_design_agent.py``): the rendered prompt carries the
  S3.3 normalized shape (category / signals / evidence / http_status /
  selector / endpoint), the result is diagnosis + audit payload +
  ``failure-investigator@2`` (latest registered); invalid model output and
  a missing prompt fail loud;
- a full run over the **30-fixture failure golden set**
  (``packages/execution/golden/failure_v1.json``) with an *oracle* fake
  model (echoes the normalizer's suggested category → 100% top-1) and a
  *dumb* model (always ``unknown`` → 2/30) — the §31.7 gate
  (``top1_min`` = 0.8) met / missed;
- failure isolation — a schema-invalid output or an LLM error fails *its*
  fixtures and only those; the run continues and the report is still
  produced;
- the CLI contract — JSON report on stdout (and ``--report``), human
  summary on stderr, exit ``0`` (targets met) / ``1`` (targets missed) /
  ``2`` (configuration error). The two end-to-end CLI tests run against an
  in-process OpenAI-compatible HTTP server that answers from the *real*
  registered prompt — no real LLM, no network.
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
from qa_copilot_ai import (
    Diagnosis,
    FailureInvestigatorAgent,
    InMemoryPromptStore,
    LLMGateway,
    PromptNotFound,
    PromptSpec,
)
from qa_copilot_ai.agents import InvestigatorInput, parse_diagnosis
from qa_copilot_ai.investigator import InvestigationReport, run_investigation_eval
from qa_copilot_ai.investigator import cli as investigator_cli
from qa_copilot_ai.prompts import FilePromptStore, render_prompt
from qa_copilot_domain import FailureCategory, NormalizedFailure
from qa_copilot_execution.failure import normalize_failure
from qa_copilot_execution.golden import (
    FailureExpectations,
    FailureFixture,
    FailureGoldenSet,
    FailureGoldenSource,
    FailureTargets,
    default_golden_path,
    load_failure_golden_set,
)

# The same spec shape the real prompt file (failure-investigator.v2.md) has,
# with the variable lines the agent renders — good enough for the fake-model
# tests to read the normalized shape back out of the rendered prompt.
PROMPT_SPEC = PromptSpec(
    name="failure-investigator",
    version=1,
    body=(
        "Investigate the failure.\n"
        "Suggested category (best guess): {{category}}\n"
        "Detected signals: {{signals}}\n"
        "Captured evidence (raw lines):\n"
        "{{evidence}}\n"
        "HTTP status: {{http_status}}\n"
        "Failing selector: {{selector}}\n"
        "Request endpoint: {{endpoint}}\n"
        "Answer with one JSON object."
    ),
    model_class="coder",
    input_budget=8000,
    output_budget=4096,
    schema_ref="failure-analysis/v1",
    temperature=0.3,
)

VALID_DIAGNOSIS = {
    "category": "product_defect",
    "root_cause": "server_error_on_checkout",
    "confidence": 0.9,
    "evidence": ["GET /api/checkout 500", "Error: expected 200, got 500"],
    "suggested_fix": "reproduce against the API and file a product defect",
    "needs_human_approval": True,
}

GOLDEN = load_failure_golden_set(default_golden_path())

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "packages" / "ai" / "prompts"


def _diagnosis(category: str = "product_defect", **overrides: object) -> dict[str, object]:
    payload = dict(VALID_DIAGNOSIS)
    payload["category"] = category
    payload.update(overrides)
    return payload


def _assistant(payload: dict[str, object] | str) -> dict[str, object]:
    """One OpenAI-style chat-completion response body."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
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


def _investigator(handler: Handler) -> FailureInvestigatorAgent:
    return FailureInvestigatorAgent(InMemoryPromptStore([PROMPT_SPEC]), _gateway(handler))


def _prompt_text(request: httpx.Request) -> str:
    body = json.loads(request.content)
    return str(body["messages"][0]["content"])


def _suggested_category(request: httpx.Request) -> str:
    """Read the normalizer's suggested category back out of the rendered prompt."""
    match = re.search(r"Suggested category \(best guess\):\s*(\w+)", _prompt_text(request))
    assert match is not None, "rendered prompt has no suggested category"
    return match.group(1)


def _oracle_handler(request: httpx.Request) -> httpx.Response:
    """A competent model: echoes the normalizer's suggested category (100% top-1)."""
    return httpx.Response(200, json=_assistant(_diagnosis(_suggested_category(request))))


def _normalized_fixture(raw: str, category: str, **context: object) -> NormalizedFailure:
    """A normalized failure shaped like the S3.3 normalizer's output."""
    return NormalizedFailure(
        category=FailureCategory(category),
        category_signals=["test.rule"],
        evidence=[raw.splitlines()[0]] if raw else ["(no lines captured)"],
        **context,  # type: ignore[arg-type]
    )


async def _run(handler: Handler, golden: FailureGoldenSet = GOLDEN) -> InvestigationReport:
    agent = _investigator(handler)
    try:
        return await run_investigation_eval(
            golden, agent=agent, model="fake-model", prompt_ref="failure-investigator@1"
        )
    finally:
        await agent._gateway.aclose()


def _run_sync(handler: Handler, golden: FailureGoldenSet = GOLDEN) -> InvestigationReport:
    return asyncio.run(_run(handler, golden))


# --------------------------------------------------------------------------
# registered prompt file (packages/ai/prompts/failure-investigator.vN.md)
# --------------------------------------------------------------------------


def test_prompt_file_registered() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("failure-investigator")
    assert spec.ref == "failure-investigator@2"
    assert spec.model_class == "coder"
    assert spec.temperature == 0.3
    assert spec.input_budget == 60000
    assert spec.output_budget == 40000
    assert spec.schema_ref == "failure-analysis/v1"
    for variable in ("category", "signals", "evidence", "http_status", "selector", "endpoint"):
        assert "{{" + variable + "}}" in spec.body


def test_prompt_renders_normalized_shape() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get("failure-investigator")
    fixture = GOLDEN.fixtures[0]
    nf = normalize_failure(fixture.raw)
    rendered = render_prompt(
        spec,
        category=nf.category.value,
        signals=", ".join(nf.category_signals),
        evidence="\n".join(nf.evidence),
        http_status=str(nf.http_status) if nf.http_status is not None else "",
        selector=nf.selector or "",
        endpoint=nf.endpoint or "",
    )
    # No placeholder may survive rendering; the normalized shape is carried.
    assert "{{" not in rendered
    assert nf.category.value in rendered
    if nf.http_status is not None:
        assert str(nf.http_status) in rendered


# --------------------------------------------------------------------------
# §12 diagnosis parser
# --------------------------------------------------------------------------


def test_parse_diagnosis_valid_json() -> None:
    d = parse_diagnosis(json.dumps(VALID_DIAGNOSIS))
    assert isinstance(d, Diagnosis)
    assert d.category == FailureCategory.PRODUCT_DEFECT
    assert d.root_cause == "server_error_on_checkout"
    assert d.confidence == 0.9
    assert d.evidence == ["GET /api/checkout 500", "Error: expected 200, got 500"]
    assert d.suggested_fix == "reproduce against the API and file a product defect"
    assert d.needs_human_approval is True


def test_parse_diagnosis_defaults_needs_human_approval_to_true() -> None:
    payload = _diagnosis()
    del payload["needs_human_approval"]
    d = parse_diagnosis(json.dumps(payload))
    assert d.needs_human_approval is True  # v1 never auto-heals (§26)


@pytest.mark.parametrize(
    "text",
    [
        "Here is the diagnosis:\n```json\n"
        + json.dumps(VALID_DIAGNOSIS)
        + "\n```\nHope that helps!",
        json.dumps(VALID_DIAGNOSIS) + "\n\n(End of analysis.)",
    ],
)
def test_parse_diagnosis_tolerates_prose_and_fences(text: str) -> None:
    d = parse_diagnosis(text)
    assert d.category == FailureCategory.PRODUCT_DEFECT
    assert len(d.evidence) == 2


def test_parse_diagnosis_no_json_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        parse_diagnosis("I am not sure, please check the logs manually.")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("category", "banana"),  # not a §16 failure category
        ("confidence", 1.5),  # out of [0, 1]
        ("confidence", -0.1),
        ("evidence", []),  # at least one evidence line
        ("root_cause", ""),
    ],
    ids=["bad-category", "confidence-high", "confidence-low", "empty-evidence", "empty-root-cause"],
)
def test_parse_diagnosis_schema_violations_raise(field: str, value: object) -> None:
    payload = dict(VALID_DIAGNOSIS)
    payload[field] = value
    with pytest.raises(ValueError, match="schema validation"):
        parse_diagnosis(json.dumps(payload))


def test_parse_diagnosis_missing_fields_raise() -> None:
    for missing in ("category", "root_cause", "confidence", "evidence", "suggested_fix"):
        payload = dict(VALID_DIAGNOSIS)
        del payload[missing]
        with pytest.raises(ValueError, match="schema validation"):
            parse_diagnosis(json.dumps(payload))


# --------------------------------------------------------------------------
# agent (fake transport)
# --------------------------------------------------------------------------


def test_agent_returns_diagnosis_and_audit() -> None:
    normalized = _normalized_fixture(
        "Error: expected 200, got 500\nGET /api/checkout 500", "product_defect"
    )
    agent = _investigator(lambda _req: httpx.Response(200, json=_assistant(VALID_DIAGNOSIS)))
    try:
        result = asyncio.run(agent.run(InvestigatorInput(normalized=normalized)))
    finally:
        asyncio.run(agent._gateway.aclose())

    assert result.diagnosis.category == FailureCategory.PRODUCT_DEFECT
    assert result.diagnosis.evidence == VALID_DIAGNOSIS["evidence"]
    assert result.call.usage.tokens_in == 40
    assert result.call.usage.tokens_out == 210
    assert result.call.latency_ms >= 0
    assert result.prompt_ref == "failure-investigator@1"


def test_agent_prompt_carries_normalized_shape() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_assistant(VALID_DIAGNOSIS))

    normalized = _normalized_fixture(
        "Error: expected 200, got 500", "product_defect", http_status=500, endpoint="/api/checkout"
    )
    agent = _investigator(capture)
    try:
        asyncio.run(agent.run(InvestigatorInput(normalized=normalized)))
    finally:
        asyncio.run(agent._gateway.aclose())

    content = _prompt_text(seen[0])
    assert "Suggested category (best guess): product_defect" in content
    assert "Error: expected 200, got 500" in content
    assert "HTTP status: 500" in content
    assert "Request endpoint: /api/checkout" in content
    body = json.loads(seen[0].content)
    assert body["model"] == "fake-model"


def test_agent_invalid_model_output_raises_value_error() -> None:
    agent = _investigator(lambda _req: httpx.Response(200, json=_assistant("no JSON here at all")))
    try:
        normalized = _normalized_fixture("Error: boom", "unknown")
        with pytest.raises(ValueError, match="no JSON object"):
            asyncio.run(agent.run(InvestigatorInput(normalized=normalized)))
    finally:
        asyncio.run(agent._gateway.aclose())


def test_agent_prompt_not_found_raises() -> None:
    store = InMemoryPromptStore([])
    gateway = _gateway(lambda _r: httpx.Response(200))
    agent = FailureInvestigatorAgent(store, gateway)
    try:
        normalized = _normalized_fixture("Error: boom", "unknown")
        with pytest.raises(PromptNotFound):
            asyncio.run(agent.run(InvestigatorInput(normalized=normalized)))
    finally:
        asyncio.run(gateway.aclose())


# --------------------------------------------------------------------------
# eval runner over the 30-fixture golden set
# --------------------------------------------------------------------------


def test_oracle_model_meets_top1_gate() -> None:
    report = _run_sync(_oracle_handler)
    assert report.model == "fake-model"
    assert report.prompt_ref == "failure-investigator@1"
    assert report.golden_fixtures == 30
    assert report.totals.fixtures == 30
    assert report.totals.passed == 30
    assert report.totals.failed == 0
    assert report.totals.top1_fraction == 1.0
    assert report.totals.schema_valid_fraction == 1.0
    assert report.targets == {"top1_min": 0.8}  # §31.7 gate from the golden set
    assert report.passed is True
    for fixture in report.fixtures:
        assert fixture.passed is True
        assert fixture.correct is True
        assert fixture.schema_valid is True
        assert fixture.category == fixture.suggested  # oracle echoes the suggestion
        assert fixture.error is None
    # the report serializes (CLI / --report path) and each fixture carries its diagnosis
    dumped = json.loads(report.model_dump_json())
    assert dumped["passed"] is True
    assert dumped["totals"]["top1_fraction"] == 1.0
    assert all(fx["category"] in {c.value for c in FailureCategory} for fx in dumped["fixtures"])


def test_dumb_model_misses_top1_gate() -> None:
    def always_unknown(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant(_diagnosis("unknown", confidence=0.2)))

    report = _run_sync(always_unknown)
    unknown_fixtures = [f for f in GOLDEN.fixtures if f.expect.category is FailureCategory.UNKNOWN]
    assert len(unknown_fixtures) == 2  # FAIL-029 / FAIL-030
    assert report.totals.fixtures == 30
    assert report.totals.passed == 2  # only the two expected-unknown fixtures
    assert report.totals.top1_fraction == pytest.approx(2 / 30)
    assert report.totals.schema_valid_fraction == 1.0  # the model was *valid*, just wrong
    assert report.passed is False  # 0.0667 < top1_min 0.8


def test_schema_invalid_fixture_fails_only_itself() -> None:
    # The runner iterates golden.fixtures in order — fail exactly FAIL-013's call.
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if GOLDEN.fixtures[calls["n"] - 1].id == "FAIL-013":
            return httpx.Response(200, json=_assistant("cannot decide, needs human review"))
        return _oracle_handler(request)

    report = _run_sync(handler)
    assert calls["n"] == 30  # the run continued past the bad fixture
    bad = next(f for f in report.fixtures if not f.passed)
    assert bad.fixture_id == "FAIL-013"
    assert bad.schema_valid is False
    assert bad.error is not None
    assert bad.category is None
    assert report.totals.passed == 29
    assert report.totals.schema_valid_fraction == pytest.approx(29 / 30)
    assert report.totals.top1_fraction == pytest.approx(29 / 30)  # 0.9667 ≥ 0.8 → still passes
    assert report.passed is True


def test_llm_error_fixtures_fail_only_themselves() -> None:
    # Fail exactly the calls for FAIL-001 and FAIL-002 (2 of 30 → gate still met).
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if GOLDEN.fixtures[calls["n"] - 1].id in ("FAIL-001", "FAIL-002"):
            return httpx.Response(500, json={"error": "upstream exploded"})
        return _oracle_handler(request)

    report = _run_sync(handler)
    assert calls["n"] == 30
    bad_ids = {f.fixture_id for f in report.fixtures if not f.passed}
    assert bad_ids == {"FAIL-001", "FAIL-002"}
    for bad in report.fixtures:
        if not bad.passed:
            assert bad.schema_valid is False
            assert bad.error is not None
            assert bad.category is None
    assert report.totals.passed == 28
    assert report.totals.schema_valid_fraction == pytest.approx(28 / 30)
    assert report.passed is True  # 0.9333 ≥ 0.8


def test_gate_threshold_comes_from_golden_targets() -> None:
    def product_set(top1_min: float) -> FailureGoldenSet:
        return FailureGoldenSet(
            name="failure-golden-set",
            version="v1",
            description="mini set for the gate test",
            source=FailureGoldenSource(build_bible="docs/build-bible.md"),
            targets=FailureTargets(normalize_pass_min=1.0, top1_min=top1_min),
            fixtures=[
                FailureFixture(
                    id=f"FAIL-90{i}",
                    title="boom",
                    raw="Error: 500 Internal Server Error",
                    expect=FailureExpectations(category=FailureCategory.PRODUCT_DEFECT),
                )
                for i in range(3)
            ],
        )

    def always_unknown(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant(_diagnosis("unknown", confidence=0.1)))

    # Same 0/3 top-1, different gates from the golden targets:
    missed = _run_sync(always_unknown, product_set(0.8))
    assert missed.totals.top1_fraction == pytest.approx(0.0)
    assert missed.passed is False  # 0.0 < 0.8

    met = _run_sync(always_unknown, product_set(0.0))
    assert met.passed is True  # 0.0 >= 0.0 — the gate is whatever the golden set says


# --------------------------------------------------------------------------
# CLI — exit codes, stdout/stderr contract, --report
# --------------------------------------------------------------------------


def test_cli_without_endpoint_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    code = investigator_cli.main([])
    assert code == 2
    assert "LLM_BASE_URL" in capsys.readouterr().err


def test_cli_bad_timeout_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    code = investigator_cli.main(["--timeout", "0"])
    assert code == 2
    assert "--timeout" in capsys.readouterr().err


def test_cli_missing_golden_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MODEL", "fake-model")
    code = investigator_cli.main(["--golden", str(tmp_path / "nope.json")])
    assert code == 2
    assert "golden set not found" in capsys.readouterr().err


def test_emit_writes_json_report_and_summary(tmp_path: pathlib.Path) -> None:
    passing = _run_sync(_oracle_handler)
    failing = _run_sync(lambda _r: httpx.Response(200, json=_assistant(_diagnosis("unknown"))))

    out, err = io.StringIO(), io.StringIO()
    report_path = tmp_path / "nested" / "report.json"
    code = investigator_cli._emit(passing, report_path=report_path, stdout=out, stderr=err)
    assert code == 0
    stdout_json = json.loads(out.getvalue())
    assert stdout_json["passed"] is True
    assert stdout_json["totals"]["top1_fraction"] == 1.0
    assert "PASSED (exit 0)" in err.getvalue()
    # --report file: same JSON payload as stdout
    assert report_path.read_text(encoding="utf-8") == out.getvalue()

    out2, err2 = io.StringIO(), io.StringIO()
    code2 = investigator_cli._emit(failing, report_path=None, stdout=out2, stderr=err2)
    assert code2 == 1
    assert json.loads(out2.getvalue())["passed"] is False
    assert "FAILED (exit 1)" in err2.getvalue()
    # per-fixture reasons on stderr for the failed ones
    assert "FAIL-001" in err2.getvalue()
    assert "unknown" in err2.getvalue()


def _start_fake_server(
    reply: Callable[[str], dict[str, object]],
) -> tuple[ThreadingHTTPServer, str]:
    """In-process OpenAI-compatible /chat/completions server (no real LLM)."""

    def make_handler_class() -> type[BaseHTTPRequestHandler]:
        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length))
                content = str(body["messages"][0]["content"])
                response = json.dumps(_assistant(reply(content))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args: object) -> None:
                return

        return _Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler_class())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def _oracle_reply(content: str) -> dict[str, object]:
    """Read the suggested category out of the *real* rendered prompt and echo it."""
    match = re.search(r"Suggested category \(strong prior\):\s*(\w+)", content)
    assert match is not None, f"no suggested category in real prompt: {content[:160]!r}"
    return _diagnosis(match.group(1))


def test_cli_end_to_end_passes_and_writes_report(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    server, base_url = _start_fake_server(_oracle_reply)
    try:
        report_path = tmp_path / "investigator_report.json"
        code = investigator_cli.main(
            ["--base-url", base_url, "--model", "fake-model", "--report", str(report_path)]
        )
    finally:
        server.shutdown()

    out = capsys.readouterr()
    assert code == 0
    assert "PASSED (exit 0)" in out.err
    stdout_json = json.loads(out.out)
    assert stdout_json["passed"] is True
    assert stdout_json["totals"]["top1_fraction"] == 1.0
    assert stdout_json["golden_fixtures"] == 30
    assert report_path.read_text(encoding="utf-8") == out.out


def test_cli_end_to_end_targets_missed_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    def dumb_reply(_content: str) -> dict[str, object]:
        return _diagnosis("unknown", confidence=0.1)

    server, base_url = _start_fake_server(dumb_reply)
    try:
        code = investigator_cli.main(["--base-url", base_url, "--model", "fake-model"])
    finally:
        server.shutdown()

    out = capsys.readouterr()
    assert code == 1
    assert "FAILED (exit 1)" in out.err
    stdout_json = json.loads(out.out)
    assert stdout_json["passed"] is False
    assert stdout_json["totals"]["passed"] == 2  # the two expected-unknown fixtures
