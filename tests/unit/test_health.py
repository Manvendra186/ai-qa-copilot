"""S0.3 skeleton tests: ``/health`` liveness contract + settings injection.

Driven in-process via httpx's async ASGITransport (httpx 0.28 is
async-transport-only) — no port binding, no live server needed (the live
``curl`` check is part of acceptance and is run separately during step
verification).
"""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI
from qa_copilot_api.config import Settings
from qa_copilot_api.main import app, create_app


def get(target: FastAPI, path: str) -> httpx.Response:
    """In-process GET against the ASGI app (no server, no port binding)."""

    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=target)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path)

    return asyncio.run(run())


def test_health_returns_200_json() -> None:
    response = get(app, "/health")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "qa-copilot-api"
    assert payload["version"]  # non-empty version string
    assert payload["env"] == "development"
    assert payload["timestamp"]


def test_health_timestamp_is_utc_within_bounds() -> None:
    before = datetime.now(UTC)
    payload = get(app, "/health").json()
    after = datetime.now(UTC)
    ts = datetime.fromisoformat(payload["timestamp"])
    assert ts.utcoffset() is not None  # tz-aware UTC, not naive local time
    assert before <= ts <= after


def test_health_reflects_injected_settings() -> None:
    assert get(create_app(Settings(env="staging")), "/health").json()["env"] == "staging"


def test_settings_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QA_COPILOT_ENV", "production")
    monkeypatch.setenv("QA_COPILOT_LOG_LEVEL", "DEBUG")
    # `_env_file=None` keeps the test hermetic (only process env vars, not
    # the repo `.env`); the pydantic mypy plugin's generated signature drops
    # BaseSettings' private init kwargs, hence the ignore.
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.env == "production"
    assert settings.log_level == "DEBUG"


def test_settings_reads_env_file_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:8080/v1")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://qa:qa@localhost:5433/qa_copilot")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.llm_base_url == "http://localhost:8080/v1"
    assert settings.database_url is not None
    assert settings.database_url.endswith(":5433/qa_copilot")


def test_openapi_documents_health() -> None:
    response = get(app, "/openapi.json")
    assert response.status_code == 200
    assert "/health" in response.json()["paths"]
