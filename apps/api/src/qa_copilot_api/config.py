"""Runtime configuration for the AI QA Copilot API (build bible §31, S0.3).

Environment-driven (no prefix — env var names match ``.env.example``);
secrets never live in code.
"""

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Operator-tunable API settings.

    Keep this flat and explicit: every value an operator may need at
    runtime (environment, log level, LLM endpoints, infra URLs) lives here
    so endpoints can report their context instead of guessing.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = Field(
        default="development",
        validation_alias=AliasChoices("QA_COPILOT_ENV", "ENV"),
    )
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("QA_COPILOT_LOG_LEVEL", "LOG_LEVEL"),
    )

    # --- LLM (local llama server, OpenAI-compatible — no cloud, §31.1) ---
    llm_base_url: str | None = None
    llm_model: str | None = None

    # --- Infrastructure (S0.2 docker-compose) ---
    database_url: str | None = None
    redis_url: str | None = None

    # --- App under test (Playwright target, §31.11) ---
    app_under_test: str | None = None

    # --- Auth (S0.8, §31.3: dev-mode single user + JWT) ---
    # HS256 signing secret (16+ chars). No default on purpose: fail loud.
    auth_token_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTH_TOKEN_SECRET"),
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (safe to call from any endpoint)."""
    return Settings()
