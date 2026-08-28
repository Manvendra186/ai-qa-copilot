"""Execution contracts (build bible §15, §31.11; S3.1).

The execution worker produces a :class:`RunReport` — a frozen,
JSON-serializable snapshot of one Playwright run: per-test outcomes plus the
artifacts captured for each test (trace / screenshot / video / console /
network / dom / log, §15). The report knows nothing about the database;
``qa_copilot_repository.runs`` maps it onto the §10 ``test_runs`` /
``test_results`` / ``artifacts`` rows.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from qa_copilot_domain.enums import ArtifactType, RunStatus, TestResultStatus


class ArtifactReport(BaseModel):
    """One stored artifact (build bible §10 ``artifacts``).

    ``uri`` is store-relative (``runs/{run_id}/{test_id}/{name}``, §31.11);
    ``metadata`` carries non-identifying details (size, source file name).
    """

    model_config = ConfigDict(frozen=True)

    type: ArtifactType
    uri: str = Field(min_length=3)
    metadata: dict[str, object] = Field(default_factory=dict)


class TestResultReport(BaseModel):
    """Outcome of one test in a run (build bible §10 ``test_results``)."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(min_length=1)
    file: str | None = None
    status: TestResultStatus
    duration_ms: int = Field(default=0, ge=0)
    #: Raw failure text (message + snippet); ``None`` when the test passed.
    error: str | None = None
    #: Deterministic slug used as the ``{test_id}`` store path segment.
    slug: str = Field(min_length=1)
    artifacts: list[ArtifactReport] = Field(default_factory=list)


class RunTotals(BaseModel):
    """Per-status counts (``total == passed + failed + flaky + skipped``)."""

    model_config = ConfigDict(frozen=True)

    total: int = Field(default=0, ge=0)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    flaky: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)


class RunReport(BaseModel):
    """Full result of one execution-worker invocation (build bible §15).

    ``status`` is the *worker* outcome (the §31.2 state machine); test
    outcomes live in ``results``. ``error`` explains a worker failure.
    """

    model_config = ConfigDict(frozen=True)
    schema_version: int = 1
    status: RunStatus
    target_dir: str
    base_url: str | None = None
    commit_sha: str | None = None
    browser: str | None = None
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    totals: RunTotals = Field(default_factory=lambda: RunTotals())
    error: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    results: list[TestResultReport] = Field(default_factory=list)
