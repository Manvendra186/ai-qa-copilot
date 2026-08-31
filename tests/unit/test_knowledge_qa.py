"""S5.4 — Knowledge Q&A (agent + eval runner + CLI). Offline test suite.

Covers the S5.4 exit criteria (build bible §19 S5.4, §22, §31.7):

- the strict answer contract — valid in-scope JSON (answer + citations)
  and a valid refusal (``in_scope=false``, no answer, no citations)
  validate into a :class:`QAAnswer`; contract violations (in-scope with
  no answer or no citations, a refusal carrying an answer or citations),
  out-of-range confidence, unknown fields, or no JSON at all fail loud
  (``ValueError``);
- the agent against a fake ``httpx`` transport (same pattern as
  ``tests/unit/test_failure_investigator.py``): the rendered prompt
  carries the question + the retrieved passages (source ref, title,
  content), the result is answer + audit payload + ``knowledge-qa@1``;
  invalid model output and a missing prompt fail loud;
- a full run over the **12-question golden Q&A set**
  (``packages/knowledge/golden/qa_v1.json``) with an *oracle* fake model
  (echoes each question's grounded facts + expected citations → 100%)
  and a *dumb* model (always refuses → 0/8 in-scope) — the §31.7 gate
  (≥ 80% in-scope grounded, 100% out-of-scope refused) met / missed;
- failure isolation — a schema-invalid output or an LLM error fails *its*
  question and only that one; the run continues and the report is still
  produced;
- the CLI contract — JSON report on stdout (and ``--report``), human
  summary on stderr, exit ``0`` (targets met) / ``1`` (targets missed) /
  ``2`` (configuration error). The end-to-end CLI tests run against an
  in-process OpenAI-compatible HTTP server that answers from the *real*
  registered prompt — no real LLM, no network.
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import re
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from qa_copilot_ai import (
    KNOWLEDGE_QA_NAME,
    InMemoryPromptStore,
    KnowledgeQAAgent,
    LLMGateway,
    PromptNotFound,
    PromptSpec,
    QAAnswer,
)
from qa_copilot_ai.agents import (
    KnowledgeContext,
    KnowledgeQAInput,
    parse_qa_answer,
)
from qa_copilot_ai.agents.knowledge_qa import NO_CONTEXT, render_context
from qa_copilot_ai.knowledge_qa import QAReport, run_qa_eval
from qa_copilot_ai.knowledge_qa import cli as knowledge_qa_cli
from qa_copilot_ai.prompts import FilePromptStore, render_prompt
from qa_copilot_knowledge import (
    QAGate,
    QAGoldenSet,
    QAQuestion,
    default_qa_golden_path,
    load_qa_golden_set,
)

# The same spec shape the real prompt file (knowledge-qa.v1.md) has, with
# the variable lines the agent renders — good enough for the fake-model
# tests to read the question + passages back out of the rendered prompt.
PROMPT_SPEC = PromptSpec(
    name="knowledge-qa",
    version=1,
    body=(
        "Answer the question.\n"
        "Question: {{question}}\n"
        "Retrieved project knowledge:\n"
        "{{context}}\n"
        "Respond with one JSON object."
    ),
    model_class="coder",
    input_budget=60000,
    output_budget=4096,
    schema_ref="knowledge-qa/v1",
    temperature=0.2,
)

VALID_ANSWER = {
    "in_scope": True,
    "answer": "The table shows ten orders per page, newest first by default.",
    "citations": [{"source_ref": "REQ-001", "title": "Order history with sorting and pagination"}],
    "confidence": 0.85,
}

REFUSAL: dict[str, object] = {"in_scope": False, "answer": None, "citations": [], "confidence": 0.9}

GOLDEN = load_qa_golden_set(default_qa_golden_path())

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "packages" / "ai" / "prompts"


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


def _assistant(payload: dict[str, object] | str) -> dict[str, object]:
    """One OpenAI-style chat-completion response body."""
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 210},
    }


def _qa_agent(handler: Handler) -> KnowledgeQAAgent:
    return KnowledgeQAAgent(InMemoryPromptStore([PROMPT_SPEC]), _gateway(handler))


def _prompt_text(request: httpx.Request) -> str:
    body = json.loads(request.content)
    return str(body["messages"][0]["content"])


def _golden_question(content: str) -> QAQuestion:
    """Read the question out of the rendered prompt and find its golden entry."""
    match = re.search(r"^Question: (.+)$", content, re.M)
    assert match is not None, f"no question in rendered prompt: {content[:160]!r}"
    return next(q for q in GOLDEN.questions if q.question == match.group(1))


def _oracle_answer(content: str) -> dict[str, object]:
    """A competent model: echoes the question's grounded facts + citations."""
    golden_q = _golden_question(content)
    if not golden_q.expect.in_scope:
        return dict(REFUSAL)
    return {
        "in_scope": True,
        "answer": " ".join(golden_q.expect.grounded_facts),
        "citations": [
            {"source_ref": source, "title": source} for source in golden_q.expect.cite_sources
        ],
        "confidence": 0.9,
    }


