"""Job service — the mandatory async pattern (build bible §11, §31.2; S0.9).

Every AI-backed endpoint returns ``202 Accepted`` + ``{job_id}`` and reports
progress two ways (§31.2: "no synchronous AI calls in HTTP handlers"):

- ``GET /api/v1/jobs/{id}`` — status, progress, result/error refs
- ``GET /api/v1/events`` — live SSE. The frame shape is the S0.7 web mock
  contract (``job.started`` → per stage ``stage.started`` / ``progress`` /
  ``stage.completed`` → ``job.completed``), so the shell's ``useJobEvents``
  hook is contract-compatible; S0.9 adds ``job.failed``.

S0.9 ships a **stub agent** for ``requirement_analysis`` (:class:`StubAgent`):
it simulates the §4 six-stage pipeline so the whole contract — 202, state
machine, progress accounting, SSE ordering/replay, failure path — is verifiable
before the real LLM-backed agents (S1.x, ``qa_copilot_ai`` §31.1) replace it
through the same :class:`JobAgent` protocol.

Components (single process, single event loop — Phase 0; a Redis pub/sub
backend can later replace :class:`EventBus` behind the same call surface):

- :class:`EventBus` — fan-out of events to SSE subscribers + a per-job replay
  buffer, so a subscriber that connects *after* the job started still receives
  its events (deduped against the live queue by sequence number: no gaps, no
  duplicates, in order — see :func:`sse_stream`).
- :class:`JobRunner` — owns the state machine ``pending → running →
  completed | failed`` (§31.2): one task per job, idempotent ``start`` (safe
  from any thread), progress persisted to ``jobs.progress``, one
  ``ai_sessions`` row per job (the audit anchor for S1.x ``ai_actions``,
  §31.5), a reaper for jobs orphaned by a crash, clean shutdown.
- :class:`JobContext` / :class:`JobAgent` — the agent contract.
- :class:`StubAgent` — the S0.9 stand-in agent.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import re
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from qa_copilot_ai.agents import (
    ADVISOR_NAME,
    SUMMARY_SOURCE_STUB,
    AdvisorInput,
    AutomationAgentResult,
    AutomationInput,
    GeneratedTest,
    KnowledgeContext,
    KnowledgeQAAgentResult,
    KnowledgeQAInput,
    QAAnswer,
    RegressionAdvisorAgent,
    RegressionAdvisorResult,
    RequirementAgent,
    RequirementAgentResult,
    RequirementInput,
    TestDesignAgent,
    TestDesignAgentResult,
    TestDesignInput,
    stub_summary,
)
from qa_copilot_ai.gateway import AICallResult, LLMGateway, TokenUsage
from qa_copilot_ai.prompts import PromptStore
from qa_copilot_domain import ImpactSet, Priority, RecommendationSet, RiskLevel, RiskRanking
from qa_copilot_domain import TestCase as DomainTestCase
from qa_copilot_domain.enums import JobStatus, JobType
from qa_copilot_execution import PlaywrightConfig, run_playwright
from qa_copilot_repository import (
    TestRiskInput,
    build_risk_ranking,
    changed_files_from_range,
    extract_conventions,
    impact_from_session,
    models,
    project_test_history,
    recommend,
    scan_repository,
    strongest_impact_kind,
)
from qa_copilot_repository import (
    db as repo_db,
)
from qa_copilot_repository import generated_tests as repo_generated_tests
from qa_copilot_repository import requirements as repo_requirements
from qa_copilot_repository import runs as repo_runs
from sqlalchemy import Engine, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from qa_copilot_api.knowledge_store import (
    build_project_knowledge,
    persist_project_knowledge,
    search_project_knowledge,
)

__all__ = [
    "AutomationJobAgent",
    "AutomationRunner",
    "AutomationStub",
    "Event",
    "EventBus",
    "JobAgent",
    "JobContext",
    "JobRunner",
    "JobSnapshot",
    "KnowledgeAskJobAgent",
    "KnowledgeIndexJobAgent",
    "KnowledgeQARunner",
    "KnowledgeQARefusalStub",
    "RegressionJobAgent",
    "RequirementJobAgent",
    "RunExecutionJobAgent",
    "StubAgent",
    "TestDesignJobAgent",
    "TERMINAL_EVENTS",
    "format_sse",
    "sse_stream",
]

logger = logging.getLogger(__name__)

#: Event names that end a job's stream (client closes on any of them).
TERMINAL_EVENTS: frozenset[str] = frozenset({"job.completed", "job.failed", "job.cancelled"})
#: Job row statuses that mean "no more events are coming".
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

#: Replay buffer: max events kept per job after it ends (dev tooling scale).
BUFFER_MAX = 512
#: Replay buffer: drop a finished job's buffer this long after its terminal event.
BUFFER_TTL_S = 600.0
#: SSE subscriber queues are bounded: a slow reader drops (reconnect + replay
#: recovers) instead of stalling the publisher or every other subscriber.
SUBSCRIBER_QUEUE = 256
#: SSE idle keepalive (SSE comment frame) — keeps proxies from timing the stream out.
HEARTBEAT_S = 15.0
#: A single SSE stream must not live forever (client bug / abandoned tab).
STREAM_MAX_AGE_S = 1800.0


@dataclass(frozen=True, slots=True)
class Event:
    """One job event: SSE ``event:`` name + ``data:`` JSON payload + sequence."""

    event: str
    data: dict[str, Any]
    seq: int = 0


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Job-row state captured *before* streaming starts.

    The request session is closed once the route returns, so the stream reads
    the row exactly once up front (used to synthesize a terminal frame when
    the replay buffer has already been evicted).
    """

    status: JobStatus
    project_id: str | None
    error: str | None
    output_ref: str | None


@dataclass(slots=True)
class JobContext:
    """What a job agent gets to work with (S0.9: stub-friendly, S1.x: real).

    ``input`` is the job's inline input payload (e.g. the requirement text);
    ``emit`` publishes a progress event to the bus (and persists progress for
    ``progress`` events) — agents never touch the database directly.

    ``ai_session_id`` is the job's ``ai_sessions`` audit anchor (created by the
    runner before the agent runs); S1.x agents record their ``ai_actions`` rows
    against it.
    """

    job_id: str
    project_id: str | None
    job_type: JobType
    input: dict[str, Any]
    emit: Callable[[str, dict[str, Any]], Awaitable[None]]
    ai_session_id: str | None = None


