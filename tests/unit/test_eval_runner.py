"""S1.4 — golden set eval (``qa_copilot_ai.eval``) — offline (no network).

Covers the S1.4 exit criteria (build bible §19 S1.4, §31.7):

- the golden set loader — a valid file loads, a missing file and an invalid
  schema/JSON fail loud (``GoldenSetError``);
- a full golden run over a fake ``httpx`` transport (same pattern as
  ``tests/unit/test_test_design_agent.py``): per-fixture results, §31.7
  scoring, and the stable report shape;
- failure isolation — a schema-invalid output or an LLM error fails its
  fixture and *only* its fixture; the run continues and still reports;
- the CLI contract — JSON report on stdout (and ``--report``), human
  summary on stderr, exit ``0`` (targets met) / ``1`` (targets missed) /
  ``2`` (configuration error). The CLI end-to-end tests use an in-process
  OpenAI-compatible HTTP server — still no real LLM.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from qa_copilot_ai import (
    InMemoryPromptStore,
    LLMGateway,
    PromptSpec,
    TestDesignAgent,
)
from qa_copilot_ai.eval import (
    EvaluationReport,
    GoldenSetError,
    default_golden_path,
    load_golden_set,
    run_test_design_eval,
    step_coverage,
)
from qa_copilot_ai.eval import cli as eval_cli

# The same spec the S1.2 tests use — the runner is prompt-agnostic here.
PROMPT_SPEC = PromptSpec(
    name="test-designer",
    version=1,
    body=(
        "Design test cases for the requirement: {{title}} | {{content}} | "
        "criteria: {{acceptance_criteria}} | analysis: {{analysis}}"
    ),
    model_class="coder",
    input_budget=8000,
    output_budget=4096,
    schema_ref="test-suite/v1",
    temperature=0.3,
)


def _assistant(payload: dict[str, object]) -> dict[str, object]:
    return {
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload)}}],
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


# --- Golden data under test --------------------------------------------------

GOLDEN = load_golden_set(default_golden_path())

GOLDEN_SUITES: dict[str, list[dict[str, object]]] = {
    fixture.title: [case.model_dump() for case in fixture.suite.test_cases]
    for fixture in GOLDEN.fixtures
}


def _title_from(request: httpx.Request) -> str:
    """The fixture title, read back out of the rendered prompt (S1.2 pattern)."""
    body = json.loads(request.content)
    content = str(body["messages"][0]["content"])
    return content.split("|", 1)[0].removeprefix("Design test cases for the requirement: ").strip()


def _run(handler: Handler) -> EvaluationReport:
    """Run the golden set eval with *handler* as the fake model."""

    async def _do() -> EvaluationReport:
        store = InMemoryPromptStore([PROMPT_SPEC])
        gateway = _gateway(handler)
        agent = TestDesignAgent(store, gateway)
        try:
            return await run_test_design_eval(
                GOLDEN, agent=agent, model="fake-model", prompt_ref="test-designer@1"
            )
        finally:
            await gateway.aclose()

    return asyncio.run(_do())


def _ok_handler(request: httpx.Request) -> httpx.Response:
    """A competent model: the golden suite for the requested fixture."""
    cases = GOLDEN_SUITES[_title_from(request)]
    return httpx.Response(200, json=_assistant({"test_cases": cases}))


# --- Golden set loader --------------------------------------------------------


def test_load_golden_set_missing_file_raises(tmp_path: pathlib.Path) -> None:
    with pytest.raises(GoldenSetError, match="cannot read"):
        load_golden_set(tmp_path / "nope.json")


def test_load_golden_set_invalid_json_raises(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(GoldenSetError, match="failed validation"):
        load_golden_set(bad)


def test_load_golden_set_invalid_schema_raises(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(GoldenSetError, match="failed validation"):
        load_golden_set(bad)


def test_golden_v1_shape() -> None:
    """golden_v1: 12 fixtures across the 7 workflow categories, §31.7 targets."""
    assert GOLDEN.schema_version == 1
    assert GOLDEN.name == "AI QA Copilot golden set"
    assert GOLDEN.version == "v1"
    assert GOLDEN.source.prompt == "test-designer@1"
    assert len(GOLDEN.fixtures) == 12
    assert {fixture.category for fixture in GOLDEN.fixtures} == {
        "auth",
        "checkout",
        "payments",
        "permissions",
        "profile",
        "search",
        "upload",
    }
    assert GOLDEN.targets.schema_valid_min == 0.99
    assert GOLDEN.targets.oracle_step_coverage_min == 0.85
    ids = [fixture.id for fixture in GOLDEN.fixtures]
    assert len(set(ids)) == len(ids)
    for fixture in GOLDEN.fixtures:
        assert fixture.oracle_steps, f"{fixture.id}: empty oracle"
        assert fixture.suite.test_cases, f"{fixture.id}: empty golden suite"


def test_step_coverage_metric_sanity() -> None:
    assert step_coverage([], []) == 1.0
    assert step_coverage(["Open the login page"], ["open the login page"]) == 1.0
    assert step_coverage(["Do something else"], ["open the login page"]) == 0.0


# --- Runner: a full golden run -------------------------------------------------


def test_full_run_meets_targets() -> None:
    report = _run(_ok_handler)
    # Report shape — the stable JSON contract (S1.4 artifact).
    assert report.schema_version == 1
    assert report.agent == "test-designer"
    assert report.model == "fake-model"
    assert report.prompt_ref == "test-designer@1"
    assert report.golden_name == GOLDEN.name
    assert report.golden_version == "v1"
    assert report.golden_fixtures == 12
    assert report.targets == {
        "schema_valid_min": 0.99,
        "oracle_step_coverage_min": 0.85,
    }
    assert report.generated_at
    # §31.7 scoring — every fixture passes, so the run passes.
    assert report.totals.fixtures == 12
    assert report.totals.passed == 12
    assert report.totals.failed == 0
    assert report.totals.schema_valid_fraction == pytest.approx(1.0)
    assert report.totals.coverage_avg is not None
    assert report.totals.coverage_avg >= 0.85
    assert report.passed is True
    for result in report.fixtures:
        assert result.passed is True
        assert result.schema_valid is True
        assert result.coverage is not None
        assert result.coverage >= 0.85
        assert result.case_count >= 1
        assert result.tokens_in == 40
        assert result.tokens_out == 210
        assert result.latency_ms is not None
        assert result.latency_ms >= 0
        assert result.error is None
    # The report serializes to the JSON artifact.
    dumped = json.loads(report.model_dump_json())
    assert dumped["passed"] is True
    assert len(dumped["fixtures"]) == 12


def test_schema_invalid_fixture_fails_but_run_continues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if _title_from(request) == "Order history":
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "not json at all"}}]
                },
            )
        return _ok_handler(request)

    report = _run(handler)
    assert report.passed is False
    assert report.totals.passed == 11
    assert report.totals.failed == 1
    assert report.totals.schema_valid_fraction == pytest.approx(11 / 12)
    bad = next(result for result in report.fixtures if result.title == "Order history")
    assert bad.schema_valid is False
    assert bad.passed is False
    assert bad.coverage is None
    assert bad.error
    for result in report.fixtures:
        if result.title != "Order history":
            assert result.passed is True
            assert result.error is None


def test_low_coverage_fixture_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # One case, one step sharing no tokens with any oracle step.
        return httpx.Response(
            200,
            json=_assistant(
                {
                    "test_cases": [
                        {
                            "id": "TC-001",
                            "title": "Unrelated",
                            "type": "functional",
                            "priority": "medium",
                            "preconditions": [],
                            "steps": ["Do something entirely different"],
                            "expected_results": ["Nothing relevant happens"],
                            "risk": "low",
                            "requirement_refs": [],
                        }
                    ]
                }
            ),
        )

    report = _run(handler)
    assert report.passed is False
    assert report.totals.schema_valid_fraction == pytest.approx(1.0)
    assert report.totals.failed == 12
    assert report.totals.coverage_avg is not None
    assert report.totals.coverage_avg < 0.85
    for result in report.fixtures:
        assert result.schema_valid is True
        assert result.passed is False
        assert result.coverage is not None
        assert result.coverage < 0.85


def test_llm_error_isolated_per_fixture() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    report = _run(handler)
    assert report.passed is False
    assert report.totals.schema_valid_fraction == pytest.approx(0.0)
    assert report.totals.failed == 12
    assert report.totals.coverage_avg is None
    for result in report.fixtures:
        assert result.schema_valid is False
        assert result.passed is False
        assert result.error  # recorded, not fatal


# --- CLI contract --------------------------------------------------------------


def test_cli_without_endpoint_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert eval_cli.main([]) == 2


def _fake_openai_server(
    suites: dict[str, list[dict[str, object]]], broken: str | None
) -> ThreadingHTTPServer:
    """In-process OpenAI-compatible server — the live endpoint for the CLI tests."""

    def _title(prompt: str) -> str:
        # The real prompt file renders "Title: {{title}}" — read it back.
        match = re.search(r"^Title: (.+)$", prompt, re.MULTILINE)
        assert match is not None, f"no title in rendered prompt: {prompt[:120]!r}"
        return match.group(1).strip()

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            title = _title(body["messages"][0]["content"])
            if broken is not None and title == broken:
                content = "not json at all"
            else:
                content = json.dumps({"test_cases": suites[title]})
            payload = {
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
            data = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            """Keep pytest output clean."""

    return ThreadingHTTPServer(("127.0.0.1", 0), _Handler)


def _start(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_cli_end_to_end_passes_and_writes_report(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    server = _fake_openai_server(GOLDEN_SUITES, broken=None)
    thread = _start(server)
    try:
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        report_path = tmp_path / "reports" / "eval.json"
        code = eval_cli.main(
            [
                "--base-url",
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--model",
                "fake-model",
                "--report",
                str(report_path),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    out, err = capsys.readouterr()
    assert code == 0
    report = json.loads(out)
    assert report["schema_version"] == 1
    assert report["agent"] == "test-designer"
    assert report["golden_version"] == "v1"
    assert report["passed"] is True
    assert report["totals"]["fixtures"] == 12
    assert report["totals"]["passed"] == 12
    # --report file carries the same artifact.
    file_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert file_report["totals"] == report["totals"]
    # Human summary goes to stderr, not stdout.
    assert "PASSED" in err
    assert "schema-valid" in err
    assert "step coverage" in err
    assert "fixtures" in err


def test_cli_end_to_end_targets_missed_exits_1(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    broken = "Order history"
    server = _fake_openai_server(GOLDEN_SUITES, broken=broken)
    thread = _start(server)
    try:
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        report_path = tmp_path / "report.json"
        code = eval_cli.main(
            [
                "--base-url",
                f"http://127.0.0.1:{server.server_address[1]}/v1",
                "--model",
                "fake-model",
                "--report",
                str(report_path),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    out, err = capsys.readouterr()
    assert code == 1
    report = json.loads(out)
    assert report["passed"] is False
    assert report["totals"]["failed"] == 1
    bad = next(result for result in report["fixtures"] if result["title"] == broken)
    assert bad["schema_valid"] is False
    assert bad["error"]
    # The summary names the failed fixture on stderr.
    assert "FAILED" in err
    assert broken in err