def _oracle_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_assistant(_oracle_answer(_prompt_text(request))))


async def _run(handler: Handler, golden: QAGoldenSet = GOLDEN) -> QAReport:
    agent = _qa_agent(handler)
    try:
        return await run_qa_eval(
            golden, agent=agent, model="fake-model", prompt_ref="knowledge-qa@1"
        )
    finally:
        await agent._gateway.aclose()


def _run_sync(handler: Handler, golden: QAGoldenSet = GOLDEN) -> QAReport:
    return asyncio.run(_run(handler, golden))


# --------------------------------------------------------------------------
# registered prompt file (packages/ai/prompts/knowledge-qa.v1.md)
# --------------------------------------------------------------------------


def test_prompt_file_registered() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get(KNOWLEDGE_QA_NAME)
    assert spec.ref == "knowledge-qa@1"
    assert spec.model_class == "coder"
    assert spec.temperature == 0.2
    assert spec.input_budget == 60000
    assert spec.output_budget == 4096
    assert spec.schema_ref == "knowledge-qa/v1"
    for variable in ("question", "context"):
        assert "{{" + variable + "}}" in spec.body


def test_prompt_renders_question_and_context() -> None:
    spec = FilePromptStore(PROMPTS_DIR).get(KNOWLEDGE_QA_NAME)
    question = GOLDEN.questions[0]
    rendered = render_prompt(
        spec,
        question=question.question,
        context="1. [REQ-001] Order history\nThe table shows ten orders per page.",
    )
    # No placeholder may survive rendering; the question + passages are carried.
    assert "{{" not in rendered
    assert question.question in rendered
    assert "[REQ-001] Order history" in rendered


# --------------------------------------------------------------------------
# strict answer contract (parse_qa_answer)
# --------------------------------------------------------------------------


def test_parse_qa_answer_valid_in_scope() -> None:
    a = parse_qa_answer(json.dumps(VALID_ANSWER))
    assert isinstance(a, QAAnswer)
    assert a.in_scope is True
    assert a.answer is not None and "ten orders per page" in a.answer
    assert a.citations[0].source_ref == "REQ-001"
    assert a.citations[0].title == "Order history with sorting and pagination"
    assert a.confidence == 0.85


def test_parse_qa_answer_valid_refusal() -> None:
    a = parse_qa_answer(json.dumps(REFUSAL))
    assert a.in_scope is False
    assert a.answer is None
    assert a.citations == []


@pytest.mark.parametrize(
    "text",
    [
        "Here is the answer:\n```json\n" + json.dumps(VALID_ANSWER) + "\n```\nHope that helps!",
        json.dumps(REFUSAL) + "\n\n(End of answer.)",
    ],
)
def test_parse_qa_answer_tolerates_prose_and_fences(text: str) -> None:
    a = parse_qa_answer(text)
    assert a.in_scope is (json.dumps(VALID_ANSWER) in text)


