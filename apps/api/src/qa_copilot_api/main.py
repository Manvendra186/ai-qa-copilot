"""AI QA Copilot API — FastAPI application entry point (S0.3 skeleton).

Run locally:
    uv run uvicorn qa_copilot_api.main:app --port 8000
then:
    curl http://127.0.0.1:8000/health
    curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
         -H 'Content-Type: application/json' \
         -d '{"email": "dev@local.dev", "password": "dev-password"}'

S0.9 adds the async jobs API (build bible §11, §31.2):
    curl -X POST http://127.0.0.1:8000/api/v1/requirements/analyze \
         -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
         -d '{"project_id": "...", "title": "Login flow", "content": "..."}'
    curl http://127.0.0.1:8000/api/v1/jobs/<job_id> -H "Authorization: Bearer $TOKEN"
    curl -N http://127.0.0.1:8000/api/v1/events?job_id=<job_id> \
         -H "Authorization: Bearer $TOKEN"
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI
from qa_copilot_ai import FilePromptStore, LLMGateway, RequirementAgent, TestDesignAgent
from sqlalchemy import Engine

from qa_copilot_api import jobs, routes
from qa_copilot_api.config import Settings, get_settings
from qa_copilot_api.db import make_app_engine
from qa_copilot_api.logging_config import configure_logging
from qa_copilot_api.schemas import HealthResponse

try:
    _VERSION = version("qa-copilot-api")
except PackageNotFoundError:  # running outside an installed workspace
    _VERSION = "0.1.0"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """S0.9 job lifecycle hooks (build bible §31.2).

    - bind the app's event loop so ``JobRunner.start`` works from the
      threadpool (sync endpoints) and from tests off-loop;
    - reap jobs left ``running`` by a previous crash (single process,
      Phase 0 — the reaper is a no-op while the process is up);
    - on shutdown, cancel in-flight jobs and let them unwind (they mark
      themselves failed; the next start reaps the rest).
    """
    runner: jobs.JobRunner = app.state.jobs_runner
    runner.bind_loop(asyncio.get_running_loop())
    reaped = await asyncio.to_thread(runner.reap_orphans)
    if reaped:
        logger.info("startup: marked %d orphaned running job(s) as failed", reaped)
    try:
        yield
    finally:
        await runner.shutdown()


def _build_jobs_agent(settings: Settings, engine: Engine) -> jobs.JobAgent:
    """Build the job agent: real LLM-backed when configured, stub otherwise.

    S1.1: when ``llm_base_url`` and ``llm_model`` are set, wire the
    :class:`RequirementJobAgent` (prompt registry + gateway, §31.6/§31.1).
    Otherwise fall back to the :class:`StubAgent` (S0.9 dev pacing).
    """
    if not settings.llm_base_url or not settings.llm_model:
        logger.info("LLM not configured; using StubAgent for requirement_analysis")
        return jobs.StubAgent(tick_delay=settings.job_tick_delay_s)
    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "packages" / "ai" / "prompts"
    store = FilePromptStore(prompts_dir)
    gateway = LLMGateway(base_url=settings.llm_base_url, model=settings.llm_model)
    agent = RequirementAgent(store, gateway)
    logger.info(
        "LLM configured (model=%s); using RequirementJobAgent",
        settings.llm_model,
    )
    return jobs.RequirementJobAgent(agent, engine)


def _build_test_design_jobs_agent(settings: Settings, engine: Engine) -> jobs.JobAgent:
    """Build the S1.2 test-design job agent: real LLM-backed when configured,
    stub otherwise.

    S1.2: when ``llm_base_url`` and ``llm_model`` are set, wire the
    :class:`TestDesignJobAgent` (``test-designer`` prompt registry + gateway,
    §31.6/§31.1). Otherwise fall back to the :class:`StubAgent` (S0.9 dev
    pacing) so the 202/SSE contract stays verifiable without a model.
    """
    if not settings.llm_base_url or not settings.llm_model:
        logger.info("LLM not configured; using StubAgent for test_case_generation")
        return jobs.StubAgent(tick_delay=settings.job_tick_delay_s)
    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "packages" / "ai" / "prompts"
    store = FilePromptStore(prompts_dir)
    gateway = LLMGateway(base_url=settings.llm_base_url, model=settings.llm_model)
    agent = TestDesignAgent(store, gateway)
    logger.info("LLM configured (model=%s); using TestDesignJobAgent", settings.llm_model)
    return jobs.TestDesignJobAgent(agent, engine)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory; settings injectable for tests and overrides."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="AI QA Copilot API",
        version=_VERSION,
        description=(
            "AI QA Copilot for Playwright-based QA teams: "
            "requirement → test design → automation → execution → "
            "failure analysis → fix. Local LLM via llama server; "
            "no cloud dependency (build bible §31.1)."
        ),
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.state.engine = make_app_engine(settings.database_url)

    # S0.9: job subsystem (in-process, Phase 0 — see ``qa_copilot_api.jobs``).
    # Created here (not only in the lifespan) so ``app.state`` is complete
    # for any request path; the lifespan only binds the loop, reaps orphans
    # and shuts in-flight jobs down.
    app.state.jobs_bus = jobs.EventBus()
    app.state.jobs_runner = jobs.JobRunner(app.state.engine, app.state.jobs_bus)
    app.state.jobs_agent = _build_jobs_agent(settings, app.state.engine)
    # S1.2: the Test Design Agent job (test_case_generation).
    app.state.jobs_test_design_agent = _build_test_design_jobs_agent(settings, app.state.engine)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Liveness: process up, configuration readable, no I/O."""
        return HealthResponse(
            service="qa-copilot-api",
            version=_VERSION,
            env=settings.env,
            timestamp=datetime.now(UTC),
        )

    # S0.8: auth baseline (§31.3) — login/me + role-gated project endpoints.
    app.include_router(routes.auth_router)
    app.include_router(routes.projects_router)
    # S0.9: async jobs API (§11) — 202 + job status + SSE events.
    app.include_router(routes.requirements_router)
    app.include_router(routes.jobs_router)
    app.include_router(routes.events_router)

    return app


app = create_app()
