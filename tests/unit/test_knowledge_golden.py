"""S5.1 golden gate tests: loader validation, runner, report, live gate.

Hermetic: the gate runs against the checked-in ``golden/retrieval_v1.json``
set (build bible §17 — a versioned gate; §19 S5.1 — no LLM in the path).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from qa_copilot_knowledge.golden import (
    KnowledgeGoldenSetError,
    default_golden_path,
    load_golden_set,
    run_golden_set,
)

LIVE = default_golden_path()


class TestLoadGoldenSet:
    def test_live_set_loads_and_is_valid(self) -> None:
        assert LIVE.is_file()
        gate_set = load_golden_set(LIVE)
        assert gate_set.name == "retrieval-golden"
        assert gate_set.version == "v1"
        assert gate_set.gate.top1_min == 0.9
        assert len(gate_set.queries) >= 10

    def test_queries_reference_distinct_ids_and_corpus_docs(self) -> None:
        gate_set = load_golden_set(LIVE)
        ids = [q.id for q in gate_set.queries]
        assert len(ids) == len(set(ids))
        corpus_refs = {d.source_ref for d in gate_set.corpus}
        for query in gate_set.queries:
            assert query.expect_top1 in corpus_refs
            assert set(query.expect_top_k) <= corpus_refs

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(KnowledgeGoldenSetError, match="cannot read"):
            load_golden_set(tmp_path / "missing.json")

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(KnowledgeGoldenSetError, match="not valid JSON"):
            load_golden_set(bad)

    def test_schema_violation_raises(self, tmp_path: Path) -> None:
        payload = _valid_payload()
        payload["gate"] = {"top1_min": 1.5}  # > 1.0 violates the gate bounds
        path = tmp_path / "set.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(KnowledgeGoldenSetError, match="schema validation"):
            load_golden_set(path)

    def test_empty_corpus_rejected(self, tmp_path: Path) -> None:
        payload = _valid_payload()
        payload["corpus"] = []  # corpus requires at least one document
        path = tmp_path / "set.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(KnowledgeGoldenSetError, match="schema validation"):
            load_golden_set(path)


# --- continued: runner + report ---


class TestRunGoldenSet:
    def test_live_gate_passes(self) -> None:
        report = run_golden_set(LIVE)
        assert report.gate_met
        assert report.pass_rate == 1.0
        assert report.total == len(report.results)
        assert report.passed == report.total
        assert report.failed == 0
        assert all(r.top1_ok and r.topk_ok for r in report.results)
        assert report.gate_top1_min == 0.9

    def test_report_json_round_trip(self) -> None:
        report = run_golden_set(LIVE)
        parsed = json.loads(report.model_dump_json())
        assert parsed["gate_met"] is True
        assert parsed["gate_top1_min"] == 0.9
        assert all(item["top1_ok"] for item in parsed["results"])

    def test_gate_fails_when_accuracy_drops(self, tmp_path: Path) -> None:
        payload = _valid_payload()
        # Point Q1 at the wrong document: 1 of 2 pass -> 0.5 < 0.9 gate.
        payload["queries"][0]["expect_top1"] = payload["queries"][1]["expect_top1"]
        path = tmp_path / "set.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        report = run_golden_set(path)
        assert not report.gate_met
        assert report.pass_rate < report.gate_top1_min
        assert any(not (r.top1_ok and r.topk_ok) for r in report.results)

    def test_run_is_deterministic(self) -> None:
        a = run_golden_set(LIVE)
        b = run_golden_set(LIVE)
        assert a.model_dump_json() == b.model_dump_json()


def _valid_payload() -> dict[str, Any]:
    """A minimal valid golden-set payload (distinct query ids + expected docs)."""
    return {
        "name": "test-set",
        "version": "v1",
        "gate": {"top1_min": 0.9},
        "corpus": [
            {
                "source_type": "document",
                "source_ref": "doc-a",
                "title": "Doc A",
                "content": "alpha beta gamma",
            },
            {
                "source_type": "document",
                "source_ref": "doc-b",
                "title": "Doc B",
                "content": "delta epsilon zeta",
            },
        ],
        "queries": [
            {"id": "Q1", "query": "alpha beta", "expect_top1": "doc-a"},
            {"id": "Q2", "query": "delta epsilon", "expect_top1": "doc-b"},
        ],
    }