class JobAgent(Protocol):
    """Contract every job agent implements (real LLM agents arrive at S1.x)."""

    #: Pipeline stages the agent works through (drives ``job.started.stages``).
    stages: tuple[str, ...]

    async def run(self, ctx: JobContext) -> str | None:
        """Execute the job; return an ``output_ref`` (or ``None``).

        Progress is reported by ``await ctx.emit(event, data)`` using the
        S0.7 shell contract names (``stage.started`` / ``progress`` /
        ``stage.completed``). Raising fails the job (``job.failed``).
        """
        ...


class EventBus:
    """In-process event fan-out for the SSE endpoint (single event loop, no locks).

    - ``publish`` is non-blocking: bounded subscriber queues drop for slow
      readers (documented trade-off: reconnect + replay recovers).
    - per-job replay buffer (``snapshot``) serves late subscribers;
    - finished jobs' buffers are pruned after :data:`BUFFER_TTL_S`.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._buffers: dict[str, deque[Event]] = {}
        self._terminal_at: dict[str, float] = {}
        self._seq = 0

    def subscribe(self) -> asyncio.Queue[Event]:
        """A live event queue (bounded; see :meth:`publish`)."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """Drop *queue* from the fan-out (idempotent; called in ``finally``)."""
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def publish(self, event: str, data: dict[str, Any]) -> None:
        """Fan out one event to every subscriber + the job's replay buffer."""
        self._prune_buffers()
        self._seq += 1
        evt = Event(event, data, self._seq)
        job_id = data.get("job_id")
        if isinstance(job_id, str) and job_id:
            self._buffers.setdefault(job_id, deque(maxlen=BUFFER_MAX)).append(evt)
            if event in TERMINAL_EVENTS:
                self._terminal_at[job_id] = time.monotonic()
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # slow subscriber drops (documented; reconnect + replay)

    def snapshot(self, job_id: str) -> list[Event]:
        """Replay window for *job_id* (bounded — oldest events may have fallen off)."""
        return list(self._buffers.get(job_id, ()))

    def snapshot_project(self, project_id: str) -> list[Event]:
        """All buffered events for jobs in *project_id*, ordered by sequence.

        A project-scoped feed has no single ``job_id`` to snapshot, so gather
        the per-job buffers and filter by the ``project_id`` payload field.
        Serves late subscribers of the project feed (same guarantee as the
        job feed: connect after the job started and still receive its events).
        """
        events = [
            evt
            for buf in self._buffers.values()
            for evt in buf
            if evt.data.get("project_id") == project_id
        ]
        events.sort(key=lambda e: e.seq)
        return events

    def has_buffer(self, job_id: str) -> bool:
        """True while *job_id* still has a replay buffer (see :func:`sse_stream`)."""
        return job_id in self._buffers

    def _prune_buffers(self) -> None:
        """Drop replay buffers of jobs that went terminal long ago (TTL)."""
        now = time.monotonic()
        expired = [jid for jid, ts in self._terminal_at.items() if now - ts > BUFFER_TTL_S]
        for job_id in expired:
            self._buffers.pop(job_id, None)
            self._terminal_at.pop(job_id, None)


class StubAgent:
    """S0.9 stand-in agent: simulates the §4 six-stage pipeline without a model.

    Emits exactly the S0.7 mock sequence (so ``apps/web`` works against the
    real API unchanged) with a configurable per-tick delay. S1.x replaces this
    with a ``qa_copilot_ai``-backed agent on the same :class:`JobAgent` protocol.
    """

    stages: tuple[str, ...] = (
        "requirement",
        "test_design",
        "automation",
        "execution",
        "failure_analysis",
        "fix",
    )
    ticks_per_stage = 4

    def __init__(self, tick_delay: float = 0.25) -> None:
        self._tick_delay = max(0.0, tick_delay)

    async def run(self, ctx: JobContext) -> str:
        for stage in self.stages:
            await ctx.emit("stage.started", {"stage": stage})
            for tick in range(1, self.ticks_per_stage + 1):
                await asyncio.sleep(self._tick_delay)
                await ctx.emit("progress", {"stage": stage, "value": tick / self.ticks_per_stage})
            await ctx.emit("stage.completed", {"stage": stage})
        await asyncio.sleep(self._tick_delay)
        return f"stub-output/{ctx.job_type.value}"


class RequirementJobAgent:
    """S1.1 requirement agent: real LLM-backed analysis on the :class:`JobAgent` protocol.

    Replaces :class:`StubAgent` for ``requirement_analysis`` jobs. Runs the
    pure :class:`qa_copilot_ai.agents.RequirementAgent` (prompt registry +
    gateway, §31.6/§31.1), records the ``ai_actions`` audit row against the
    job's ``ai_sessions`` anchor (§31.5), and returns the analysis JSON as the
    ``output_ref``.
    """

    stages: tuple[str, ...] = ("requirement",)

    def __init__(self, agent: RequirementAgent, engine: Engine) -> None:
        self._agent = agent
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        """Analyze the requirement; return the analysis JSON as ``output_ref``."""
        input_data = ctx.input
        requirement = RequirementInput(
            title=input_data.get("title", ""),
            content=input_data.get("content", ""),
            acceptance_criteria=tuple(input_data.get("acceptance_criteria", [])),
        )
        await ctx.emit("stage.started", {"stage": "requirement"})
        await ctx.emit("progress", {"stage": "requirement", "value": 0.5})
        result = await self._agent.run(requirement)
        analysis_json = json.dumps(result.analysis.model_dump(), separators=(",", ":"))
        await ctx.emit("progress", {"stage": "requirement", "value": 1.0})
        await ctx.emit("stage.completed", {"stage": "requirement"})
        self._record_action(ctx, result, analysis_json)
        return analysis_json[:1024]

    def _record_action(
        self, ctx: JobContext, result: RequirementAgentResult, analysis_json: str
    ) -> None:
        """Record the ``ai_actions`` audit row against the job's session anchor."""
        if ctx.ai_session_id is None:
            return
        audit = result.call.audit_dict()
        with repo_db.session_scope(self._engine) as session:
            session.add(
                models.AIAction(
                    session_id=ctx.ai_session_id,
                    agent=str(audit["agent"]),
                    model=str(audit["model"]),
                    tokens_in=cast(int, audit["tokens_in"]),
                    tokens_out=cast(int, audit["tokens_out"]),
                    latency_ms=cast(int, audit["latency_ms"]),
                    input_hash=audit.get("input_hash"),
                    output_ref=analysis_json[:1024],
                )
            )


