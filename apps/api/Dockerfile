# AI QA Copilot API — production image (build bible §19 S8.5).
#
# The uv workspace must stay intact, so build from the REPO ROOT:
#   docker build -f apps/api/Dockerfile -t qa-copilot-api .
#
# Posture:
#   - two stages: build tooling never ships in the runtime image
#   - non-root: the app runs as `appuser` (system uid, nologin shell)
#   - healthcheck-friendly: `/health` is a no-I/O liveness probe and the
#     image HEALTHCHECK hits it (compose adds the same check per-service)
#   - alembic ships in the runtime image on purpose: the compose `migrate`
#     one-shot service reuses this image for `alembic upgrade head`

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# uv first (its wheel is architecture-specific; the layer caches it).
RUN pip install --no-cache-dir uv==0.8.17

# Workspace manifests + lock file (the dependency-layer cache key).
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY packages/ai/pyproject.toml packages/ai/pyproject.toml
COPY packages/domain/pyproject.toml packages/domain/pyproject.toml
COPY packages/execution/pyproject.toml packages/execution/pyproject.toml
COPY packages/integrations/pyproject.toml packages/integrations/pyproject.toml
COPY packages/knowledge/pyproject.toml packages/knowledge/pyproject.toml
COPY packages/repository/pyproject.toml packages/repository/pyproject.toml

# Workspace source (the local packages are installed from here).
COPY . .

RUN uv venv --python python3.12 /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv sync --frozen --no-dev && \
    # alembic lives in the dev group locally; the prod `migrate` service
    # needs it, so install it explicitly (the floor matches the dev group).
    VIRTUAL_ENV=/opt/venv uv pip install 'alembic>=1.13'

# --- runtime ---------------------------------------------------------------

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root by design: system uid, no login shell, no sudo.
RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --home-dir /srv/qa-copilot --shell /usr/sbin/nologin appuser

WORKDIR /srv/qa-copilot

# venv (identical python path in both stages → shebangs stay valid) plus the
# app code: packages, alembic.ini and infra/migrations ride along.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/apps /srv/qa-copilot/apps
COPY --from=builder /build/packages /srv/qa-copilot/packages
COPY --from=builder /build/alembic.ini /srv/qa-copilot/alembic.ini
COPY --from=builder /build/infra /srv/qa-copilot/infra
COPY --from=builder /build/pyproject.toml /srv/qa-copilot/pyproject.toml

RUN chown -R appuser:appgroup /srv/qa-copilot && \
    chown -R appuser:appgroup /opt/venv
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# Single worker in the default configuration (Phase 0: in-process jobs —
# see the S0.9 note in qa_copilot_api.main). Scale workers only when the
# job runner moves out of the process.
CMD ["uvicorn", "qa_copilot_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