def test_parse_qa_answer_no_json_raises_value_error() -> None:
    with pytest.raises(ValueError, match="no JSON object"):
        parse_qa_answer("I don't have any project knowledge about that, sorry.")


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (
            {"in_scope": True, "citations": [{"source_ref": "REQ-001", "title": "t"}]},
            "non-empty",  # in-scope without an answer
        ),
        (
            {
                "in_scope": True,
                "answer": "   ",
                "citations": [{"source_ref": "REQ-001", "title": "t"}],
            },
            "non-empty",  # blank answer
        ),
        ({"in_scope": True, "answer": "ten orders per page"}, "cite"),  # no citations
        ({"in_scope": False, "answer": "Paris is the capital."}, "no answer"),
        (
            {"in_scope": False, "citations": [{"source_ref": "REQ-001", "title": "t"}]},
            "citations",
        ),
        (dict(VALID_ANSWER, confidence=1.5), "schema validation"),  # out of [0, 1]
        (dict(VALID_ANSWER, extra_field="x"), "schema validation"),  # unknown field
        ({"in_scope": "yes", "answer": "x", "citations": []}, "schema validation"),
    ],
    ids=[
        "in-scope-no-answer",
        "in-scope-blank-answer",
        "in-scope-no-citations",
        "refusal-with-answer",
        "refusal-with-citations",
        "confidence-high",
        "unknown-field",
        "in-scope-not-bool",
    ],
)
def test_parse_qa_answer_contract_violations_raise(payload: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_qa_answer(json.dumps(payload))


# --------------------------------------------------------------------------
# context rendering
# --------------------------------------------------------------------------


def test_render_context_empty_is_placeholder() -> None:
    assert render_context(()) == NO_CONTEXT
    assert "no relevant project knowledge" in NO_CONTEXT


def test_render_context_numbered_passages() -> None:
    rendered = render_context(
        (
            KnowledgeContext(source_ref="REQ-001", title="Order history", content="Ten per page."),
            KnowledgeContext(source_ref="REQ-002", title="Password reset", content="30 minutes."),
        )
    )
    assert rendered.index("[REQ-001] Order history") < rendered.index("[REQ-002] Password reset")
    assert "1. [REQ-001] Order history\nTen per page." in rendered
    assert "2. [REQ-002] Password reset\n30 minutes." in rendered


# --------------------------------------------------------------------------
# agent (fake transport)
# --------------------------------------------------------------------------


def test_agent_returns_answer_and_audit() -> None:
    agent = _qa_agent(lambda _req: httpx.Response(200, json=_assistant(VALID_ANSWER)))
    try:
        result = asyncio.run(
            agent.run(
                KnowledgeQAInput(
                    question="How many orders per page?",
                    context=(
                        KnowledgeContext(
                            source_ref="REQ-001",
                            title="Order history",
                            content="Ten orders per page.",
                        ),
                    ),
                )
            )
        )
    finally:
        asyncio.run(agent._gateway.aclose())

    assert result.answer.in_scope is True
    assert result.answer.answer is not None and "ten orders per page" in result.answer.answer
    assert result.call.usage.tokens_in == 40
    assert result.call.usage.tokens_out == 210
    assert result.call.latency_ms >= 0
    assert result.prompt_ref == "knowledge-qa@1"


def test_agent_prompt_carries_question_and_passages() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_assistant(VALID_ANSWER))

    agent = _qa_agent(capture)
    try:
        asyncio.run(
            agent.run(
                KnowledgeQAInput(
                    question="How many orders per page?",
                    context=(
                        KnowledgeContext(
                            source_ref="REQ-001",
                            title="Order history with sorting and pagination",
                            content="The table shows ten orders per page.",
                        ),
                    ),
                )
            )
        )
    finally:
        asyncio.run(agent._gateway.aclose())

    content = _prompt_text(seen[0])
    assert "Question: How many orders per page?" in content
    assert "[REQ-001] Order history with sorting and pagination" in content
    assert "The table shows ten orders per page." in content
    body = json.loads(seen[0].content)
    assert body["model"] == "fake-model"


def test_agent_invalid_model_output_raises_value_error() -> None:
    agent = _qa_agent(lambda _req: httpx.Response(200, json=_assistant("no JSON here at all")))
    try:
        with pytest.raises(ValueError, match="no JSON object"):
            asyncio.run(agent.run(KnowledgeQAInput(question="What?")))
    finally:
        asyncio.run(agent._gateway.aclose())


def test_agent_prompt_not_found_raises() -> None:
    store = InMemoryPromptStore([])
    gateway = _gateway(lambda _r: httpx.Response(200))
    agent = KnowledgeQAAgent(store, gateway)
    try:
        with pytest.raises(PromptNotFound):
            asyncio.run(agent.run(KnowledgeQAInput(question="What?")))
    finally:
        asyncio.run(gateway.aclose())


# --------------------------------------------------------------------------
# eval runner over the real golden set (12 questions, §31.7 gate)
# --------------------------------------------------------------------------