class TestDesignJobAgent:
    """S1.2 test design agent: real LLM-backed design on the :class:`JobAgent` protocol.

    Replaces :class:`StubAgent` for ``test_case_generation`` jobs. Runs the
    pure :class:`qa_copilot_ai.agents.TestDesignAgent` (prompt registry +
    gateway, §31.6/§31.1), records the ``ai_actions`` audit row against the
    job's ``ai_sessions`` anchor (§31.5), and **persists the suite** (S1.3:
    requirement + test-case rows + the §10 M:N join). The job's ``output_ref``
    is the persisted requirement id; the full suite JSON is the audit ref.
    """

    stages: tuple[str, ...] = ("test_design",)

    def __init__(self, agent: TestDesignAgent, engine: Engine) -> None:
        self._agent = agent
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        """Design the test suite, persist it (§10 rows), return the requirement id."""
        input_data = ctx.input
        requirement = TestDesignInput(
            title=input_data.get("title", ""),
            content=input_data.get("content", ""),
            acceptance_criteria=tuple(input_data.get("acceptance_criteria", [])),
        )
        await ctx.emit("stage.started", {"stage": "test_design"})
        await ctx.emit("progress", {"stage": "test_design", "value": 0.5})
        result = await self._agent.run(requirement)
        suite_json = json.dumps(result.suite.model_dump(), separators=(",", ":"))
        # S1.3: persist the suite — requirement + test-case rows + the §10
        # M:N join. ``output_ref`` becomes the persisted requirement id; the
        # §10 rows are the result the UI reads back.
        with repo_db.session_scope(self._engine) as session:
            persisted = repo_requirements.persist_requirement_with_suite(
                session,
                project_id=ctx.project_id or "",
                title=requirement.title,
                content=requirement.content,
                acceptance_criteria=list(requirement.acceptance_criteria),
                suite=result.suite,
            )
        await ctx.emit("progress", {"stage": "test_design", "value": 1.0})
        await ctx.emit("stage.completed", {"stage": "test_design"})
        self._record_action(ctx, result, suite_json)
        return persisted.requirement_id

    def _record_action(
        self, ctx: JobContext, result: TestDesignAgentResult, suite_json: str
    ) -> None:
        """Record the ``ai_actions`` audit row against the job's session anchor."""
        if ctx.ai_session_id is None:
            return
        audit = result.call.audit_dict()
        with repo_db.session_scope(self._engine) as session:
            session.add(
                models.AIAction(
                    session_id=ctx.ai_session_id,
                    agent=str(audit["agent"]),
                    model=str(audit["model"]),
                    tokens_in=cast(int, audit["tokens_in"]),
                    tokens_out=cast(int, audit["tokens_out"]),
                    latency_ms=cast(int, audit["latency_ms"]),
                    input_hash=audit.get("input_hash"),
                    output_ref=suite_json[:1024],
                )
            )


class AutomationRunner(Protocol):
    """What :class:`AutomationJobAgent` needs from the automation layer.

    Satisfied by the real :class:`qa_copilot_ai.agents.AutomationAgent`
    (S2.3, prompt + gateway) and by :class:`AutomationStub` (deterministic,
    no model) — same ``run(AutomationInput) → AutomationAgentResult``
    surface, so the job/persistence/review flow is identical either way.
    """

    async def run(self, input_data: AutomationInput) -> AutomationAgentResult: ...


class AutomationStub:
    """Deterministic stand-in for the S2.3 Automation Agent (no live model).

    Produces a schema-valid :class:`GeneratedTest` from the approved test
    case so the full S2.4 flow — 202 job → ``generated_tests`` row → diff
    review → approve/apply/reject — is verifiable without a model (the same
    role :class:`StubAgent` plays for the S0.9 pipeline). Output quality is
    *not* the point: the S2.3 agent (and its §21 gate) is tested separately
    (``tests/unit/test_automation_agent.py``).
    """

    async def run(self, input_data: AutomationInput) -> AutomationAgentResult:
        case = input_data.test_case
        slug = re.sub(r"[^a-z0-9]+", "-", case.title.lower()).strip("-") or "generated"
        content = _stub_test_content(case.title, case.steps, case.expected_results)
        generated = GeneratedTest(
            file_path=f"tests/{slug}.spec.ts",
            language="typescript",
            framework="playwright",
            content=content,
            notes=["S2.4 stub output — deterministic stand-in, not a live model run"],
        )
        call = AICallResult(
            agent="test-automator",
            model="stub",
            text=content,
            usage=TokenUsage(tokens_in=10, tokens_out=20),
            latency_ms=0,
            redactions=0,
            retries=0,
            input_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        )
        return AutomationAgentResult(test=generated, prompt_ref="test-automator@1", call=call)


def _stub_test_content(title: str, steps: list[str], expected: list[str]) -> str:
    """A syntactically plausible Playwright test echoing the approved case."""
    lines = [
        'import { test, expect } from "@playwright/test";',
        "",
        f'test("{title}", async ({{ page }}) => {{',
        "  // Approved test case (S2.4 stub — steps/expectations as review context):",
        "  // steps:",
    ]
    lines += [f"  //   - {step}" for step in steps]
    lines += ["  // expected:"]
    lines += [f"  //   - {result}" for result in expected]
    lines.append("  await expect(page).toHaveTitle(/.*/);")
    lines.append("});")
    lines.append("")
    return "\n".join(lines)


