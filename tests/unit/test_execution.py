"""Execution package tests (S3.1): artifact store, report parsing, CLI exit codes.

Pure-function tests stay hermetic (no Playwright, no network). The CLI tests
drive a *fake* target repo whose ``node_modules/.bin/playwright`` shim copies
a prepared JSON report to ``PLAYWRIGHT_JSON_OUTPUT_FILE`` — the same
contract the real ``@playwright/test`` JSON reporter honours — so the worker
code path (spawn → report file → artifacts → exit code) runs end to end
without a browser.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from qa_copilot_domain.enums import ArtifactType, RunStatus, TestResultStatus
from qa_copilot_execution import cli
from qa_copilot_execution.report import TestResultReport
from qa_copilot_execution.runner import (
    _classify,
    _error_text,
    _extract_json,
    _fallback_files,
    _iter_specs,
    _last_result,
    _load_report_file,
    _slugify,
    _spec_status,
    _suites,
    _totals,
)
from qa_copilot_execution.store import ArtifactStore, ArtifactStoreError, check_segment

#: The real demo-app test's Playwright output-dir slug (verified on disk).
PASS_SLUG = "demo-login-products-signs-in-and-sees-the-product-catalog"


# --- ArtifactStore (build bible §31.11 layout) ---------------------------------


class TestArtifactStore:
    def test_store_copies_into_3111_layout(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        src = tmp_path / "src.bin"
        src.write_bytes(b"trace-bytes")
        uri, size = store.store("run-1", "test-1", "trace", src)
        assert uri == "runs/run-1/test-1/trace"
        assert size == 11
        assert store.resolve(uri).read_bytes() == b"trace-bytes"

    def test_store_missing_source_raises(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        with pytest.raises(ArtifactStoreError, match="source file missing"):
            store.store("r", "t", "n", tmp_path / "missing")

    def test_store_never_overwrites(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        src = tmp_path / "s"
        src.write_text("1", encoding="utf-8")
        store.store("r", "t", "n", src)
        with pytest.raises(ArtifactStoreError, match="already stored"):
            store.store("r", "t", "n", src)

    @pytest.mark.parametrize("bad", ["", "..", "../r", "a/b", ".hidden", "-dash", "x y"])
    def test_check_segment_rejects(self, bad: str) -> None:
        with pytest.raises(ArtifactStoreError):
            check_segment(bad, "segment")

    def test_check_segment_accepts(self) -> None:
        assert check_segment("run-1.2_3", "segment") == "run-1.2_3"

    def test_store_rejects_traversal_in_any_slot(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        src = tmp_path / "s"
        src.write_text("x", encoding="utf-8")
        with pytest.raises(ArtifactStoreError):
            store.store("..", "t", "n", src)
        with pytest.raises(ArtifactStoreError):
            store.store("r", "..", "n", src)
        with pytest.raises(ArtifactStoreError):
            store.store("r", "t", "..", src)

    def test_store_text_writes_and_returns_size(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        uri, size = store.store_text("r", "t", "log", "line1\nline2\n")
        assert uri == "runs/r/t/log"
        # Written in text mode (platform line endings) — size is the on-disk bytes.
        assert size == (tmp_path / uri).stat().st_size
        assert store.resolve(uri).read_text(encoding="utf-8") == "line1\nline2\n"

    def test_resolve_rejects_escape(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        with pytest.raises(ArtifactStoreError, match="escapes"):
            store.resolve("../outside")

    def test_delete_run_removes_only_that_run(self, tmp_path: Path) -> None:
        store = ArtifactStore(tmp_path)
        src = tmp_path / "s"
        src.write_text("x", encoding="utf-8")
        store.store("keep", "t", "a", src)
        store.store("drop", "t", "a", src)
        store.store("drop", "t", "b", src)
        assert store.delete_run("drop") == 2
        assert not (tmp_path / "runs" / "drop").exists()
        assert (tmp_path / "runs" / "keep" / "t" / "a").is_file()

    def test_delete_run_missing_returns_zero(self, tmp_path: Path) -> None:
        assert ArtifactStore(tmp_path).delete_run("nope") == 0


# --- Report parsing (Playwright JSON reporter, both schemas) -------------------


class TestSpecStatus:
    """Old schema: spec.status; new (1.62): results[].status + spec.ok."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ({"status": "expected"}, TestResultStatus.PASSED),
            ({"status": "unexpected"}, TestResultStatus.FAILED),
            ({"status": "flaky"}, TestResultStatus.FLAKY),
            ({"status": "skipped"}, TestResultStatus.SKIPPED),
            (
                {"ok": True, "tests": [{"results": [{"status": "passed"}]}]},
                TestResultStatus.PASSED,
            ),
            (
                {"ok": False, "tests": [{"results": [{"status": "failed"}]}]},
                TestResultStatus.FAILED,
            ),
            (
                {"tests": [{"results": [{"status": "failed"}, {"status": "passed"}]}]},
                TestResultStatus.FLAKY,
            ),
            ({"tests": [{"results": [{"status": "skipped"}]}]}, TestResultStatus.SKIPPED),
            ({"ok": False}, TestResultStatus.FAILED),
            ({}, TestResultStatus.FAILED),
            # An explicit old-schema spec status wins over result details.
            (
                {"status": "flaky", "tests": [{"results": [{"status": "failed"}]}]},
                TestResultStatus.FLAKY,
            ),
        ],
    )
    def test_status_mapping(self, spec: dict[str, object], expected: TestResultStatus) -> None:
        assert _spec_status(spec) is expected


