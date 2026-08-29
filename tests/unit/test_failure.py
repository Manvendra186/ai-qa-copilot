"""S3.3 failure normalizer tests: rules, extraction, golden gate, CLI.

All tests are hermetic (no Playwright, no network, no LLM): the normalizer is
deterministic by contract (build bible §15/§16/§19 S3.3), so the same raw
failure text must always produce the same :class:`NormalizedFailure`.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from qa_copilot_domain.enums import FailureCategory
from qa_copilot_execution import failure
from qa_copilot_execution.failure import (
    EXIT_GATE_MISSED,
    EXIT_OK,
    EXIT_USAGE,
    main,
    mismatches,
    normalize_failure,
    run_golden_set,
)
from qa_copilot_execution.golden import (
    FailureExpectations,
    FailureFixture,
    FailureGoldenSetError,
    default_golden_path,
    load_failure_golden_set,
)

ENV_TEXT = "API request to GET http://localhost:3000/api/orders failed: 503 Service Unavailable"


# --- normalize_failure (build bible §16 taxonomy, priority order) -------------


class TestNormalizeFailure:
    def test_empty_input_is_unknown(self) -> None:
        nf = normalize_failure("")
        assert nf.category is FailureCategory.UNKNOWN
        assert nf.category_signals == []
        assert nf.evidence == []
        assert nf.http_status is None
        assert nf.selector is None
        assert nf.endpoint is None

    def test_whitespace_input_is_unknown(self) -> None:
        assert normalize_failure("   \n  ").category is FailureCategory.UNKNOWN

    def test_environment_connection_refused(self) -> None:
        nf = normalize_failure(
            "page.goto: Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000/products"
        )
        assert nf.category is FailureCategory.ENVIRONMENT_DEFECT
        assert nf.category_signals[0] == "env.net"
        assert nf.endpoint == "http://localhost:3000/products"

    def test_environment_unauthorized_beats_product_assertion(self) -> None:
        text = (
            "API request to POST http://localhost:3000/api/auth/login "
            "failed: 401 Unauthorized\n"
            "expect(received).toBe(expected)\nExpected: 200\nReceived: 401"
        )
        nf = normalize_failure(text)
        assert nf.category is FailureCategory.ENVIRONMENT_DEFECT
        assert nf.category_signals[0] == "env.credentials"
        assert nf.http_status == 401
        # lower-priority matches are still reported as signals
        assert "product.assertion" in nf.category_signals

    def test_flaky_beats_automation_timing(self) -> None:
        text = 'Test timeout of 30000ms exceeded.\n  - waiting for locator("#submit")'
        nf = normalize_failure(text)
        assert nf.category is FailureCategory.FLAKY_BEHAVIOR
        assert nf.category_signals[0] == "flaky.timeout"

    def test_product_api_500(self) -> None:
        nf = normalize_failure(
            "API request to POST http://localhost:3000/api/checkout "
            "failed: 500 Internal Server Error"
        )
        assert nf.category is FailureCategory.PRODUCT_DEFECT
        assert nf.category_signals[0] == "product.api-status"
        assert nf.http_status == 500
        assert nf.endpoint == "http://localhost:3000/api/checkout"

    def test_automation_strict_mode(self) -> None:
        nf = normalize_failure('strict mode violation: locator("#save") resolved to 2 elements')
        assert nf.category is FailureCategory.AUTOMATION_DEFECT
        assert nf.category_signals[0] == "auto.strict"
        assert nf.selector == "#save"

    def test_unknown_when_no_rule_matches(self) -> None:
        nf = normalize_failure("Fatal process error: worker exited with code 137")
        assert nf.category is FailureCategory.UNKNOWN
        assert nf.category_signals == []

    def test_deterministic(self) -> None:
        text = "page.goto: Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000/products"
        assert normalize_failure(text).model_dump() == normalize_failure(text).model_dump()

    def test_evidence_capped(self) -> None:
        lines = [f"net::ERR_CONNECTION_REFUSED at attempt {i}" for i in range(50)]
        nf = normalize_failure("\n".join(lines))
        assert len(nf.evidence) <= failure.MAX_EVIDENCE_LINES
        assert all(len(line) <= failure.MAX_EVIDENCE_CHARS for line in nf.evidence)


# --- structured extraction ------------------------------------------------------


class TestExtraction:
    def test_http_status_failed_keyword(self) -> None:
        assert failure._extract_http_status("API request failed: 503 Service Unavailable") == 503

    def test_http_status_status_name(self) -> None:
        assert failure._extract_http_status("page.request.get: 403 Forbidden") == 403

    def test_http_status_got(self) -> None:
        assert failure._extract_http_status("Expected response status 200, got 500") == 500

    def test_http_status_none_when_absent(self) -> None:
        assert failure._extract_http_status("Expectation failed. Expected: 3, Received: 2") is None

    def test_selector_locator_call(self) -> None:
        assert failure._extract_selector('locator("text=Total")') == "text=Total"

    def test_selector_waiting_for(self) -> None:
        assert (
            failure._extract_selector('waiting for selector ".order-confirmed"')
            == ".order-confirmed"
        )

    def test_selector_none_when_absent(self) -> None:
        assert failure._extract_selector("net::ERR_CONNECTION_REFUSED") is None

    def test_endpoint_first_url(self) -> None:
        assert (
            failure._extract_endpoint('navigating to "http://localhost:3000/login"')
            == "http://localhost:3000/login"
        )


# --- golden set gate (build bible §22; exit criterion §19) ---------------------


class TestGoldenGate:
    def test_gate_met_30_of_30(self) -> None:
        golden = load_failure_golden_set(default_golden_path())
        assert len(golden.fixtures) == 30
        report = run_golden_set(golden)
        assert report.total == 30
        assert report.passed == 30
        assert report.failed == []
        assert report.gate_met is True

    def test_mismatches_empty_when_correct(self) -> None:
        fixture = FailureFixture(
            id="FAIL-901",
            title="correct expectation",
            raw=ENV_TEXT,
            expect=FailureExpectations(
                category=FailureCategory.ENVIRONMENT_DEFECT,
                http_status=503,
                endpoint="http://localhost:3000/api/orders",
                signals=["env.service"],
            ),
        )
        assert mismatches(fixture, normalize_failure(fixture.raw)) == []

    def test_mismatches_report_wrong_category(self) -> None:
        fixture = FailureFixture(
            id="FAIL-900",
            title="tampered expectation",
            raw="page.goto: Error: net::ERR_CONNECTION_REFUSED at http://localhost:3000/products",
            expect=FailureExpectations(category=FailureCategory.PRODUCT_DEFECT),
        )
        diffs = mismatches(fixture, normalize_failure(fixture.raw))
        assert any(diff.startswith("category:") for diff in diffs)

    def test_run_golden_set_reports_failures(self) -> None:
        golden = load_failure_golden_set(default_golden_path())
        bad = FailureFixture(
            id="FAIL-902",
            title="deliberately wrong",
            raw="Test timeout of 30000ms exceeded.",
            expect=FailureExpectations(category=FailureCategory.PRODUCT_DEFECT),
        )
        tampered = golden.model_copy(update={"fixtures": [bad]})
        report = run_golden_set(tampered)
        assert report.total == 1
        assert report.passed == 0
        assert report.gate_met is False
        assert report.failed[0].id == "FAIL-902"

    def test_load_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FailureGoldenSetError, match="not found"):
            load_failure_golden_set(tmp_path / "nope.json")

    def test_load_invalid_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text('{"fixtures": []}', encoding="utf-8")
        with pytest.raises(FailureGoldenSetError, match="invalid"):
            load_failure_golden_set(path)


# --- CLI -----------------------------------------------------------------------


class TestCli:
    def test_main_no_args_is_usage(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == EXIT_USAGE
        assert "error" in capsys.readouterr().err

    def test_main_single_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        file = tmp_path / "failure.txt"
        file.write_text(ENV_TEXT, encoding="utf-8")
        assert main([str(file)]) == EXIT_OK
        out = capsys.readouterr().out
        assert "environment_defect" in out
        assert "503" in out

    def test_main_single_file_json(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        file = tmp_path / "failure.txt"
        file.write_text(
            'strict mode violation: locator("#save") resolved to 2 elements',
            encoding="utf-8",
        )
        assert main([str(file), "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["category"] == "automation_defect"
        assert payload["selector"] == "#save"

    def test_main_stdin(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(failure.sys, "stdin", io.StringIO("net::ERR_CONNECTION_REFUSED"))
        assert main(["-"]) == EXIT_OK
        assert "environment_defect" in capsys.readouterr().out

    def test_main_missing_file_is_usage(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main([str(tmp_path / "missing.txt")]) == EXIT_USAGE
        assert "error" in capsys.readouterr().err

    def test_main_golden_gate_met(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--golden"]) == EXIT_OK
        out = capsys.readouterr().out
        assert "fixtures: 30" in out
        assert "met" in out

    def test_main_golden_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--golden", "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert payload["total"] == 30
        assert payload["passed"] == 30
        assert payload["gate_met"] is True

    def test_main_golden_missing_file_is_usage(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["--golden", "--golden-path", str(tmp_path / "missing.json")]) == EXIT_USAGE
        assert "error" in capsys.readouterr().err


# --- exit codes ------------------------------------------------------------------


def test_exit_code_constants() -> None:
    assert (EXIT_OK, EXIT_GATE_MISSED, EXIT_USAGE) == (0, 1, 2)