class AutomationJobAgent:
    """S2.4 automation job: S2.3 agent output → reviewable ``generated_tests`` row.

    For ``automation_generation`` jobs (build bible §19 S2.4, §11):

    1. loads the approved test case (``test_case_id`` in the job input),
    2. scans the target repository (S2.1) + extracts conventions (S2.2) —
       the S2.3 agent's shared contract inputs,
    3. runs the pure :class:`AutomationRunner` (real S2.3 agent or stub),
    4. persists the validated output as a **pending** ``generated_tests``
       row (human review is mandatory before anything ships, §19 S2.4),
    5. records the ``ai_actions`` audit row against the job's
       ``ai_sessions`` anchor (§31.1/§31.5) with ``output_ref`` pointing at
       the new review row,
    6. returns the row id as the job's ``output_ref`` (the S1.3 pattern).
    """

    stages: tuple[str, ...] = ("automation",)

    def __init__(self, runner: AutomationRunner, engine: Engine) -> None:
        self._runner = runner
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        """Generate + persist one generated test; return the row id."""
        input_data = ctx.input
        test_case_id = str(input_data.get("test_case_id") or "")
        repository_path = str(input_data.get("repository_path") or "")

        await ctx.emit("stage.started", {"stage": "automation"})
        await ctx.emit("progress", {"stage": "automation", "value": 0.1})

        with repo_db.session_scope(self._engine) as session:
            row = session.get(models.TestCase, test_case_id) if test_case_id else None
            if row is None:
                raise ValueError(f"test case {test_case_id!r} not found")
            test_case = DomainTestCase(
                id=row.id,
                title=row.title,
                type=row.type,
                priority=row.priority,
                preconditions=list(row.preconditions),
                steps=list(row.steps),
                expected_results=list(row.expected_results),
                risk=row.risk,
            )

        if not repository_path:
            raise ValueError("job input is missing 'repository_path'")
        root = Path(repository_path)
        profile = await asyncio.to_thread(scan_repository, root)
        conventions = await asyncio.to_thread(extract_conventions, root, profile)
        await ctx.emit("progress", {"stage": "automation", "value": 0.5})

        result = await self._runner.run(
            AutomationInput(
                test_case=test_case,
                repository_profile=profile,
                conventions=conventions,
            )
        )

        file_path_pattern = (
            conventions.test_file_patterns[0] if conventions.test_file_patterns else None
        )
        with repo_db.session_scope(self._engine) as session:
            gt = repo_generated_tests.persist_generated_test(
                session,
                project_id=ctx.project_id or "",
                job_id=ctx.job_id,
                test_case_id=test_case_id,
                file_path=result.test.file_path,
                file_path_pattern=file_path_pattern,
                language=result.test.language,
                framework=result.test.framework,
                content=result.test.content,
                notes=list(result.test.notes),
                repository_path=repository_path or None,
            )
            generated_test_id = gt.id

        await ctx.emit("progress", {"stage": "automation", "value": 1.0})
        await ctx.emit("stage.completed", {"stage": "automation"})
        self._record_action(ctx, result, generated_test_id)
        return generated_test_id

    def _record_action(
        self, ctx: JobContext, result: AutomationAgentResult, generated_test_id: str
    ) -> None:
        """Record the ``ai_actions`` audit row against the job's session anchor."""
        if ctx.ai_session_id is None:
            return
        audit = result.call.audit_dict()
        with repo_db.session_scope(self._engine) as session:
            session.add(
                models.AIAction(
                    session_id=ctx.ai_session_id,
                    agent=str(audit["agent"]),
                    model=str(audit["model"]),
                    tokens_in=cast(int, audit["tokens_in"]),
                    tokens_out=cast(int, audit["tokens_out"]),
                    latency_ms=cast(int, audit["latency_ms"]),
                    input_hash=audit.get("input_hash"),
                    # S2.4: the durable result is the review row — approve /
                    # apply / reject happen against it (§19 S2.4).
                    output_ref=generated_test_id,
                )
            )


class KnowledgeIndexJobAgent:
    """S5.3 project-scoped knowledge indexing (build bible §7, §14, §19 Phase 5).

    Assembles the project's knowledge corpus — repository files when
    ``repository_path`` is supplied, plus the project's persisted requirements,
    designed test cases, and run history — then persists it to the
    ``knowledge_documents`` table (idempotent delete+insert, stable ids).
    Deterministic: no LLM call (the local model is completion-only, §19 S5.0).
    Progress is reported over SSE; the job's ``output_ref`` is a stable
    ``knowledge://<project>`` reference.
    """

    stages: tuple[str, ...] = ("knowledge_index",)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        """Build + persist the project corpus, report progress, return a ref."""
        project_id = ctx.project_id or ""
        repository_path = ctx.input.get("repository_path")
        await ctx.emit("stage.started", {"stage": "knowledge_index"})
        await ctx.emit("progress", {"stage": "knowledge_index", "value": 0.25})
        with repo_db.session_scope(self._engine) as session:
            documents, _capped = build_project_knowledge(session, project_id, repository_path)
            count = persist_project_knowledge(session, project_id, documents)
        await ctx.emit("progress", {"stage": "knowledge_index", "value": 0.9})
        await ctx.emit("stage.completed", {"stage": "knowledge_index", "documents": count})
        return f"knowledge://{project_id}"


class KnowledgeQARunner(Protocol):
    """What :class:`KnowledgeAskJobAgent` needs from the S5.4 grounding agent.

    The real :class:`qa_copilot_ai.agents.KnowledgeQAAgent` (prompt registry +
    gateway) satisfies this; so does :class:`KnowledgeQARefusalStub` when no LLM
    is configured. Keeps the job layer decoupled from a live model — the same
    seam :class:`AutomationJobAgent` uses for its :class:`AutomationStub`.
    """

    async def run(self, qa_input: KnowledgeQAInput) -> KnowledgeQAAgentResult:
        """Ground *qa_input* into a contract-valid ``QAAnswer`` + audit call."""


class KnowledgeQARefusalStub(KnowledgeQARunner):
    """No-LLM stand-in for the S5.4 agent: a deterministic, contract-valid refusal.

    When no local model is configured (dev/demo), Ask still honours its SSE
    contract — it emits a ``knowledge.answer`` refusal (out of scope) instead of
    failing or going silent. Mirrors the S2.3 ``AutomationStub`` pattern.
    """

    async def run(self, qa_input: KnowledgeQAInput) -> KnowledgeQAAgentResult:
        """A contract-valid refusal: ``in_scope=False``, no answer, no citations."""
        answer = QAAnswer(in_scope=False, answer=None, citations=[], confidence=0.0)
        call = AICallResult(
            agent="knowledge-qa",
            model="none",
            text="",
            usage=TokenUsage(tokens_in=0, tokens_out=0, source="estimated"),
            latency_ms=0,
            redactions=0,
            retries=0,
            input_hash="0" * 64,
        )
        return KnowledgeQAAgentResult(answer=answer, call=call, prompt_ref="knowledge-qa@stub")


