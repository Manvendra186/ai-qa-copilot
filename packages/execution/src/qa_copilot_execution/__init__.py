"""Playwright execution and artifacts (build bible §7, §15, §31.11).

S3.1: the execution worker — :func:`run_playwright` spawns the target
repository's Playwright suite, maps its JSON report onto the domain status
vocabulary, and captures the §15 artifact set into an
:class:`ArtifactStore` under the §31.11 layout ``runs/{run_id}/{test_id}/{name}``.
The worker is database-free: it produces a frozen :class:`RunReport`;
:func:`qa_copilot_repository.runs.persist_run` maps that report onto the §10
``test_runs`` / ``test_results`` / ``artifacts`` rows.
"""

from .report import ArtifactReport, RunReport, RunTotals, TestResultReport
from .runner import PlaywrightConfig, run_playwright
from .store import ArtifactStore, ArtifactStoreError

__version__ = "0.1.0"

__all__ = [
    "ArtifactReport",
    "ArtifactStore",
    "ArtifactStoreError",
    "PlaywrightConfig",
    "RunReport",
    "RunTotals",
    "TestResultReport",
    "__version__",
    "run_playwright",
]