class TestSlugAndIteration:
    DEMO_REPORT = {
        "suites": [
            {
                "file": "demo.spec.js",
                "title": "demo.spec.js",
                "suites": [
                    {
                        "title": "login + products",
                        "specs": [{"title": "signs in and sees the product catalog"}],
                    }
                ],
            }
        ]
    }

    def test_iter_specs_carries_title_chain(self) -> None:
        entries = _iter_specs(_suites(self.DEMO_REPORT))
        assert len(entries) == 1
        file_name, spec, chain = entries[0]
        assert file_name == "demo.spec.js"
        assert spec["title"] == "signs in and sees the product catalog"
        # File stem (ALL extensions stripped) + describe titles.
        assert chain == ["demo", "login + products"]

    def test_slug_matches_playwright_output_dir(self) -> None:
        _, spec, chain = _iter_specs(_suites(self.DEMO_REPORT))[0]
        slug = _slugify(" ".join([*chain, str(spec["title"])]))
        assert slug == PASS_SLUG

    def test_classify_by_canonical_name(self) -> None:
        assert _classify("trace", "test-results/x/trace.zip") is ArtifactType.TRACE
        assert _classify("video", "test-results/x/video.webm") is ArtifactType.VIDEO
        assert (
            _classify("screenshot", "test-results/x/test-finished-1.png") is ArtifactType.SCREENSHOT
        )
        assert _classify("console.jsonl", "test-results/x/console.jsonl") is ArtifactType.CONSOLE
        assert _classify("network.jsonl", "test-results/x/network.jsonl") is ArtifactType.NETWORK
        assert _classify("error context", "test-results/x/context.md") is ArtifactType.DOM

    def test_classify_by_extension(self) -> None:
        assert _classify("something", "test-results/x/video.webm") is ArtifactType.VIDEO
        assert _classify("something", "test-results/x/page.png") is ArtifactType.SCREENSHOT
        assert _classify("something", "test-results/x/thing.bin") is None

    def test_extract_json_tolerates_leading_noise(self) -> None:
        assert _extract_json('{"suites": []}') == {"suites": []}
        assert _extract_json('Running 1 test\n[{"specs": []}]') == [{"specs": []}]
        assert _extract_json("no json here") is None

    def test_load_report_file(self, tmp_path: Path) -> None:
        good = tmp_path / "r.json"
        good.write_text('{"ok": 1}', encoding="utf-8")
        assert _load_report_file(good) == {"ok": 1}
        assert _load_report_file(tmp_path / "missing.json") is None
        bad = tmp_path / "bad.json"
        bad.write_text("{nope", encoding="utf-8")
        assert _load_report_file(bad) is None

    def test_fallback_files_match_the_test_dir(self, tmp_path: Path) -> None:
        base = tmp_path / "test-results"
        (base / PASS_SLUG).mkdir(parents=True)
        (base / PASS_SLUG / "console.jsonl").write_text("c", encoding="utf-8")
        (base / PASS_SLUG / "network.jsonl").write_text("n", encoding="utf-8")
        (base / PASS_SLUG / "error-context.md").write_text("e", encoding="utf-8")
        other = base / "another-test"
        other.mkdir()
        (other / "console.jsonl").write_text("x", encoding="utf-8")

        found = _fallback_files(tmp_path, "test-results", PASS_SLUG)
        assert {kind for kind, _ in found} == {
            ArtifactType.CONSOLE,
            ArtifactType.NETWORK,
            ArtifactType.DOM,
        }
        assert all(path.parent.name == PASS_SLUG for _, path in found), (
            "files from other tests must not leak in"
        )

    def test_fallback_files_missing_output_dir(self, tmp_path: Path) -> None:
        assert _fallback_files(tmp_path, "test-results", "x") == []

    def test_last_result_summed_duration(self) -> None:
        spec: dict[str, object] = {
            "tests": [
                {
                    "results": [
                        {"duration": 100},
                        {"duration": 250, "attachments": []},
                    ]
                }
            ]
        }
        last, total = _last_result(spec)
        assert last is not None and last.get("attachments") == []
        assert total == 350

    def test_totals_counts_each_status(self) -> None:
        results = [
            TestResultReport(title=f"t{i}", slug=f"t{i}", status=status)
            for i, status in enumerate(
                (
                    TestResultStatus.PASSED,
                    TestResultStatus.FAILED,
                    TestResultStatus.FLAKY,
                    TestResultStatus.SKIPPED,
                )
            )
        ]
        totals = _totals(results)
        assert totals.total == 4
        assert totals.passed == 1
        assert totals.failed == 1
        assert totals.flaky == 1
        assert totals.skipped == 1

    def test_error_text_joins_message_and_snippet(self) -> None:
        text = _error_text({"errors": [{"message": "boom", "snippet": "at spec.js:3"}]})
        assert text == "boom\n\nat spec.js:3"
        assert _error_text({"errors": []}) is None
        assert _error_text(None) is None