class KnowledgeAskJobAgent:
    """S5.5 project-knowledge Ask (build bible §7, §14, §19 Phase 5).

    Answers a project question grounded **only** in the project's knowledge
    base. Flow (S5.3 → S5.4 → S5.5):

    1. retrieve the project's top-k chunks (S5.3
       ``search_project_knowledge``);
    2. hand them to the S5.4 runner (:class:`KnowledgeQARunner`) to produce a
       contract-valid :class:`QAAnswer` (in-scope answer + citations, or a
       refusal);
    3. emit the answer over the ``knowledge.answer`` job event — the full text
       and citations ride the SSE payload (``jobs.output_ref`` is a 1024-char
       column and is only a stable ``knowledge-ask://`` reference);
    4. record the ``ai_actions`` audit row against the job's ``ai_sessions``
       anchor (§31.5).

    Progress is reported over SSE; the job's ``output_ref`` is a stable
    ``knowledge-ask://<project>`` reference.
    """

    stages: tuple[str, ...] = ("knowledge_ask",)

    def __init__(self, agent: KnowledgeQARunner, engine: Engine) -> None:
        self._agent = agent
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        """Retrieve + ground the answer, emit ``knowledge.answer``, return a ref."""
        project_id = ctx.project_id or ""
        question = str(ctx.input.get("question") or "")
        await ctx.emit("stage.started", {"stage": "knowledge_ask"})
        await ctx.emit("progress", {"stage": "knowledge_ask", "value": 0.2})

        # 1. S5.3 retrieval (project-scoped, top-k ≤ 5, §14).
        with repo_db.session_scope(self._engine) as session:
            search = search_project_knowledge(session, project_id, question, top_k=5)
        hits = list(search.hits)
        context = tuple(
            KnowledgeContext(
                source_ref=hit.chunk.document_ref,
                title=hit.chunk.title,
                content=hit.chunk.content,
            )
            for hit in hits
        )
        # citation enrichment: document_ref → (source_type, score)
        cite_meta: dict[str, tuple[str, float]] = {
            hit.chunk.document_ref: (hit.chunk.source_type.value, hit.score) for hit in hits
        }

        await ctx.emit("progress", {"stage": "knowledge_ask", "value": 0.5})

        # 2. S5.4 grounded answer (contract-valid QAAnswer).
        qa_result = await self._agent.run(KnowledgeQAInput(question=question, context=context))
        answer = qa_result.answer

        # 3. Map the QAAnswer to the ``knowledge.answer`` event body and emit it.
        payload = self._answer_payload(answer, cite_meta)
        await ctx.emit("knowledge.answer", payload)
        await ctx.emit("progress", {"stage": "knowledge_ask", "value": 0.95})
        await ctx.emit(
            "stage.completed",
            {
                "stage": "knowledge_ask",
                "in_scope": answer.in_scope,
                "citations": len(payload["citations"]),
            },
        )

        # 4. Audit (ai_actions) against the job's session anchor (§31.5).
        self._record_action(ctx, qa_result, json.dumps(payload, separators=(",", ":")))

        return f"knowledge-ask://{project_id}"

    @staticmethod
    def _answer_payload(
        answer: QAAnswer, cite_meta: dict[str, tuple[str, float]]
    ) -> dict[str, Any]:
        """Map the S5.4 ``QAAnswer`` to the ``knowledge.answer`` event body."""
        citations = [
            {
                "document_ref": cite.source_ref,
                "source_type": cite_meta.get(cite.source_ref, ("", 0.0))[0],
                "title": cite.title,
                "score": cite_meta.get(cite.source_ref, ("", 0.0))[1],
            }
            for cite in answer.citations
        ]
        return {
            "in_scope": answer.in_scope,
            "answer": answer.answer or "",
            "citations": citations,
            "confidence": answer.confidence,
        }

    def _record_action(
        self, ctx: JobContext, result: KnowledgeQAAgentResult, answer_json: str
    ) -> None:
        """Record the ``ai_actions`` audit row against the job's session anchor."""
        if ctx.ai_session_id is None:
            return
        audit = result.call.audit_dict()
        with repo_db.session_scope(self._engine) as session:
            session.add(
                models.AIAction(
                    session_id=ctx.ai_session_id,
                    agent=str(audit["agent"]),
                    model=str(audit["model"]),
                    tokens_in=cast(int, audit["tokens_in"]),
                    tokens_out=cast(int, audit["tokens_out"]),
                    latency_ms=cast(int, audit["latency_ms"]),
                    input_hash=audit.get("input_hash"),
                    output_ref=answer_json[:1024],
                )
            )


# ---------------------------------------------------------------------------
# S6.4: regression / impact / history / advice (build bible §19 S6.4)
# ---------------------------------------------------------------------------

# Ordinal weights for "take the strongest" context (deterministic).
_RISK_ORDER: dict[RiskLevel, int] = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
_PRIORITY_ORDER: dict[Priority, int] = {Priority.LOW: 0, Priority.MEDIUM: 1, Priority.HIGH: 2}


