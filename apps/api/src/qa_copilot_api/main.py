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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from qa_copilot_ai import (
    AutomationAgent,
    FilePromptStore,
    KnowledgeQAAgent,
    LLMGateway,
    RequirementAgent,
    TestDesignAgent,
)
from qa_copilot_domain.enums import AuditAction, AuditOutcome
from qa_copilot_repository import billing as repo_billing
from qa_copilot_repository import db as repo_db
from qa_copilot_repository import security_audit as repo_security_audit
from sqlalchemy import Engine
from starlette.responses import JSONResponse

from qa_copilot_api import jobs, routes, security, throttle
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
        limiter = getattr(app.state, "rate_limiter", None)
        if limiter is not None:  # S8.5: release the Redis pool on shutdown
            limiter.close()


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


def _build_automation_jobs_agent(settings: Settings, engine: Engine) -> jobs.JobAgent:
    """Build the S2.4 automation job agent: S2.3 runner behind a review row.

    S2.4 (§19): the :class:`AutomationJobAgent` wraps the automation runner —
    the real S2.3 :class:`AutomationAgent` (``test-automator`` prompt + gateway,
    §31.6/§31.1) when an LLM is configured, otherwise the deterministic
    :class:`AutomationStub` (no model) — and persists the output as a
    **pending** ``generated_tests`` review row for the approve/apply/reject
    endpoints.
    """
    if not settings.llm_base_url or not settings.llm_model:
        logger.info("LLM not configured; using AutomationStub for automation_generation")
        runner: jobs.AutomationRunner = jobs.AutomationStub()
    else:
        prompts_dir = (
            Path(__file__).parent.parent.parent.parent.parent / "packages" / "ai" / "prompts"
        )
        store = FilePromptStore(prompts_dir)
        gateway = LLMGateway(base_url=settings.llm_base_url, model=settings.llm_model)
        runner = AutomationAgent(store, gateway)
        logger.info("LLM configured (model=%s); using AutomationAgent", settings.llm_model)
    return jobs.AutomationJobAgent(runner, engine)


def _build_knowledge_ask_jobs_agent(settings: Settings, engine: Engine) -> jobs.JobAgent:
    """Build the S5.5 knowledge Ask job agent: grounded QA over the project base.

    S5.5: when ``llm_base_url`` and ``llm_model`` are set, wire the
    :class:`KnowledgeAskJobAgent` around the real S5.4
    :class:`KnowledgeQAAgent` (``knowledge-qa`` prompt registry + gateway,
    §31.6/§31.1). Otherwise fall back to the deterministic
    :class:`KnowledgeQARefusalStub` (no model) so the 202/SSE contract stays
    verifiable without a model — Ask still emits a well-formed refusal
    (``in_scope=False``) instead of failing or going silent.
    """
    if not settings.llm_base_url or not settings.llm_model:
        logger.info("LLM not configured; using KnowledgeQARefusalStub for knowledge_ask")
        runner: jobs.KnowledgeQARunner = jobs.KnowledgeQARefusalStub()
    else:
        prompts_dir = (
            Path(__file__).parent.parent.parent.parent.parent / "packages" / "ai" / "prompts"
        )
        store = FilePromptStore(prompts_dir)
        gateway = LLMGateway(base_url=settings.llm_base_url, model=settings.llm_model)
        runner = KnowledgeQAAgent(store, gateway)
        logger.info("LLM configured (model=%s); using KnowledgeAskJobAgent", settings.llm_model)
    return jobs.KnowledgeAskJobAgent(runner, engine)