def test_oracle_model_meets_qa_gate() -> None:
    report = _run_sync(_oracle_handler)

    assert report.golden_questions == 12
    assert len(report.questions) == 12
    assert report.totals.in_scope_passed == 8
    assert report.totals.in_scope_questions == 8
    assert report.totals.out_of_scope_refused == 4
    assert report.totals.out_of_scope_questions == 4
    assert report.totals.in_scope_fraction == 1.0
    assert report.totals.out_of_scope_fraction == 1.0
    # The §31.7 gate comes from the golden file, not the code.
    assert report.targets == {"in_scope_min": 0.8, "out_of_scope_refuse_min": 1.0}
    assert report.passed is True
    assert all(q.passed for q in report.questions)

    # Every in-scope answer is grounded + every citation is a corpus source.
    for q in report.questions:
        if q.expected_in_scope:
            assert q.grounded is True
            assert q.citations_ok is True
            assert q.schema_valid is True
        else:
            assert q.refused is True
            assert q.answer is None


def test_dumb_model_misses_in_scope_gate() -> None:
    def always_refuse(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant(REFUSAL))

    report = _run_sync(always_refuse)
    assert report.totals.in_scope_passed == 0
    assert report.totals.out_of_scope_refused == 4
    assert report.passed is False
    # The gate is still read from the golden file.
    assert report.targets == {"in_scope_min": 0.8, "out_of_scope_refuse_min": 1.0}


def test_gate_thresholds_come_from_golden_file() -> None:
    # A model that always refuses still passes a *loose* gate (in-scope min 0)
    # and fails the shipped one (0.8) — proof the thresholds are data-driven.
    relaxed = QAGoldenSet(
        name=GOLDEN.name,
        version=GOLDEN.version,
        gate=QAGate(in_scope_min=0.0),
        corpus=GOLDEN.corpus,
        questions=GOLDEN.questions,
    )

    def always_refuse(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_assistant(REFUSAL))

    assert _run_sync(always_refuse, relaxed).passed is True
    assert _run_sync(always_refuse).passed is False


def test_hallucinating_model_fails_both_gates() -> None:
    def hallucinate(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_assistant(
                {
                    "in_scope": True,
                    "answer": "I think it is probably like that in most projects.",
                    "citations": [{"source_ref": "WIKIPEDIA", "title": "General knowledge"}],
                    "confidence": 0.7,
                }
            ),
        )

    report = _run_sync(hallucinate)
    assert report.totals.in_scope_passed == 0  # not grounded + citation not in corpus
    assert report.totals.out_of_scope_refused == 0  # must refuse, answers instead
    assert report.passed is False
    # The fabricated citation must never count as a corpus source.
    assert all(q.citations_ok is False for q in report.questions)


def test_llm_error_fails_only_its_question() -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if GOLDEN.questions[calls["n"] - 1].id == "QA-001":
            return httpx.Response(500, json={"error": "upstream exploded"})
        return _oracle_handler(request)

    report = _run_sync(flaky)
    failed = {q.id for q in report.questions if not q.passed}
    assert failed == {"QA-001"}
    assert report.totals.in_scope_passed == 7
    assert report.totals.out_of_scope_refused == 4
    # 7/8 = 0.875 ≥ 0.8 and 4/4 = 1.0 ≥ 1.0 — the run still meets the gate.
    assert report.passed is True
    # The failed question is recorded with the reason, not silently dropped.
    bad = next(q for q in report.questions if q.id == "QA-001")
    assert bad.passed is False
    assert bad.error is not None


# --------------------------------------------------------------------------
# CLI (arg parsing, exit codes, stdout/stderr contract, end-to-end)
# --------------------------------------------------------------------------


