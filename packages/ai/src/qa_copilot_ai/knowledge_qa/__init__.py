"""S5.4 Knowledge Q&A eval — golden Q&A set runner + CLI (build bible §19 Phase 5).

``runner`` scores the :class:`~qa_copilot_ai.agents.KnowledgeQAAgent` over
the golden Q&A set (in-scope grounded answers + out-of-scope refusals, the
§19 S5.4 live gate); ``cli`` is the ``knowledge-qa run`` entry point with
the shared exit-code contract (0 targets met · 1 targets missed · 2
configuration error).
"""

from .cli import build_parser, main
from .runner import (
    QAAnsweringAgent,
    QAQuestionResult,
    QAReport,
    QATotals,
    run_qa_eval,
)

__all__ = [
    "QAAnsweringAgent",
    "QAQuestionResult",
    "QAReport",
    "QATotals",
    "build_parser",
    "main",
    "run_qa_eval",
]