def _build_regression_jobs_agent(settings: Settings, engine: Engine) -> jobs.JobAgent:
    """Build the S6.4 regression/impact/history/advice job agent (§19 S6.4).

    The deterministic S6.1/S6.2/S6.3 cores are always available (no model
    required). The optional S6.5 advisor brief is wired to the real
    :class:`RegressionAdvisorAgent` when ``llm_base_url`` and ``llm_model`` are
    set; otherwise the agent falls back to the stub summary (``gateway=None``)
    so a flaky/absent model can never change *which* tests are re-run.
    """
    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "packages" / "ai" / "prompts"
    store = FilePromptStore(prompts_dir)
    if not settings.llm_base_url or not settings.llm_model:
        logger.info("LLM not configured; S6.4 advisor uses the stub summary")
        gateway = None
    else:
        gateway = LLMGateway(base_url=settings.llm_base_url, model=settings.llm_model)
        logger.info("LLM configured (model=%s); S6.4 advisor is live", settings.llm_model)
    return jobs.RegressionJobAgent(store, gateway, engine)


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

    # S8.4 (build bible §19 S8.4): a job dispatch that hits the org's plan
    # quota is a 409 ``plan_limit`` — not a 5xx, not a created-then-failed
    # job. The gate (``jobs.JobRunner.start``) already removed the job row
    # and published the terminal ``job.rejected`` event; here we render the
    # structured body and record the S8.3 audit row (``org.quota.denied``)
    # with the actor + client IP. The audit write is best-effort — a
    # database failure must never mask the client-facing 409.
    @app.exception_handler(repo_billing.PlanLimitExceeded)
    async def _plan_limit_handler(
        request: Request, exc: repo_billing.PlanLimitExceeded
    ) -> JSONResponse:
        forwarded = request.headers.get("x-forwarded-for")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else "unknown")
        )
        try:
            with repo_db.session_scope(app.state.engine) as session:
                repo_security_audit.record(
                    session,
                    actor_id=exc.actor_id,
                    action=AuditAction.ORG_QUOTA_DENIED,
                    target=exc.org_id,
                    outcome=AuditOutcome.DENIED,
                    ip=ip,
                )
                session.commit()
        except Exception:  # noqa: BLE001 — the 409 contract outranks the audit row
            logger.exception("S8.4: failed to record org.quota.denied audit row")
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "plan_limit",
                    "plan": exc.plan,
                    "limit": exc.limit,
                    "used": exc.used,
                    "allowed": exc.allowed,
                    "org_id": exc.org_id,
                    "project_id": exc.project_id,
                    "job_id": exc.job_id,
                }
            },
        )

    # S8.1: Redis-backed login throttling (per email + IP). The client is
    # constructed from the configured URL but is lazy — no connection is
    # attempted until a request needs it — so the API boots without Redis
    # running (throttling fails open, see ``throttle.py``).
    app.state.login_throttler = throttle.LoginThrottler(
        settings.redis_url or "redis://localhost:6379/0",
        max_failures=settings.login_throttle_max_failures,
        window_s=settings.login_throttle_window_s,
    )

    # S8.5 (build bible §19): request rate limiting (per IP + per verified
    # user, Redis fixed-window, 429 + Retry-After). Same lazy/fail-open
    # posture as the login throttler above — the API boots without Redis.
    app.state.rate_limiter = security.RateLimiter(
        settings.redis_url or "redis://localhost:6379/0",
        max_requests=settings.rate_limit_max_requests,
        window_s=settings.rate_limit_window_s,
    )

    # S8.5 middleware stack. ``add_middleware`` inserts at the front, so the
    # *last* call is the outermost layer — the effective order is:
    #   RequestID -> CORS -> RateLimit -> SecurityHeaders -> routes
    # RequestID outermost: every downstream layer (and every log line via
    # ``RequestIDFilter``) sees the id. CORS before the limiter so preflights
    # never burn quota. SecurityHeaders innermost so the four headers ride on
    # every response the inner layers produce — including the 429 body, the
    # 401 from auth, and the 409 from the S8.4 quota gate.
    if settings.rate_limit_enabled:
        app.add_middleware(security.RateLimitMiddleware, api=app)
    cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    # Locked-down by default: no origins configured → no middleware → the
    # browser only ever sees same-origin responses (fail-closed CORS).
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    app.add_middleware(security.SecurityHeadersMiddleware)
    app.add_middleware(security.RequestIDMiddleware)

    # S0.9: job subsystem (in-process, Phase 0 — see ``qa_copilot_api.jobs``).
    # Created here (not only in the lifespan) so ``app.state`` is complete
    # for any request path; the lifespan only binds the loop, reaps orphans
    # and shuts in-flight jobs down.
    app.state.jobs_bus = jobs.EventBus()
    app.state.jobs_runner = jobs.JobRunner(app.state.engine, app.state.jobs_bus)
    app.state.jobs_agent = _build_jobs_agent(settings, app.state.engine)
    # S1.2: the Test Design Agent job (test_case_generation).
    app.state.jobs_test_design_agent = _build_test_design_jobs_agent(settings, app.state.engine)
    # S2.4: the Automation Agent job (automation_generation) → pending review row.
    app.state.jobs_automation_agent = _build_automation_jobs_agent(settings, app.state.engine)
    # S5.3: the Knowledge Index job (knowledge_index) — deterministic, no LLM.
    app.state.jobs_knowledge_agent = jobs.KnowledgeIndexJobAgent(app.state.engine)
    # S5.5: the Knowledge Ask job (knowledge_ask) — grounded QA over the base.
    app.state.jobs_knowledge_ask_agent = _build_knowledge_ask_jobs_agent(settings, app.state.engine)
    # S6.4: the regression/impact/history/advice job (regression_analysis).
    app.state.jobs_regression_agent = _build_regression_jobs_agent(settings, app.state.engine)
    # S6.4: "Run this set" (run_execution) — reuses the S3 execution path.
    app.state.jobs_run_execution_agent = jobs.RunExecutionJobAgent(app.state.engine)
    # S7.2: PR → regression set posted to the PR (regression_pr_comment) —
    # owner-or-above; deterministic, LLM-free end to end.
    app.state.jobs_regression_pr_comment_agent = jobs.RegressionPrCommentJobAgent(app.state.engine)
    # S7.4: file/link a failure as a Jira issue (jira_link) — owner-or-above;
    # deterministic, LLM-free (create-or-update idempotent, stale → recreate).
    app.state.jobs_jira_link_agent = jobs.JiraLinkJobAgent(app.state.engine)

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
    # S2.4: automation generation + generated-test review (§19 S2.4).
    app.include_router(routes.automation_router)
    app.include_router(routes.generated_tests_router)
    # S3.2: run history, results, artifacts (§10, §15).
    app.include_router(routes.runs_router)
    # S7.1: external integrations config (§19 S7.1; member+ read, owner+ write).
    app.include_router(routes.integrations_router)
    # S7.3: CI/CD webhook (§19 S7.3) — the HMAC signature is the auth;
    # pull_request opened/synchronize reuses the S6.4 regression_analysis
    # job (no new agent, no new JobType).
    app.include_router(routes.webhooks_router)
    # S8.2: teams — organizations, member management, code-based invites
    # (§19 S8.2; org baseline project access + one owner per org).
    app.include_router(routes.organizations_router)
    app.include_router(routes.invites_router)

    return app


app = create_app()
