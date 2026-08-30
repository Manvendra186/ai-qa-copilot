"""S5.1 CLI tests: golden / index / search subcommands and exit codes.

Exit codes: 0 = success or gate met, 1 = golden gate not met, 2 = usage or
environment error. JSON payloads go to stdout, summaries to stderr.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qa_copilot_knowledge.cli import EXIT_GATE_MISSED, EXIT_OK, EXIT_USAGE, main

REPO_FILE = "e2e/orders.spec.ts"
REPO_CONTENT = "test('orders', () => { expect(table).toContainText('Order 2026-001'); });\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "e2e").mkdir()
    (tmp_path / REPO_FILE).write_text(REPO_CONTENT, encoding="utf-8")
    return tmp_path


class TestGoldenCommand:
    def test_default_gate_passes_and_prints_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["golden"]) == EXIT_OK
        out, err = capsys.readouterr()
        report = json.loads(out)
        assert report["gate_met"] is True
        assert report["pass_rate"] == 1.0
        assert "PASS" in err
        assert "MISS" not in err

    def test_explicit_golden_path(self, capsys: pytest.CaptureFixture[str]) -> None:
        from qa_copilot_knowledge.golden import default_golden_path

        assert main(["golden", "--golden-path", str(default_golden_path())]) == EXIT_OK
        assert json.loads(capsys.readouterr().out)["gate_met"] is True

    def test_failing_set_exits_gate_missed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        payload = {
            "name": "fail-set",
            "version": "v1",
            "gate": {"top1_min": 0.9},
            "corpus": [
                {
                    "source_type": "document",
                    "source_ref": "doc-a",
                    "title": "A",
                    "content": "alpha beta gamma",
                },
                {
                    "source_type": "document",
                    "source_ref": "doc-b",
                    "title": "B",
                    "content": "delta epsilon zeta",
                },
            ],
            "queries": [
                {"id": "Q1", "query": "alpha beta", "expect_top1": "doc-b"},
                {"id": "Q2", "query": "delta epsilon", "expect_top1": "doc-b"},
            ],
        }
        path = tmp_path / "set.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert main(["golden", "--golden-path", str(path)]) == EXIT_GATE_MISSED
        out, err = capsys.readouterr()
        report = json.loads(out)
        assert report["gate_met"] is False
        assert "MISS" in err

    def test_invalid_golden_set_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert main(["golden", "--golden-path", str(bad)]) == EXIT_USAGE
        assert "error:" in capsys.readouterr().err

    def test_missing_golden_set_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["golden", "--golden-path", str(tmp_path / "nope.json")]) == EXIT_USAGE
        assert "error:" in capsys.readouterr().err


class TestIndexAndSearchCommands:
    def test_index_reports_the_repository(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["index", str(repo)]) == EXIT_OK
        out, err = capsys.readouterr()
        report = json.loads(out)
        assert report["document_count"] == 1
        assert report["chunk_count"] >= 1
        assert report["source_breakdown"] == {"repository_file": 1}
        assert "index:" in err

    def test_index_missing_root_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["index", str(tmp_path / "missing")]) == EXIT_USAGE
        assert "error:" in capsys.readouterr().err

    def test_search_finds_the_file(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["search", str(repo), "orders table"]) == EXIT_OK
        out, err = capsys.readouterr()
        result = json.loads(out)
        assert result["hits"]
        assert result["hits"][0]["chunk"]["document_ref"] == REPO_FILE
        assert "search:" in err

    def test_search_respects_top_k(self, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (repo / "e2e" / "more.spec.ts").write_text("test('more', () => {});", encoding="utf-8")
        assert main(["search", str(repo), "test", "--top-k", "1"]) == EXIT_OK
        result = json.loads(capsys.readouterr().out)
        assert len(result["hits"]) == 1

    def test_search_blank_query_is_usage_error(
        self, repo: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["search", str(repo), "   "]) == EXIT_USAGE
        assert "error:" in capsys.readouterr().err

    def test_search_missing_root_is_usage_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["search", str(tmp_path / "missing"), "query"]) == EXIT_USAGE
        assert "error:" in capsys.readouterr().err

    def test_no_subcommand_is_argparse_usage_error(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main([])
        assert excinfo.value.code == 2
