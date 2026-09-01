"""S6.3 — deterministic regression recommender (core + golden + advisor + CLI).

Offline test suite (build bible §19 S6.3, §22, §31.7). The S6.3 gate is the
**deterministic** :func:`qa_copilot_repository.recommend` core (S6.1 impact
joined with the S6.2 risk ranking), so the whole suite runs with no LLM, no
network, and no database: the deterministic core (join, ordering, tie-break,
truncation, no-history, strongest-impact-kind, rationale, ``top_n < 1`` guard);
the golden set (loads + a 100% green eval); the optional LLM advisor (LLM brief
via a fake transport, plus the safe stub fallback on LLM error / schema-invalid
output / missing prompt); and the CLI contract (JSON on stdout + ``--report``,
summary on stderr, exit 0/1/2).
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
from collections.abc import Callable, Mapping

import httpx
import pytest
from qa_copilot_ai import InMemoryPromptStore, LLMGateway, PromptSpec
from qa_copilot_ai.agents import AdvisorInput, RegressionAdvisorAgent, parse_summary, stub_summary
from qa_copilot_ai.regression import (
    RegressionExpect,
    RegressionFixture,
    RegressionGoldenSet,
    RegressionGoldenSource,
    RegressionReport,
    build_parser,
    default_golden_path,
    load_regression_golden_set,
    run_regression_eval,
)
from qa_copilot_ai.regression import cli as regression_cli
from qa_copilot_domain import (
    ImpactedTest,
    ImpactKind,
    ImpactSet,
    RecommendationSet,
    RiskRanking,
    TestHistoryStats,
    TestRisk,
)
from qa_copilot_repository import recommend


def _impact(*impacted: tuple[str, list[str]]) -> ImpactSet:
    """An :class:`ImpactSet` with the given (path, kinds) impacted tests."""
    return ImpactSet(
        changed=["src/app.py"],
        impacted=[
            ImpactedTest(path=path, kinds=[ImpactKind(kind) for kind in kinds])
            for path, kinds in impacted
        ],
    )


def _ranking(*risks: tuple[str, float]) -> RiskRanking:
    """A :class:`RiskRanking` with the given (test_key, risk_score) entries."""
    return RiskRanking(
        project_id="proj",
        ranked=[
            TestRisk(test_key=key, risk_score=score, stats=TestHistoryStats(test_key=key))
            for key, score in risks
        ],
    )


def _set(impact: ImpactSet, ranking: RiskRanking, top_n: int = 10) -> RecommendationSet:
    return recommend(impact, ranking, top_n=top_n)


# --- deterministic core (qa_copilot_repository.recommend) -------------------


def test_recommend_ranks_by_risk_score_desc() -> None:
    impact = _impact(
        ("tests/test_b.py", ["direct"]),
        ("tests/test_c.py", ["direct"]),
        ("tests/test_a.py", ["direct"]),
    )
    ranking = _ranking(("tests/test_a.py", 0.9), ("tests/test_c.py", 0.6), ("tests/test_b.py", 0.3))
    result = _set(impact, ranking)
    assert [r.test_key for r in result.recommendations] == [
        "tests/test_a.py",
        "tests/test_c.py",
        "tests/test_b.py",
    ]
    assert [r.rank for r in result.recommendations] == [1, 2, 3]


def test_recommend_truncates_to_top_n() -> None:
    impact = _impact(
        ("tests/test_d.py", ["direct"]),
        ("tests/test_a.py", ["direct"]),
        ("tests/test_b.py", ["direct"]),
        ("tests/test_c.py", ["direct"]),
    )
    ranking = _ranking(
        ("tests/test_a.py", 0.9),
        ("tests/test_b.py", 0.8),
        ("tests/test_c.py", 0.7),
        ("tests/test_d.py", 0.6),
    )
    result = _set(impact, ranking, top_n=2)
    assert [r.test_key for r in result.recommendations] == ["tests/test_a.py", "tests/test_b.py"]
    assert result.top_n == 2


def test_recommend_tie_breaks_by_test_key_ascending() -> None:
    impact = _impact(
        ("tests/test_z.py", ["direct"]),
        ("tests/test_a.py", ["direct"]),
        ("tests/test_m.py", ["direct"]),
    )
    ranking = _ranking(("tests/test_z.py", 0.5), ("tests/test_a.py", 0.5), ("tests/test_m.py", 0.5))
    result = _set(impact, ranking)
    assert [r.test_key for r in result.recommendations] == [
        "tests/test_a.py",
        "tests/test_m.py",
        "tests/test_z.py",
    ]


def test_recommend_never_includes_non_impacted_tests() -> None:
    impact = _impact()  # empty impacted set
    ranking = _ranking(("tests/test_a.py", 0.9))
    assert _set(impact, ranking).recommendations == []


def test_recommend_impacted_test_with_no_history_scores_zero() -> None:
    impact = _impact(("tests/test_new.py", ["direct"]), ("tests/test_old.py", ["referenced"]))
    ranking = _ranking(("tests/test_old.py", 0.4))  # test_new has no history
    result = _set(impact, ranking)
    assert [r.test_key for r in result.recommendations] == [
        "tests/test_old.py",
        "tests/test_new.py",
    ]
    new = result.recommendations[-1]
    assert new.risk_score == 0.0
    assert new.stats.executions == 0


def test_recommend_reports_strongest_impact_kind() -> None:
    impact = _impact(
        ("tests/test_a.py", ["direct"]),
        ("tests/test_b.py", ["generated"]),
        ("tests/test_c.py", ["referenced", "direct"]),
    )
    ranking = _ranking(("tests/test_a.py", 0.5), ("tests/test_b.py", 0.5), ("tests/test_c.py", 0.5))
    result = _set(impact, ranking)
    assert [r.impact_kind for r in result.recommendations] == [
        ImpactKind.DIRECT,
        ImpactKind.GENERATED,
        ImpactKind.DIRECT,  # direct outranks referenced
    ]


def test_recommend_rationale_is_deterministic() -> None:
    impact = _impact(("tests/test_a.py", ["direct"]))
    ranking = _ranking(("tests/test_a.py", 0.9))
    first = _set(impact, ranking).recommendations[0].rationale
    second = _set(impact, ranking).recommendations[0].rationale
    assert first == second
    assert "impact:direct" in first


def test_recommend_rejects_top_n_below_one() -> None:
    with pytest.raises(ValueError):
        _set(_impact(("tests/test_a.py", ["direct"])), _ranking(("tests/test_a.py", 0.9)), top_n=0)


# --- golden set + eval runner -------------------------------------------------


def test_golden_set_loads() -> None:
    golden = load_regression_golden_set(default_golden_path())
    assert golden.name == "regression-recommender"
    assert golden.targets.pass_min == 1.0
    assert len(golden.fixtures) >= 1
    assert golden.fixtures[0].id.startswith("REG-")


def test_regression_eval_passes_the_golden_set() -> None:
    golden = load_regression_golden_set(default_golden_path())
    report = run_regression_eval(golden)
    assert report.passed is True
    assert report.totals.passed == report.totals.fixtures
    assert all(case.passed for case in report.cases)


def test_regression_eval_flags_a_wrong_expectation() -> None:
    golden = RegressionGoldenSet(
        name="regression-recommender",
        source=RegressionGoldenSource(build_bible="v1"),
        fixtures=[
            RegressionFixture(
                id="REG-999",
                title="deliberately wrong expectation",
                impact=_impact(("tests/test_a.py", ["direct"])),
                ranking=_ranking(("tests/test_a.py", 0.9)),
                expect=RegressionExpect(ordered_keys=["tests/test_zzz.py"]),
            )
        ],
    )
    report = run_regression_eval(golden)
    assert report.passed is False
    assert report.cases[0].passed is False
    assert report.cases[0].actual_keys == ["tests/test_a.py"]


def test_build_parser_flags() -> None:
    assert build_parser().parse_args([]).advise is False
    assert build_parser().parse_args(["--advise"]).advise is True


# --- optional LLM advisor (RegressionAdvisorAgent) --------------------------

PROMPT_SPEC = PromptSpec(
    name="regression-advisor",
    version=1,
    body=(
        "Recommend the top regression tests.\n"
        "Changed: {{changed}}\n"
        "Ranked recommendations:\n"
        "{{recommendations}}\n"
        'Answer with one JSON object: {"summary": str, "focus": str|null}.'
    ),
    model_class="coder",
    input_budget=8000,
    output_budget=4096,
    schema_ref="regression-summary/v1",
    temperature=0.1,
)

VALID_SUMMARY = {"summary": "Run the highest-risk impacted test first.", "focus": "tests/test_a.py"}


def _sample_set() -> RecommendationSet:
    return _set(
        _impact(("tests/test_a.py", ["direct"]), ("tests/test_b.py", ["referenced"])),
        _ranking(("tests/test_a.py", 0.9), ("tests/test_b.py", 0.3)),
    )


def _assistant(payload: Mapping[str, object] | str) -> dict[str, object]:
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


def _advisor(handler: Handler, spec: PromptSpec | None = PROMPT_SPEC) -> RegressionAdvisorAgent:
    store = InMemoryPromptStore([spec] if spec is not None else [])
    return RegressionAdvisorAgent(store, _gateway(handler))


def test_parse_summary_accepts_valid_json() -> None:
    summary = parse_summary("```json\n" + json.dumps(VALID_SUMMARY) + "\n```")
    assert summary.summary == VALID_SUMMARY["summary"]
    assert summary.focus == VALID_SUMMARY["focus"]


def test_parse_summary_rejects_invalid_output() -> None:
    with pytest.raises(ValueError):
        parse_summary("no json object here")
    with pytest.raises(ValueError):
        parse_summary(json.dumps({"summary": ""}))  # empty summary fails the schema


def test_advisor_uses_the_llm_brief_when_valid() -> None:
    advisor = _advisor(lambda request: httpx.Response(200, json=_assistant(VALID_SUMMARY)))
    try:
        result = asyncio.run(advisor.run(AdvisorInput(set=_sample_set())))
    finally:
        asyncio.run(advisor._gateway.aclose())
    assert result.source == "llm"
    assert result.summary == VALID_SUMMARY["summary"]
    assert result.prompt_ref == "regression-advisor@1"


def test_advisor_falls_back_to_stub_on_llm_error() -> None:
    advisor = _advisor(lambda request: httpx.Response(500, text="boom"))
    try:
        result = asyncio.run(advisor.run(AdvisorInput(set=_sample_set())))
    finally:
        asyncio.run(advisor._gateway.aclose())
    assert result.source == "stub"
    assert result.summary == stub_summary(_sample_set())


def test_advisor_falls_back_to_stub_on_schema_invalid_output() -> None:
    advisor = _advisor(lambda request: httpx.Response(200, json=_assistant("not a json object")))
    try:
        result = asyncio.run(advisor.run(AdvisorInput(set=_sample_set())))
    finally:
        asyncio.run(advisor._gateway.aclose())
    assert result.source == "stub"


def test_advisor_falls_back_to_stub_when_prompt_missing() -> None:
    advisor = _advisor(
        lambda request: httpx.Response(200, json=_assistant(VALID_SUMMARY)),
        spec=None,
    )
    try:
        result = asyncio.run(advisor.run(AdvisorInput(set=_sample_set())))
    finally:
        asyncio.run(advisor._gateway.aclose())
    assert result.source == "stub"
    assert result.summary == stub_summary(_sample_set())


def test_stub_summary_is_deterministic_and_names_the_focus() -> None:
    result_set = _sample_set()
    assert stub_summary(result_set) == stub_summary(result_set)
    assert "tests/test_a.py" in stub_summary(result_set)  # rank-1 focus


# --- CLI contract -------------------------------------------------------------


def _passing_report() -> RegressionReport:
    return run_regression_eval(load_regression_golden_set(default_golden_path()))


def _failing_report() -> RegressionReport:
    golden = RegressionGoldenSet(
        name="regression-recommender",
        source=RegressionGoldenSource(build_bible="v1"),
        fixtures=[
            RegressionFixture(
                id="REG-999",
                title="wrong",
                impact=_impact(("tests/test_a.py", ["direct"])),
                ranking=_ranking(("tests/test_a.py", 0.9)),
                expect=RegressionExpect(ordered_keys=["tests/test_zzz.py"]),
            )
        ],
    )
    return run_regression_eval(golden)


def test_cli_emit_writes_json_and_summary() -> None:
    report = _passing_report()
    out, err = io.StringIO(), io.StringIO()
    code = regression_cli._emit(report, report_path=None, stdout=out, stderr=err)
    assert code == 0
    assert json.loads(out.getvalue())["passed"] is True
    assert "PASSED (exit 0)" in err.getvalue()


def test_cli_emit_writes_report_file(tmp_path: pathlib.Path) -> None:
    report = _passing_report()
    report_path = tmp_path / "regression.json"
    out, err = io.StringIO(), io.StringIO()
    code = regression_cli._emit(report, report_path=report_path, stdout=out, stderr=err)
    assert code == 0
    assert report_path.read_text(encoding="utf-8") == out.getvalue()


def test_cli_emit_failing_exits_1() -> None:
    report = _failing_report()
    out, err = io.StringIO(), io.StringIO()
    code = regression_cli._emit(report, report_path=None, stdout=out, stderr=err)
    assert code == 1
    assert json.loads(out.getvalue())["passed"] is False
    assert "FAILED (exit 1)" in err.getvalue()
    assert "REG-999" in err.getvalue()


def test_cli_main_gate_pass(capsys: pytest.CaptureFixture[str]) -> None:
    code = regression_cli.main([])
    out = capsys.readouterr()
    assert code == 0
    assert json.loads(out.out)["passed"] is True
    assert "PASSED (exit 0)" in out.err


def test_cli_main_config_error_exits_2(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = regression_cli.main(["--golden", str(tmp_path / "missing.json")])
    out = capsys.readouterr()
    assert code == 2
    assert "error:" in out.err