# --- CLI exit codes (§31.11 worker contract) ------------------------------------


def _report(status: str, ok: bool, errors: list[dict[str, str]] | None = None) -> dict[str, object]:
    """A minimal 1.62-schema JSON report for one test (with attachments)."""
    result: dict[str, object] = {"status": status, "duration": 450, "annotations": []}
    if errors is not None:
        result["errors"] = errors
    result["attachments"] = [
        {"name": "video", "path": f"test-results/{PASS_SLUG}/video.webm"},
        {"name": "screenshot", "path": f"test-results/{PASS_SLUG}/test-finished-1.png"},
    ]
    return {
        "config": {"projects": []},
        "suites": [
            {
                "file": "demo.spec.js",
                "title": "demo.spec.js",
                "suites": [
                    {
                        "title": "login + products",
                        "specs": [
                            {
                                "title": "signs in and sees the product catalog",
                                "ok": ok,
                                "tests": [{"status": status, "results": [result]}],
                            }
                        ],
                    }
                ],
            }
        ],
        "stats": {},
    }


def _write_fake_target(
    target: Path,
    report: dict[str, object] | None,
    files: dict[str, bytes] | None = None,
) -> None:
    """A target repo whose ``playwright`` shim 'emits' the JSON report file.

    The shim copies a prepared report to ``PLAYWRIGHT_JSON_OUTPUT_FILE`` —
    exactly what the real JSON reporter does — or exits non-zero with no
    report (worker-failure path).
    """
    bin_dir = target / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    if report is not None:
        (bin_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    if os.name == "nt":
        shim = bin_dir / "playwright.cmd"
        if report is not None:
            shim.write_text(
                '@echo off\r\ncopy /Y "%~dp0report.json" "%PLAYWRIGHT_JSON_OUTPUT_FILE%" >nul\r\n',
                encoding="ascii",
            )
        else:
            shim.write_text("@echo off\r\nexit /b 3\r\n", encoding="ascii")
    else:
        shim = bin_dir / "playwright"
        if report is not None:
            shim.write_text(
                '#!/bin/sh\ncp "$(dirname "$0")/report.json" "$PLAYWRIGHT_JSON_OUTPUT_FILE"\n',
                encoding="ascii",
            )
        else:
            shim.write_text("#!/bin/sh\nexit 3\n", encoding="ascii")
        shim.chmod(0o755)
    for rel, content in (files or {}).items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _run_cli(*args: str, cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "qa_copilot_execution", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestCliExitCodes:
    def test_missing_target_exits_usage(self, tmp_path: Path) -> None:
        code, _out, err = _run_cli(str(tmp_path / "no-such-dir"), cwd=tmp_path)
        assert code == cli.EXIT_USAGE
        assert "target directory not found" in err

    def test_missing_positional_exits_usage(self) -> None:
        with pytest.raises(SystemExit) as exc:
            cli.main([])
        assert exc.value.code == cli.EXIT_USAGE

    def test_passed_run_exits_ok_and_stores_all_artifact_kinds(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        files = {
            f"test-results/{PASS_SLUG}/video.webm": b"WEBM",
            f"test-results/{PASS_SLUG}/test-finished-1.png": b"PNG",
            f"test-results/{PASS_SLUG}/console.jsonl": b'{"type":"console"}\n',
            f"test-results/{PASS_SLUG}/network.jsonl": b'{"type":"request"}\n',
        }
        _write_fake_target(target, _report("passed", True), files)
        store = tmp_path / "store"

        code, out, err = _run_cli(
            str(target), "--store", str(store), "--run-id", "t-pass", "--json", cwd=tmp_path
        )
        assert code == cli.EXIT_OK, err
        report = json.loads(out)
        assert report["status"] == RunStatus.COMPLETED.value
        assert report["totals"] == {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "flaky": 0,
            "skipped": 0,
        }
        assert report["results"][0]["status"] == TestResultStatus.PASSED.value

        run_dir = store / "runs" / "t-pass" / PASS_SLUG
        assert run_dir.joinpath("video").read_bytes() == b"WEBM"
        assert run_dir.joinpath("screenshot").read_bytes() == b"PNG"
        assert run_dir.joinpath("console").read_bytes() == b'{"type":"console"}\n'
        assert run_dir.joinpath("network").read_bytes() == b'{"type":"request"}\n'

    def test_failed_test_exits_1_and_stores_failure_log(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        errors = [
            {"message": "expect(received).toBe(expected)", "snippet": "at e2e/demo.spec.js:20:5"}
        ]
        _write_fake_target(target, _report("failed", False, errors=errors))
        store = tmp_path / "store"

        code, out, err = _run_cli(
            str(target), "--store", str(store), "--run-id", "t-fail", cwd=tmp_path
        )
        assert code == cli.EXIT_TESTS_FAILED, err
        assert "run t-fail: completed" in out

        log = store / "runs" / "t-fail" / PASS_SLUG / "log"
        assert log.is_file()
        text = log.read_text(encoding="utf-8")
        assert "expect(received).toBe(expected)" in text
        assert "at e2e/demo.spec.js:20:5" in text

    def test_no_report_exits_worker_failed(self, tmp_path: Path) -> None:
        target = tmp_path / "target"
        _write_fake_target(target, None)
        store = tmp_path / "store"

        code, out, err = _run_cli(
            str(target), "--store", str(store), "--run-id", "t-nope", cwd=tmp_path
        )
        assert code == cli.EXIT_WORKER_FAILED, out + err
        assert "without a JSON report" in out
        assert "run t-nope: failed" in out
