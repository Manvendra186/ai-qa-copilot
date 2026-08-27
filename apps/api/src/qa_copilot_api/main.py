"""AI QA Copilot API — FastAPI application entry point (S0.3 skeleton).

Run locally:
    uv run uvicorn qa_copilot_api.main:app --port 8000
then:
    curl http://127.0.0.1:8000/health
    curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
         -H 'Content-Type: application/json' \
         -d '{"email": "dev@local.dev", "password": "dev-password"}'
"""

from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI

from qa_copilot_api import routes
from qa_copilot_api.config import Settings, get_settings
from qa_copilot_api.db import make_app_engine
from qa_copilot_api.logging_config import configure_logging
from qa_copilot_api.schemas import HealthResponse

try:
    _VERSION = version("qa-copilot-api")
except PackageNotFoundError:  # running outside an installed workspace
    _VERSION = "0.1.0"


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
    )
    app.state.settings = settings
    app.state.engine = make_app_engine(settings.database_url)

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

    return app


app = create_app()
