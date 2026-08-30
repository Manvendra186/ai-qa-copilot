"""S5.1 source adapter tests: requirements, test cases, standards, history, repo files.

All adapters are pure functions of their inputs (no DB, no LLM — build
bible §19 S5.1), so the same input must always yield the same documents.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qa_copilot_domain import (
    LocatorStyle,
    Priority,
    Requirement,
    RiskLevel,
    TestCase,
    TestConventions,
    TestScript,
    TestType,
)
from qa_copilot_knowledge.models import KnowledgeSourceType, RunRecord, TestOutcomeRecord
from qa_copilot_knowledge.sources import (
    history_documents,
    repository_file_documents,
    requirement_documents,
    standard_documents,
)


class TestRequirementDocuments:
    def test_contains_criteria_and_risk(self) -> None:
        req = Requirement(
            id="REQ-1",
            title="Order history",
            content="Buyers see past orders.",
            acceptance_criteria=["Ten orders per page", "CSV export"],
            risk=RiskLevel.HIGH,
        )
        docs = requirement_documents([req])
        assert len(docs) == 1
        doc = docs[0]
        assert doc.source_type is KnowledgeSourceType.REQUIREMENT
        assert doc.source_ref == "REQ-1"
        assert doc.id == "REQ-1"
        assert "Ten orders per page" in doc.content
        assert "Risk: high" in doc.content
        assert doc.metadata == {"risk": "high"}

    def test_fallback_ref_when_no_id(self) -> None:
        req = Requirement(title="Password Reset Flow", content="Reset via email link.")
        docs = requirement_documents([req])
        assert docs[0].source_ref == "requirement-password-reset-flow"

    def test_includes_linked_test_cases(self) -> None:
        tc = TestCase(
            id="TC-1",
            title="Order history paginates",
            type=TestType.FUNCTIONAL,
            priority=Priority.HIGH,
            preconditions=["Signed-in buyer"],
            steps=["Open order history", "Move to page two"],
            expected_results=["Page two shows orders 11 to 20"],
            requirement_refs=["REQ-1"],
        )
        docs = requirement_documents([], [tc])
        doc = docs[0]
        assert doc.source_type is KnowledgeSourceType.TEST_CASE
        assert "1. Open order history" in doc.content
        assert "2. Move to page two" in doc.content
        assert "Requirement refs: REQ-1" in doc.content
        assert doc.metadata == {"type": "functional", "priority": "high"}


class TestStandardDocuments:
    def test_renders_locator_styles_and_scripts(self) -> None:
        conventions = TestConventions(
            test_file_patterns=["*.spec.ts"],
            locator_styles=[LocatorStyle(api="getByRole", framework="playwright", count=18)],
            page_object_files=["e2e/pages/order-history.ts"],
            fixture_files=["e2e/fixtures.ts"],
            helper_files=["e2e/helpers.ts"],
            test_configs=["playwright.config.ts"],
            test_ids=["checkout-button"],
            base_url="http://localhost:3000",
            test_scripts=[TestScript(name="e2e", command="playwright test")],
            notes=["Use data-testid for dynamic rows"],
        )
        docs = standard_documents(conventions, repo_name="demo-shop")
        assert len(docs) == 1
        doc = docs[0]
        assert doc.source_ref == "standards/test-conventions"
        assert "Test conventions for demo-shop" in doc.content
        assert "- getByRole (playwright, 18 uses)" in doc.content
        assert "- e2e: playwright test" in doc.content
        assert "- Use data-testid for dynamic rows" in doc.content
        assert doc.metadata == {"base_url": "http://localhost:3000"}

    def test_empty_conventions_still_yield_a_document(self) -> None:
        docs = standard_documents(TestConventions())
        assert len(docs) == 1
        assert docs[0].content  # non-blank header
        assert "Locator APIs" not in docs[0].content


# --- continued: history + repository-file adapters ---


class TestHistoryDocuments:
    def test_renders_outcomes_and_failures(self) -> None:
        run = RunRecord(
            run_id="r-1",
            status="completed",
            commit_sha="abc123",
            results=[
                TestOutcomeRecord(test="a.spec.ts > passes", status="passed"),
                TestOutcomeRecord(
                    test="a.spec.ts > fails",
                    status="failed",
                    failure_category="product_defect",
                    failure_root_cause="endpoint ignores page param",
                    failure_evidence=["Test timeout of 30000ms exceeded"],
                ),
            ],
        )
        docs = history_documents([run])
        doc = docs[0]
        assert doc.source_type is KnowledgeSourceType.RUN_HISTORY
        assert doc.source_ref == "run-r-1"
        assert "commit abc123" in doc.content
        assert "a.spec.ts > passes: passed" in doc.content
        assert "product_defect, endpoint ignores page param" in doc.content
        assert "evidence: Test timeout of 30000ms exceeded" in doc.content

    def test_evidence_is_capped(self) -> None:
        run = RunRecord(
            run_id="r-2",
            status="completed",
            results=[
                TestOutcomeRecord(
                    test="t",
                    status="failed",
                    failure_category="flaky_behavior",
                    failure_evidence=["x" * 500, "y" * 500, "never shown"],
                )
            ],
        )
        content = history_documents([run])[0].content
        assert "x" * 201 not in content  # 500-char lines truncated to the cap
        assert "x" * 100 in content
        assert "y" * 201 not in content
        assert "y" * 100 in content
        assert "never shown" not in content  # only the first two lines are kept


class TestRepositoryFileDocuments:
    def test_walks_and_references_relative_paths(self, tmp_path: Path) -> None:
        (tmp_path / "e2e").mkdir()
        spec = tmp_path / "e2e" / "login.spec.ts"
        spec.write_text("test('login', () => {});", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("def app():\n    return 'ok'\n", encoding="utf-8")
        docs, capped = repository_file_documents(tmp_path)
        assert not capped
        refs = {d.source_ref for d in docs}
        assert refs == {"e2e/login.spec.ts", "src/app.py"}
        doc = next(d for d in docs if d.source_ref == "e2e/login.spec.ts")
        assert doc.metadata["is_test"] is True
        assert doc.metadata["language"] == "ts"

    def test_skips_lockfiles_and_generated_noise(self, tmp_path: Path) -> None:
        for name in ["package-lock.json", "bundle.min.js", "app.js.map", "notes.txt"]:
            (tmp_path / name).write_text("x", encoding="utf-8")
        docs, _ = repository_file_documents(tmp_path)
        assert {d.source_ref for d in docs} == {"notes.txt"}

    def test_respects_max_files_and_reports_capped(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"f{i}.txt").write_text(f"file {i}", encoding="utf-8")
        docs, capped = repository_file_documents(tmp_path, max_files=3)
        assert len(docs) == 3
        assert capped

    def test_missing_root_raises(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            repository_file_documents(tmp_path / "nope")

    def test_unreadable_files_are_skipped_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
        (tmp_path / "bad.bin").write_bytes(b"\x00\x01\x02binary")
        (tmp_path / "weird.xyz").write_text("unknown extension", encoding="utf-8")
        docs, _ = repository_file_documents(tmp_path)
        assert {d.source_ref for d in docs} == {"ok.txt"}
