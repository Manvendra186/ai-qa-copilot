"""Runtime configuration for the AI QA Copilot API (build bible §31, S0.3).

Environment-driven (no prefix — env var names match ``.env.example``);
secrets never live in code.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from qa_copilot_ai.config import load_dotenv

# The ai package reads its tuning knobs (``AI_MAX_INPUT_TOKENS``,
# ``AI_MAX_OUTPUT_TOKENS``, ``AI_TEMPERATURE``, timeouts, retries) from the
# process environment — expose the repo ``.env`` (the same file ``Settings``
# reads below) to ``os.environ`` so one file controls everything. Existing
# shell environment variables always win.
_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    """Operator-tunable API settings.

    Keep this flat and explicit: every value an operator may need at
    runtime (environment, log level, LLM endpoints, infra URLs) lives here
    so endpoints can report their context instead of guessing.
    """

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[4] / ".env",
        extra="ignore",
    )

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

    # --- Artifacts (S3.1, §15: local store root; S3.2 read/download) ---
    # Root of the execution artifact store. ``ArtifactStore.resolve`` maps a
    # store-relative URI (``runs/{run_id}/{test_id}/{name}``) under this path;
    # ``ARTIFACT_STORE_ROOT`` relocates it (defaults to ``data/artifacts``).
    artifact_store_root: str | None = None

    # --- Auth (S0.8, §31.3: dev-mode single user + JWT) ---
    # HS256 signing secret (16+ chars). No default on purpose: fail loud.
    auth_token_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTH_TOKEN_SECRET"),
    )

    # --- Auth hardening (S8.1, §19): login brute-force throttle (Redis) ------
    # Max failed logins per email / per IP before ``429`` + ``Retry-After``.
    login_throttle_max_failures: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices("LOGIN_THROTTLE_MAX_FAILURES"),
    )
    login_throttle_window_s: int = Field(
        default=60,
        ge=1,
        validation_alias=AliasChoices("LOGIN_THROTTLE_WINDOW_S"),
    )

    # --- Deployment hardening (S8.5, §19) ------------------------------------
    # Comma-separated allowed CORS origins (e.g. "https://app.example.com").
    # Empty (the default) = locked down: no Access-Control-Allow-Origin is
    # ever emitted, so only same-origin callers can read responses.
    cors_origins: str = Field(
        default="",
        validation_alias=AliasChoices("QA_COPILOT_CORS_ORIGINS", "CORS_ORIGINS"),
    )
    # Request rate limiting (per IP + per authenticated user, Redis-backed,
    # 429 + Retry-After). Off by default — local-first dev loops and the
    # test suite must not trip their own limiter; ``docker-compose.prod.yml``
    # turns it on (the bible S8.5 exit criterion is enforced there + in tests).
    rate_limit_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("QA_COPILOT_RATE_LIMIT_ENABLED", "RATE_LIMIT_ENABLED"),
    )
    rate_limit_max_requests: int = Field(
        default=120,
        ge=1,
        validation_alias=AliasChoices(
            "QA_COPILOT_RATE_LIMIT_MAX_REQUESTS", "RATE_LIMIT_MAX_REQUESTS"
        ),
    )
    rate_limit_window_s: int = Field(
        default=60,
        ge=1,
        validation_alias=AliasChoices("QA_COPILOT_RATE_LIMIT_WINDOW_S", "RATE_LIMIT_WINDOW_S"),
    )

    # --- Jobs (S0.9, §31.2: 202 + SSE) ---
    # StubAgent progress-tick delay in seconds (dev pacing; tests use ~0.01).
    job_tick_delay_s: float = Field(
        default=0.25,
        ge=0.0,
        validation_alias=AliasChoices("JOB_TICK_DELAY_S"),
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (safe to call from any endpoint)."""
    return Settings()