class RegressionJobAgent:
    """S6.4: the regression/impact/history/advice job (build bible §19 S6.4).

    Orchestrates the deterministic cores rather than re-implementing them:

    - S6.1 — the change-impact set (:func:`impact_from_session`, LLM-free);
    - S6.2 — the flaky/risk ranking keyed by each impacted test's file path
      (from the project's §10 execution history + requirement risk / test-case
      priority context);
    - S6.3 — the deterministic top-N recommendation (:func:`recommend`);
    - S6.5 — the optional advisor brief (:class:`RegressionAdvisorAgent`),
      degrading safely to the stub if the model is unavailable, so a flaky
      model can never change *which* tests are re-run.

    Emits a single ``regression.set`` event carrying the serialized
    recommendation set (plus the impact / ranking / advice context for the
    S6.4 UI) and returns a stable ``regression://<project>`` output ref.

    The stable ref is also recorded as the S6.4 ``ai_actions`` audit row on
    the job's ``ai_sessions`` anchor (§19 S6.4: "``output_ref`` = stable
    ``regression://<project>`` ref → ``ai_actions`` audit row") — one row
    per job, carrying the advisor's model-call stats (§31.1) when the LLM
    ran, or a deterministic ``model="stub"`` marker when it degraded (the
    audit covers the job's AI activity, not the deterministic ranking).
    """

    stages: tuple[str, ...] = ("regression",)

    def __init__(self, store: PromptStore, gateway: LLMGateway | None, engine: Engine) -> None:
        self._store = store
        self._gateway = gateway  # LLMGateway | None (None → advisor stub)
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        project_id = ctx.project_id or ""
        repository_path = str(ctx.input.get("repository_path") or "")
        changed_files = [str(p) for p in (ctx.input.get("files") or [])]
        base_ref = ctx.input.get("base_ref")
        head_ref = ctx.input.get("head_ref")
        top_n = int(ctx.input.get("top_n") or 10)

        await ctx.emit("stage.started", {"stage": "regression"})
        await ctx.emit("progress", {"stage": "regression", "value": 0.1})

        # Resolve the changed-file set: an explicit diff, or a BASE..HEAD range.
        if not changed_files and base_ref and head_ref:
            changed_files = changed_files_from_range(repository_path, base_ref, head_ref)

        with repo_db.session_scope(self._engine) as session:
            # S6.1: deterministic change-impact set.
            impact = impact_from_session(session, project_id, repository_path, changed_files)
            await ctx.emit("progress", {"stage": "regression", "value": 0.4})
            # S6.2: the flaky/risk ranking for the impacted tests.
            ranking = self._ranking_for_impact(session, project_id, impact)
            await ctx.emit("progress", {"stage": "regression", "value": 0.65})
            # S6.3: deterministic top-N recommendation.
            recommendation = recommend(impact, ranking, top_n=top_n)

        # S6.5 (optional): the advisor brief (degrades safely to the stub).
        await ctx.emit("progress", {"stage": "regression", "value": 0.8})
        advice, advisor_result = await self._advise(recommendation)

        await ctx.emit(
            "regression.set",
            {
                "recommendation": recommendation.model_dump(mode="json"),
                "impact": impact.model_dump(mode="json"),
                "ranking": ranking.model_dump(mode="json"),
                "advice": advice,
            },
        )
        await ctx.emit(
            "stage.completed",
            {"stage": "regression", "recommendations": len(recommendation.recommendations)},
        )

        # S6.4 exit criterion (§19 S6.4, §31.5): the stable output_ref →
        # the ``ai_actions`` audit row on the job's ``ai_sessions`` anchor.
        self._record_action(ctx, advisor_result)
        return f"regression://{project_id}"

    async def _advise(
        self, recommendation: RecommendationSet
    ) -> tuple[dict[str, Any], RegressionAdvisorResult | None]:
        """The S6.5 advisor brief; always succeeds (safe stub fallback).

        Returns the event payload plus the raw advisor result (``None`` when
        no gateway is configured or the call degraded) so
        :meth:`_record_action` can audit the model call.
        """
        if self._gateway is None:
            return (
                {"summary": stub_summary(recommendation), "source": SUMMARY_SOURCE_STUB},
                None,
            )
        try:
            advisor = RegressionAdvisorAgent(self._store, self._gateway)
            result = await advisor.run(AdvisorInput(set=recommendation))
            return {"summary": result.summary, "source": result.source}, result
        except Exception:  # noqa: BLE001 — the advisor is optional; never fail the job
            logger.warning("S6.4 advisor failed; using the stub summary", exc_info=True)
            return (
                {"summary": stub_summary(recommendation), "source": SUMMARY_SOURCE_STUB},
                None,
            )

    def _record_action(
        self, ctx: JobContext, advisor_result: RegressionAdvisorResult | None
    ) -> None:
        """Record the S6.4 ``ai_actions`` audit row against the job's anchor.

        §19 S6.4: "``output_ref`` = stable ``regression://<project>`` ref →
        ``ai_actions`` audit row". When the LLM ran, the row carries the
        model-call stats (§31.1); on the deterministic stub path a
        ``model="stub"`` marker keeps the job's AI activity auditable.
        """
        if ctx.ai_session_id is None:
            return
        output_ref = f"regression://{ctx.project_id or ''}"
        if advisor_result is not None and advisor_result.call is not None:
            audit = advisor_result.call.audit_dict()
            action = models.AIAction(
                session_id=ctx.ai_session_id,
                agent=str(audit["agent"]),
                model=str(audit["model"]),
                tokens_in=cast(int, audit["tokens_in"]),
                tokens_out=cast(int, audit["tokens_out"]),
                latency_ms=cast(int, audit["latency_ms"]),
                input_hash=audit.get("input_hash"),
                output_ref=output_ref,
            )
        else:
            action = models.AIAction(
                session_id=ctx.ai_session_id,
                agent=ADVISOR_NAME,
                model="stub",
                output_ref=output_ref,
            )
        with repo_db.session_scope(self._engine) as session:
            session.add(action)

    @staticmethod
    def _ranking_for_impact(session: Session, project_id: str, impact: ImpactSet) -> RiskRanking:
        """Map the S6.1 impact set to an S6.2 ranking keyed by test file path.

        Each impacted test file is mapped to its §10-linked execution history
        (``project_test_history``) and its §10 context (requirement risk,
        test-case priority) so the S6.3 ``recommend`` join — on
        ``ImpactedTest.path`` — lines up. LLM-free and deterministic.
        """
        history = project_test_history(session, project_id)
        requirement_ids = sorted({rid for imp in impact.impacted for rid in imp.requirement_ids})
        test_case_ids = sorted({tcid for imp in impact.impacted for tcid in imp.test_case_ids})
        req_risk = (
            {
                r.id: r.risk
                for r in session.scalars(
                    select(models.Requirement).where(models.Requirement.id.in_(requirement_ids))
                ).all()
            }
            if requirement_ids
            else {}
        )
        tc_priority = (
            {
                t.id: t.priority
                for t in session.scalars(
                    select(models.TestCase).where(models.TestCase.id.in_(test_case_ids))
                ).all()
            }
            if test_case_ids
            else {}
        )

        inputs = []
        for imp in impact.impacted:
            outcomes = [o for tcid in imp.test_case_ids for o in history.get(tcid, ())]
            req_risks = [req_risk[rid] for rid in imp.requirement_ids if rid in req_risk]
            tc_prios = [tc_priority[tcid] for tcid in imp.test_case_ids if tcid in tc_priority]
            inputs.append(
                TestRiskInput(
                    test_key=imp.path,
                    outcomes=tuple(outcomes),
                    impact_kind=strongest_impact_kind(imp.kinds),
                    requirement_risk=max(req_risks, key=lambda r: _RISK_ORDER[r])
                    if req_risks
                    else None,
                    test_case_priority=max(tc_prios, key=lambda p: _PRIORITY_ORDER[p])
                    if tc_prios
                    else None,
                )
            )
        return build_risk_ranking(project_id, inputs)


