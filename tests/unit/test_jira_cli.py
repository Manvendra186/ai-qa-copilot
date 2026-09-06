"""S7.4 Jira CLI tests (build bible §19 S7.4, §17).

The CLI contract (JSON on **stdout**, human summary on **stderr**):

- ``map FAILURE_JSON --project-key KEY`` — emits the deterministic failure →
  issue ``fields`` payload (the exact body the client POSTs; the ``map`` pin
  in the golden set must match 100%).
- ``golden [--path FILE]`` — replays the S7.4 golden set through the real
  client on the fake server and emits the §31.7 gate report.

Exit codes: 0 success / gate met · 1 gate failed · 2 configuration error.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qa_copilot_integrations.jira import cli
from qa_copilot_integrations.jira.issue import build_issue_payload

# --- map ------------------------------------------------------------------------


def test_cli_map_emits_payload(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    failure = {
        "category": "regression",
        "root_cause": "Coupon discount applied before tax",
        "error": "AssertionError: 9.4 != 9.0",
    }
    failure_path = tmp_path / "failure.json"
    failure_path.write_text(json.dumps(failure), encoding="utf-8")
    rc = cli.main(["map", str(failure_path), "--project-key", "QA"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    # must match the golden mapping pin byte-for-byte (S7.4 exit criterion)
    assert payload == build_issue_payload(dict(failure), "QA")
    assert payload["project"] == {"key": "QA"}
    assert payload["issuetype"] == {"name": "Bug"}
    assert payload["summary"].startswith("[QA] ")
    assert payload["labels"] == ["qa-copilot", "failure-regression"]


def test_cli_map_invalid_failure_is_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "failure.json"
    path.write_text("not json", encoding="utf-8")
    rc = cli.main(["map", str(path), "--project-key", "QA"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "jira map" in captured.err.lower()
    assert captured.out == ""


def test_cli_map_missing_file_is_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(["map", str(Path.cwd() / "definitely_missing.json"), "--project-key", "QA"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "jira map" in captured.err.lower()
    assert captured.out == ""


# --- golden ---------------------------------------------------------------------


def test_cli_golden_runs_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["golden"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["golden_name"] == "jira_client"
    assert payload["mappings"] == 1
    assert payload["fixtures"] == 7
    assert payload["passed"] == 8
    assert payload["score"] == 1.0
    assert payload["gate_met"] is True
    assert payload["failures"] == []
    # human summary on stderr
    assert "8/8 cases" in captured.err
    assert "score=1.0" in captured.err
    assert "PASS" in captured.err


def test_cli_golden_missing_path_is_config_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cli.main(["golden", "--path", str(Path.cwd() / "definitely_missing.json")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "cannot load" in captured.err.lower()
    assert captured.out == ""


def test_cli_golden_malformed_is_config_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "golden.json"
    path.write_text("not json", encoding="utf-8")
    rc = cli.main(["golden", "--path", str(path)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "cannot load" in captured.err.lower()
