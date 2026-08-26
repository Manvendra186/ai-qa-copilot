# AI QA Copilot

AI QA Copilot for Playwright-based QA teams:
requirement → test design → automation → execution → failure analysis → fix.

## Start here (for humans and for every new AI session)

1. **`agent-memory/STATE.md`** — where the project is right now, the next step, environment facts.
   *(Every new AI session reads this file FIRST. Protocol: build bible §32.)*
2. **`docs/AI_QA_Copilot_Build_Bible_v1.1.md`** — master build reference: vision, architecture,
   step plan (§19), decisions (§29), Phase 0 decisions (§31).
3. **`agent-memory/SESSION_LOG.md`** — what past sessions did.

## Rules of work (short version)

- One step at a time (build bible §19); verify the step's exit criterion; commit `step S#.x: ...`.
- Update `agent-memory/STATE.md` at the end of every session (or when context runs low mid-step).
- Local LLM via llama server; hard context budgets (build bible §31.1). No cloud LLMs.
- Human approval for every code change the product proposes — and for our own big steps.

## Quickstart

1. **Infra (S0.2)** — PostgreSQL+pgvector + Redis:
   ```powershell
   docker compose up -d
   docker compose exec db psql -U qa -d qa_copilot -c 'SELECT 1'   # → 1
   docker compose exec redis redis-cli ping                        # → PONG
   ```
   *(This machine: native PG16 holds 5432 → `.env` sets `POSTGRES_PORT=5433`
   and `DATABASE_URL` on 5433.)*
2. **Database (S0.5)** — schema + seed:
    ```powershell
    uv run alembic upgrade head    # apply migrations (pgvector extension + §10 tables)
    uv run python scripts/seed.py  # idempotent dev fixtures (safe to run twice)
    docker compose exec db psql -U qa -d qa_copilot -c '\dt'   # 18 core tables
    ```
3. **API (S0.3)** — FastAPI skeleton:
   ```powershell
   uv sync
   uv run uvicorn qa_copilot_api.main:app --port 8000
   curl http://127.0.0.1:8000/health   # → {"status":"ok","service":"qa-copilot-api",...}
   ```