class RunExecutionJobAgent:
    """S6.4 "Run this set" (build bible §19 S6.4 exit criteria).

    Runs the selected regression tests through the **existing S3 execution
    path** — the same Playwright worker the automation pipeline uses
    (``qa_copilot_execution.run_playwright``): spawn the target repo's
    Playwright suite filtered to the selected test files, capture the §15
    artifacts, and persist the run via
    :func:`qa_copilot_repository.persist_run` (run/results/artifacts rows +
    the S6.2 history feed).

    Progress rides the ``run_execution`` stage; the terminal ``run.result``
    event carries the persisted run id and Playwright totals, and the job's
    ``output_ref`` is the persisted run id (the S3.2 ``GET /runs/{id}`` read
    path serves its results and artifacts).
    """

    stages: tuple[str, ...] = ("run_execution",)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def run(self, ctx: JobContext) -> str:
        repository_path = str(ctx.input.get("repository_path") or "")
        tests = [str(t) for t in (ctx.input.get("tests") or [])]
        timeout_s = float(ctx.input.get("timeout_s") or 600.0)

        await ctx.emit("stage.started", {"stage": "run_execution"})
        await ctx.emit("progress", {"stage": "run_execution", "value": 0.1})

        # S3 execution path: the Playwright worker (subprocess) — keep it off
        # the event loop, then persist the report onto the §10 rows.
        config = PlaywrightConfig(
            target_dir=Path(repository_path),
            test_filter=self._filter_for_tests(tests),
            timeout_s=timeout_s,
        )
        run_id = str(uuid4())
        report = await asyncio.to_thread(run_playwright, config, run_id)

        with repo_db.session_scope(self._engine) as session:
            run = repo_runs.persist_run(session, project_id=ctx.project_id or "", report=report)
        totals = report.totals

        await ctx.emit("progress", {"stage": "run_execution", "value": 1.0})
        await ctx.emit(
            "run.result",
            {
                "run_id": run.id,
                "status": report.status.value,
                "totals": {
                    "total": totals.total,
                    "passed": totals.passed,
                    "failed": totals.failed,
                    "flaky": totals.flaky,
                    "skipped": totals.skipped,
                },
            },
        )
        await ctx.emit("stage.completed", {"stage": "run_execution", "run_id": run.id})
        return run.id

    @staticmethod
    def _filter_for_tests(tests: list[str]) -> str:
        """Playwright filter for the selected test files (repo-relative paths).

        A single file runs on its own; a set is an alternation of the exact
        file paths — Playwright matches them positionally against the target
        dir. An empty selection runs the whole suite (the schema forbids an
        empty body, so this is defensive).
        """
        if not tests:
            return ""
        if len(tests) == 1:
            return tests[0]
        return "|".join(tests)


def _payload(job_id: str, project_id: str | None, **fields: Any) -> dict[str, Any]:
    """Event payload: ``job_id`` always; ``project_id`` when known (filters)."""
    payload: dict[str, Any] = {"job_id": job_id, **fields}
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


class JobRunner:
    """Drives the job state machine (build bible §31.2).

    - one task per job; ``start`` is **idempotent** and safe from any thread
      (schedules onto the app's event loop when called off-loop, e.g. from
      tests or a future worker process)
    - ``pending → running → completed | failed``; ``jobs.progress`` updated on
      every ``progress`` event (overall = (finished stages + stage value) / stages)
    - one ``ai_sessions`` row per job (audit anchor for S1.x ``ai_actions``)
    - :meth:`reap_orphans` fails jobs left ``running`` by a crash (single
      process in Phase 0); :meth:`shutdown` cancels in-flight jobs
    """

    def __init__(self, engine: Engine, bus: EventBus) -> None:
        self._engine = engine
        self._bus = bus
        self._active: dict[str, asyncio.Future[None] | concurrent.futures.Future[None]] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the app's event loop (called from the lifespan, on the loop)."""
        self._loop = loop

    def start(
        self,
        job_id: str,
        *,
        agent: JobAgent,
        user_id: str | None = None,
        job_input: dict[str, Any] | None = None,
    ) -> bool:
        """Schedule *job_id* with *agent*; returns ``False`` when already running.

        A duplicate start while the job is in flight is a no-op (idempotent).
        Raises ``RuntimeError`` if no event loop is available (programming error).
        """
        job_input = job_input or {}
        with self._lock:
            existing = self._active.get(job_id)
            if existing is not None and not existing.done():
                return False
            self._active[job_id] = self._schedule(job_id, agent, user_id, job_input)
        return True

    async def shutdown(self) -> None:
        """Cancel in-flight jobs and wait for the ones on this loop to unwind."""
        loop = asyncio.get_running_loop()
        handles = list(self._active.values())
        for handle in handles:
            handle.cancel()
        awaitables = [h for h in handles if isinstance(h, asyncio.Task) and h.get_loop() is loop]
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)

    def reap_orphans(self) -> int:
        """Fail jobs left ``running`` by a previous crash (startup hook).

        Synchronous (DB only) — call via ``asyncio.to_thread`` from the
        lifespan so the loop never blocks on I/O.
        """
        with repo_db.session_scope(self._engine) as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(models.Job)
                    .where(models.Job.status == JobStatus.RUNNING)
                    .values(
                        status=JobStatus.FAILED,
                        error="server restarted while job was running",
                        completed_at=datetime.now(UTC),
                    )
                ),
            )
            return result.rowcount or 0

    def _schedule(
        self,
        job_id: str,
        agent: JobAgent,
        user_id: str | None,
        job_input: dict[str, Any],
    ) -> asyncio.Future[None] | concurrent.futures.Future[None]:
        """Create the job task on the right loop (see :meth:`start`)."""
        coro = self._run(job_id, agent, user_id, job_input)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if self._loop is None:
                coro.close()
                raise RuntimeError(
                    "JobRunner.start called with no event loop available "
                    "(start the app via a TestClient/uvicorn context so the "
                    "lifespan can bind the loop)"
                ) from None
            return asyncio.run_coroutine_threadsafe(coro, self._loop)
        return loop.create_task(coro, name=f"job-{job_id}")

    async def _run(
        self,
        job_id: str,
        agent: JobAgent,
        user_id: str | None,
        job_input: dict[str, Any],
    ) -> None:
        """One job's full lifecycle (runs as an asyncio task)."""
        started_at = time.monotonic()
        session = repo_db.make_session_factory(self._engine)()
        ai_session: models.AISession | None = None
        job: models.Job | None = None  # bound before the try: the except handler reports it
        try:
            job = session.get(models.Job, job_id)
            if job is None or job.status != JobStatus.PENDING:
                return  # vanished, or already driven (defensive; start() dedupes)

            # Audit anchor for the job's AI activity (§31.5; S1.x ai_actions
            # rows link through this session). ``ai_sessions.project_id`` is
            # NOT NULL, so jobs without a project skip the anchor.
            if job.project_id is not None:
                ai_session = models.AISession(
                    project_id=job.project_id,
                    user_id=user_id,
                    task_type=job.type.value,
                    status="active",
                )
                session.add(ai_session)
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(UTC)
            session.commit()
            self._bus.publish(
                "job.started", _payload(job_id, job.project_id, stages=list(agent.stages))
            )

            stages = list(agent.stages)
            stage_index = -1

            async def emit(event: str, data: dict[str, Any]) -> None:
                nonlocal stage_index
                if event == "stage.started":
                    stage_index += 1
                elif event == "progress" and stages:
                    try:
                        value = float(data.get("value", 0.0))
                    except (TypeError, ValueError):
                        value = 0.0
                    overall = (max(stage_index, 0) + min(max(value, 0.0), 1.0)) / len(stages)
                    job.progress = min(overall, 1.0)
                    session.commit()
                self._bus.publish(event, _payload(job_id, job.project_id, **data))

            ctx = JobContext(
                job_id=job_id,
                project_id=job.project_id,
                job_type=job.type,
                input=job_input,
                emit=emit,
                ai_session_id=ai_session.id if ai_session is not None else None,
            )
            output_ref = await agent.run(ctx)

            job.output_ref = output_ref
            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.completed_at = datetime.now(UTC)
            if ai_session is not None:
                ai_session.status = "completed"
            session.commit()
            self._bus.publish(
                "job.completed", _payload(job_id, job.project_id, output_ref=output_ref)
            )
            logger.info(
                "job completed",
                extra={
                    "job_id": job_id,
                    "job_type": job.type.value,
                    "latency_ms": int((time.monotonic() - started_at) * 1000),
                },
            )
        except Exception as exc:
            logger.exception("job failed", extra={"job_id": job_id})
            session.rollback()
            row = session.get(models.Job, job_id)
            if row is not None and row.status not in (JobStatus.COMPLETED, JobStatus.CANCELLED):
                row.status = JobStatus.FAILED
                row.error = str(exc)[:2000]
                row.completed_at = datetime.now(UTC)
                session.commit()
            if ai_session is not None:
                try:
                    ai_session.status = "failed"
                    session.commit()
                except Exception:
                    session.rollback()  # best-effort; the job row is the source of truth
            project_id = job.project_id if job is not None else None
            self._bus.publish("job.failed", _payload(job_id, project_id, error=str(exc)[:500]))
        finally:
            session.close()
            with self._lock:
                self._active.pop(job_id, None)


