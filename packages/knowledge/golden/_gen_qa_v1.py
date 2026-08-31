"""One-off generator: build qa_v1.json from the S5.1 retrieval corpus + questions.

Run: uv run python packages/knowledge/golden/_gen_qa_v1.py
(Delete after first use; qa_v1.json is the checked-in artifact.)
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
corpus = json.loads((HERE / "retrieval_v1.json").read_text(encoding="utf-8"))["corpus"]

questions = [
    {
        "id": "QA-001",
        "question": (
            "How many orders does the order history table show per page,"
            " and what is the default sort order?"
        ),
        "expect": {
            "in_scope": True,
            "grounded_facts": ["ten orders per page", "newest"],
            "cite_sources": ["REQ-001"],
        },
    },
    {
        "id": "QA-002",
        "question": (
            "After a password reset, what happens to the buyer's other sessions,"
            " and how long is the reset link valid?"
        ),
        "expect": {
            "in_scope": True,
            "grounded_facts": ["other sessions", "30 minutes"],
            "cite_sources": ["REQ-002"],
        },
    },
    {
        "id": "QA-003",
        "question": "In what order are discounts and tax applied to an order total?",
        "expect": {
            "in_scope": True,
            "grounded_facts": ["before tax", "discounted subtotal"],
            "cite_sources": ["standards/api-rules"],
        },
    },
    {
        "id": "QA-004",
        "question": "Within what time frame are refunds accepted after delivery?",
        "expect": {
            "in_scope": True,
            "grounded_facts": ["14 days"],
            "cite_sources": ["standards/api-rules"],
        },
    },
    {
        "id": "QA-005",
        "question": "Why did the CSV export test fail in test run 2026-01?",
        "expect": {
            "in_scope": True,
            "grounded_facts": ["ignores the page", "30000ms"],
            "cite_sources": ["run-2026-01"],
        },
    },
    {
        "id": "QA-006",
        "question": "Which test run completed with every test passed?",
        "expect": {
            "in_scope": True,
            "grounded_facts": ["2026-02"],
            "cite_sources": ["run-2026-02"],
        },
    },
    {
        "id": "QA-007",
        "question": "Which discount code is seeded for the demo, and what does it discount?",
        "expect": {
            "in_scope": True,
            "grounded_facts": ["SAVE10", "10 percent off"],
            "cite_sources": ["server/src/seed.js"],
        },
    },
    {
        "id": "QA-008",
        "question": (
            "Which Playwright locator APIs are used in the test code,"
            " and how many uses does getByRole have?"
        ),
        "expect": {
            "in_scope": True,
            "grounded_facts": ["getByRole", "18 uses"],
            "cite_sources": ["standards/test-conventions"],
        },
    },
    {
        "id": "QA-009",
        "question": "What is the capital city of France?",
        "expect": {"in_scope": False},
    },
    {
        "id": "QA-010",
        "question": "How do I compile a C program with GCC on Linux?",
        "expect": {"in_scope": False},
    },
    {"id": "QA-011", "question": "Who won the 1998 FIFA World Cup?", "expect": {"in_scope": False}},
    {
        "id": "QA-012",
        "question": "What is the boiling point of water at sea level?",
        "expect": {"in_scope": False},
    },
]

out = {
    "name": "qa-golden",
    "version": "v1",
    "description": (
        "S5.4 golden Q&A set: demo-shop corpus (same documents as retrieval_v1) + 12 "
        "questions (8 in-scope with grounded facts + expected sources, 4 out-of-scope "
        "that must be refused). Live gate: >= 80% in-scope grounded, 100% out-of-scope "
        "refused (build bible §19 S5.4)."
    ),
    "gate": {"in_scope_min": 0.8, "out_of_scope_refuse_min": 1.0},
    "corpus": corpus,
    "questions": questions,
}

(HERE / "qa_v1.json").write_text(
    json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"wrote {HERE / 'qa_v1.json'} ({len(corpus)} docs, {len(questions)} questions)")