@pytest.fixture()
def _cli_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI must not depend on a developer's shell env.
    for var in ("LLM_BASE_URL", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_cli_without_endpoint_exits_2(_cli_env: None) -> None:  # noqa: ARG001
    stdout, err, code = _run_cli([])
    assert code == 2
    assert "LLM endpoint not configured" in err
    assert stdout == ""


def test_cli_bad_timeout_exits_2(_cli_env: None) -> None:  # noqa: ARG001
    stdout, err, code = _run_cli(
        [
            "--base-url",
            "http://127.0.0.1:1/v1",
            "--model",
            "fake-model",
            "--timeout",
            "0",
        ]
    )
    assert code == 2
    assert "--timeout must be > 0" in err
    assert stdout == ""


def test_cli_missing_golden_exits_2(
    _cli_env: None,
    tmp_path: pathlib.Path,  # noqa: ARG001
) -> None:
    stdout, err, code = _run_cli(
        [
            "--base-url",
            "http://127.0.0.1:1/v1",
            "--model",
            "fake-model",
            "--golden",
            str(tmp_path / "missing.json"),
        ]
    )
    assert code == 2
    assert "golden Q&A set not found" in err
    assert stdout == ""


def test_emit_writes_json_report_and_summary(
    _cli_env: None,
    tmp_path: pathlib.Path,  # noqa: ARG001
) -> None:
    oracle_report = _run_sync(_oracle_handler)
    dumb_report = _run_sync(lambda _req: httpx.Response(200, json=_assistant(REFUSAL)))

    report_path = tmp_path / "report.json"
    out, err = io.StringIO(), io.StringIO()
    code = knowledge_qa_cli._emit(oracle_report, report_path=report_path, stdout=out, stderr=err)
    assert code == 0
    payload = out.getvalue()
    assert json.loads(payload)["passed"] is True
    assert report_path.read_text(encoding="utf-8") == payload  # file mirrors stdout
    assert "PASSED (exit 0)" in err.getvalue()
    assert "in-scope grounded" in err.getvalue() and "8/8" in err.getvalue()
    assert "out-of-scope refused" in err.getvalue() and "4/4" in err.getvalue()

    out, err = io.StringIO(), io.StringIO()
    code = knowledge_qa_cli._emit(dumb_report, report_path=None, stdout=out, stderr=err)
    assert code == 1
    assert json.loads(out.getvalue())["passed"] is False
    assert "FAILED (exit 1)" in err.getvalue()
    assert "in-scope grounded" in err.getvalue() and "0/8" in err.getvalue()


def _run_cli(argv: list[str]) -> tuple[str, str, int]:
    """Invoke ``knowledge_qa.cli.main`` in-process, capturing stdout/stderr."""
    stdout, stderr = io.StringIO(), io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = stdout, stderr
    try:
        code = knowledge_qa_cli.main(argv)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return stdout.getvalue(), stderr.getvalue(), code


def _fake_llm_server(
    reply: Callable[[str], dict[str, object]],
) -> tuple[ThreadingHTTPServer, int, threading.Thread]:
    """One-shot OpenAI-compatible server whose model answers via ``reply``."""

    class FakeModelHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._respond(200, _assistant(reply(body["messages"][0]["content"])))

        def do_GET(self) -> None:  # noqa: N802
            self._respond(200, {"object": "list", "data": [{"id": "fake-model"}]})

        def _respond(self, status: int, payload: dict[str, object]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_fmt: object) -> None:  # keep pytest output clean
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeModelHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, int(server.server_address[1]), t


def test_cli_end_to_end_oracle_passes(_cli_env: None, tmp_path: pathlib.Path) -> None:  # noqa: ARG001
    """Real prompt file → real gateway → real runner → real CLI, fake HTTP model."""
    server, port, t = _fake_llm_server(_oracle_answer)
    try:
        report_path = tmp_path / "e2e.json"
        out, err, code = _run_cli(
            [
                "--base-url",
                f"http://127.0.0.1:{port}/v1",
                "--model",
                "fake-model",
                "--timeout",
                "5",
                "--report",
                str(report_path),
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2.0)

    assert code == 0, err
    report = json.loads(out)
    assert report["passed"] is True
    assert report["golden_questions"] == 12
    assert report["totals"]["in_scope_passed"] == 8
    assert report["totals"]["out_of_scope_refused"] == 4
    assert report_path.read_text(encoding="utf-8") == out  # file mirrors stdout
    assert "PASSED (exit 0)" in err


def test_cli_end_to_end_dumb_model_exits_1(_cli_env: None) -> None:  # noqa: ARG001
    server, port, t = _fake_llm_server(lambda _content: dict(REFUSAL))
    try:
        out, err, code = _run_cli(
            [
                "--base-url",
                f"http://127.0.0.1:{port}/v1",
                "--model",
                "fake-model",
                "--timeout",
                "5",
            ]
        )
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=2.0)

    assert code == 1, err
    report = json.loads(out)
    assert report["passed"] is False
    assert report["totals"]["in_scope_passed"] == 0
    assert report["totals"]["out_of_scope_refused"] == 4
    assert "FAILED (exit 1)" in err