def format_sse(event: Event) -> str:
    """One SSE frame — identical shape to the S0.7 web mock (``event:`` + ``data:`` JSON)."""
    payload = json.dumps(event.data, separators=(",", ":"), default=str)
    return f"event: {event.event}\ndata: {payload}\n\n"


async def sse_stream(
    bus: EventBus,
    *,
    job_id: str | None = None,
    project_id: str | None = None,
    snapshot: JobSnapshot | None = None,
) -> AsyncGenerator[str, None]:
    """SSE frame generator for ``GET /api/v1/events`` (build bible §11).

    Delivery guarantees (all events for one job arrive **in order, exactly
    once**, whether the subscriber connects before or after they were
    published):

    1. subscribe → 2. snapshot the replay buffer → 3. replay it → 4. drain
       the live queue, skipping events already replayed (``seq`` dedup).

    Terminal handling: the stream closes on a terminal event
    (:data:`TERMINAL_EVENTS`). If the job already finished and its replay
    buffer was evicted (TTL), the terminal frame is synthesized from
    *snapshot* (row state read up front by the route).

    *project_id* scopes the stream to one project's jobs: it replays that
    project's buffered events (gathered across jobs) then keeps streaming —
    open-ended. *job_id* scopes the stream to a single job and makes it finite.

    Heartbeats (SSE comment frames) keep proxies from timing idle streams
    out; the stream self-terminates after :data:`STREAM_MAX_AGE_S`.
    """
    queue = bus.subscribe()
    last_seq = -1
    last_yielded: Event | None = None
    deadline = time.monotonic() + STREAM_MAX_AGE_S
    try:
        if job_id is not None:
            replay = bus.snapshot(job_id)
        elif project_id is not None:
            replay = bus.snapshot_project(project_id)
        else:
            replay = []
        for event in replay:
            if event.seq <= last_seq:
                continue
            if project_id is not None and event.data.get("project_id") != project_id:
                continue
            last_seq = event.seq
            last_yielded = event
            yield format_sse(event)
        if job_id is not None:
            if last_yielded is not None and last_yielded.event in TERMINAL_EVENTS:
                return  # replay already ended the job — nothing live to drain
            if (
                snapshot is not None
                and snapshot.status in TERMINAL_STATUSES
                and not bus.has_buffer(job_id)
            ):
                # Finished long ago, buffer evicted: close with the terminal
                # state so clients don't sit on a stream that can never end.
                # (Safe: buffer evicted ⇒ terminal event was published long
                # before subscribe ⇒ it cannot also be in this queue ⇒ no
                # duplicate delivery.)
                data = _payload(job_id, snapshot.project_id)
                if snapshot.status == JobStatus.COMPLETED:
                    data["output_ref"] = snapshot.output_ref
                    yield format_sse(Event("job.completed", data, seq=last_seq + 1))
                else:
                    data["error"] = snapshot.error or "job failed"
                    yield format_sse(Event("job.failed", data, seq=last_seq + 1))
                return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    return
                yield ": keepalive\n\n"
                continue
            if event.seq <= last_seq:
                continue  # already delivered via replay
            if project_id is not None and event.data.get("project_id") != project_id:
                continue
            last_seq = event.seq
            yield format_sse(event)
            if job_id is not None and event.event in TERMINAL_EVENTS:
                return
    finally:
        bus.unsubscribe(queue)
