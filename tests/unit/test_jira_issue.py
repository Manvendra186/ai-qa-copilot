"""S7.4 failure → Jira issue mapping tests (build bible §19 S7.4).

``build_issue_payload`` is the single deterministic producer of the Jira
``fields`` body — pure input → output, the same input always yields the same
payload. These tests pin its invariants (summary format, ``Bug`` issuetype,
stable labels, ADF description shape, evidence cap) and the fail-loud
contract for input that cannot be mapped (§15: never invent facts).
"""

from __future__ import annotations

import pytest
from qa_copilot_integrations.jira.issue import (
    BASE_LABEL,
    ISSUE_TYPE_NAME,
    MAX_EVIDENCE,
    MAX_SUMMARY,
    JiraMappingError,
    build_description,
    build_issue_payload,
    build_summary,
)


def _failure(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "f-9f2c",
        "test_name": "tests/test_checkout.py::test_apply_coupon",
        "run_id": "run-2026-09-01-0042",
        "status": "failed",
        "error": "AssertionError: expected discount 0.10, got 0.05",
        "category": "regression",
        "confidence": 0.86,
        "root_cause": "Coupon discount applied before tax in pricing pipeline",
        "evidence": ["checkout/pricing.py:142"],
        "suggested_fix": "Apply coupon discount after tax in the pricing pipeline.",
    }
    base.update(overrides)
    return base


# --- summary ---------------------------------------------------------------------


def test_build_summary_category_only() -> None:
    assert build_summary("regression", None) == "[QA] regression"


def test_build_summary_category_and_root_cause() -> None:
    assert build_summary("regression", "tax before coupon") == (
        "[QA] regression: tax before coupon"
    )


def test_build_summary_defaults_unknown_category() -> None:
    assert build_summary(None, None) == "[QA] unknown"


def test_build_summary_truncates_to_limit() -> None:
    summary = build_summary("regression", "x" * 1000)
    assert len(summary) <= MAX_SUMMARY
    assert summary.startswith("[QA] regression")
    assert summary.endswith("…")


# --- payload shape ----------------------------------------------------------------


def test_build_issue_payload_full_shape() -> None:
    payload = build_issue_payload(_failure(), "QA")
    assert set(payload) == {"project", "issuetype", "summary", "description", "labels"}
    assert payload["project"] == {"key": "QA"}
    assert payload["issuetype"] == {"name": ISSUE_TYPE_NAME}
    assert ISSUE_TYPE_NAME == "Bug"
    assert payload["summary"] == (
        "[QA] regression: Coupon discount applied before tax in pricing pipeline"
    )
    assert payload["labels"] == [BASE_LABEL, "failure-regression"]


def test_build_issue_payload_labels_use_unknown_when_no_category() -> None:
    payload = build_issue_payload(_failure(category=None, root_cause=None), "QA")
    assert payload["summary"] == "[QA] unknown"
    assert payload["labels"] == [BASE_LABEL, "failure-unknown"]


def test_build_issue_payload_description_is_adf_doc() -> None:
    description = build_issue_payload(_failure(), "QA")["description"]
    assert description["type"] == "doc"
    assert description["version"] == 1
    types = {node["type"] for node in description["content"]}
    assert "heading" in types  # Diagnosis / Failure section headers
    assert "paragraph" in types
    text_blob = str(description["content"])
    assert "Category: regression" in text_blob
    assert "Root cause: Coupon discount applied before tax" in text_blob
    assert "Test: tests/test_checkout.py::test_apply_coupon" in text_blob


def test_build_description_caps_evidence_lines() -> None:
    evidence = [f"line-{i}" for i in range(MAX_EVIDENCE + 3)]
    description = build_description(
        failure_id=None,
        test_name=None,
        run_id=None,
        status=None,
        error=None,
        category="regression",
        confidence=None,
        root_cause=None,
        evidence=evidence,
        suggested_fix=None,
    )
    lists = [n for n in description["content"] if n["type"] == "list"]
    assert len(lists) == 1
    items = lists[0]["content"]
    assert len(items) == MAX_EVIDENCE
    kept = [item["content"][0]["content"][0]["text"] for item in items]
    assert kept == evidence[:MAX_EVIDENCE]


def test_mapping_is_deterministic() -> None:
    first = build_issue_payload(_failure(), "QA")
    second = build_issue_payload(dict(_failure()), "QA")
    assert first == second


# --- fail loud (never guess) ------------------------------------------------------


def test_build_issue_payload_invalid_project_key_raises() -> None:
    with pytest.raises(ValueError, match="project key"):
        build_issue_payload(_failure(), "bad key!")


def test_build_issue_payload_non_string_category_raises() -> None:
    with pytest.raises(JiraMappingError, match="category"):
        build_issue_payload(_failure(category=42), "QA")


def test_build_issue_payload_non_string_error_raises() -> None:
    with pytest.raises(JiraMappingError, match="error"):
        build_issue_payload(_failure(error={"not": "text"}), "QA")


def test_build_issue_payload_bad_confidence_raises() -> None:
    with pytest.raises(JiraMappingError, match="confidence"):
        build_issue_payload(_failure(confidence=1.5), "QA")


def test_build_issue_payload_string_confidence_raises() -> None:
    with pytest.raises(JiraMappingError, match="confidence"):
        build_issue_payload(_failure(confidence="high"), "QA")


def test_build_issue_payload_non_list_evidence_raises() -> None:
    with pytest.raises(JiraMappingError, match="evidence"):
        build_issue_payload(_failure(evidence="not a list"), "QA")
