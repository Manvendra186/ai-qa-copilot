"""S8.5 production artifacts — structure checks (build bible §19 S8.5).

These tests pin the *shape* of the deployment artifacts so the S8.5 exit
criteria stay enforced without a Docker daemon:

- docker-compose.prod.yml: Postgres/Redis/API publish NO ports (Caddy is
  the only public front door), the API has a healthcheck + restart policy
  + resource limits, ``AUTH_TOKEN_SECRET`` is required (fail-loud), rate
  limiting is ON, and the one-shot ``migrate`` service must complete
  before the API starts
- apps/api/Dockerfile: two stages, a non-root runtime user, a HEALTHCHECK
- infra/caddy/Caddyfile: local-first ``tls internal`` + reverse proxy
- .dockerignore keeps .env / VCS state / agent memory out of images
- .gitleaks.toml + the CI workflow: secret scanning, fail-closed

(Actual middleware behaviour lives in test_s85_security.py.)
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_prod_compose_structure() -> None:
    doc = yaml.safe_load(_text("docker-compose.prod.yml"))
    services = doc["services"]
    for name in ("db", "redis", "migrate", "api", "caddy"):
        assert name in services, name

    # No database-shaped or app-shaped port is published, anywhere.
    for name in ("db", "redis", "api"):
        assert "ports" not in services[name], f"{name} must not publish ports"
    # Caddy is the single public entrypoint.
    caddy_ports = [str(p) for p in services["caddy"]["ports"]]
    assert any(p.startswith("80:") for p in caddy_ports)
    assert any(p.startswith("443:") for p in caddy_ports)

    api = services["api"]
    assert api["environment"]["AUTH_TOKEN_SECRET"].count(":?") == 1  # fail loud
    assert api["environment"]["RATE_LIMIT_ENABLED"] == "true"
    assert api["environment"]["REDIS_URL"].startswith("redis://redis:")
    assert api["healthcheck"]["test"]
    assert api["restart"] == "unless-stopped"
    assert api["deploy"]["resources"]["limits"]["memory"]
    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert api["depends_on"]["db"]["condition"] == "service_healthy"

    migrate = services["migrate"]
    assert migrate["restart"] == "no"  # one-shot
    assert "alembic" in " ".join(migrate["command"])

    for name in ("db", "redis", "caddy"):
        assert services[name]["restart"] == "unless-stopped"
    assert services["db"]["healthcheck"]["test"]
    assert services["redis"]["healthcheck"]["test"]


def test_dockerfile_nonroot_and_healthcheck() -> None:
    df = _text("apps/api/Dockerfile")
    assert "FROM python:3.12-slim" in df
    assert len(re.findall(r"^FROM ", df, re.M)) == 2  # builder + runtime
    assert re.search(r"^USER appuser$", df, re.M)  # non-root runtime
    assert not re.search(r"^USER root$", df, re.M)
    assert "HEALTHCHECK" in df and "/health" in df
    assert "uvicorn" in df and "qa_copilot_api.main:app" in df


def test_caddyfile_local_tls_and_proxy() -> None:
    cf = _text("infra/caddy/Caddyfile")
    assert "tls internal" in cf  # local-first (build bible: local-first option)
    assert "reverse_proxy api:8000" in cf
    assert "Strict-Transport-Security" in cf
    assert "format json" in cf  # structured access logs


def test_dockerignore_excludes_secrets_and_state() -> None:
    di = _text(".dockerignore")
    for needle in (".env", ".git", "agent-memory", "backups", "__pycache__"):
        assert needle in di, needle


def test_secret_scanning_fail_closed_in_ci() -> None:
    gl = _text(".gitleaks.toml")
    assert "allowlist" in gl  # default rules stay active; only samples allowed
    wf = _text(".github/workflows/ci.yml")
    doc = yaml.safe_load(wf)
    secret_steps = doc["jobs"]["secrets"]["steps"]
    steps = " ".join(str(s.get("uses", "")) + " " + str(s.get("run", "")) for s in secret_steps)
    assert "gitleaks" in steps  # the secrets job runs gitleaks
    assert "fetch-depth: 0" in wf  # full history — old secrets still fail CI


def test_backup_restore_scripts_shape() -> None:
    b = _text("scripts/backup.sh")
    r = _text("scripts/restore.sh")
    for body in (b, r):
        assert body.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in body
    assert "pg_dump --format=custom" in b and "tar -czf" in b  # gzip output
    assert "pg_restore" in r and "--single-transaction" in r  # atomic restore
