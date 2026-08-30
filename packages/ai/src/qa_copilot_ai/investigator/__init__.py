"""Failure Investigator eval (S4.1, build bible §19 Phase 4).

``run_investigation_eval`` scores the Failure Investigator's top-1 category
against the 30-broken-test golden set (``qa_copilot_execution.golden``) —
the S4.1 exit criterion is top-1 ≥ 80% (§19/§31.7). The CLI (``cli.main``)
emits the JSON artifact to stdout / ``--report`` and exits 0/1/2 like the
S1.4/S2.3 runners.
"""

from .runner import (
    FixtureInvestigationResult,
    InvestigationReport,
    InvestigationTotals,
    run_investigation_eval,
)

__all__ = [
    "FixtureInvestigationResult",
    "InvestigationReport",
    "InvestigationTotals",
    "run_investigation_eval",
]
