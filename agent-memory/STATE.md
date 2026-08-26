# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.1 ✓ · S0.2 ✓ · S0.3 ✓ · S0.4 ✓ · S0.5 ✓ · **S0.6 ✓ — next: S0.7**
  (React shell — **blocked on Node LTS install**, see §7)

## 2. Just completed

- 2026-08-26 · **S0.6 — AI gateway** (commit this session): `qa_copilot_ai` — `gateway.py`
  (async `LLMGateway` over OpenAI-compatible `/chat/completions`: `chat()` +
  `chat_stream()` NDJSON/SSE, `usage` from server w/ char-count estimate fallback,
  120 s timeout, one retry on transport errors only, hard `LLMError` w/ status
  otherwise — no silent model-swap; per-call `ai_call` log record with
  `agent/model/tokens_in/tokens_out/latency_ms/retries/redactions/input_hash`),
  `redaction.py` (bearer/GitHub/OpenAI/AWS/JWT/DSN-password/key-value patterns →
  `***REDACTED***`, idempotent, applied to wire + logs; §31.7 leaks=0), `prompts.py`
  (`PromptSpec`, `PromptStore` protocol, `InMemoryPromptStore`, strict `{{var}}`
  rendering — missing var raises). Repository: `prompts.load_prompt()` (DB-backed
  registry, §31.6) + `audit.record_ai_action()/record_ai_call()` → `ai_actions`.
  Runtime fixed: **LM Studio :8080, Qwen3.8-27B Q4_K_M** (27.3B params, n_ctx 100,096,
  completion-only) → `.env` + §31.1 note. Verified: **60 tests green** (fake-server
  unit tests + 3 live DB), `scripts/llm_live_check.py` → 2 live `ai_call` records
  (`usage_source: "reported"`, reply "QA copilot S0.6 live check OK") · ruff ✓ ·
  mypy strict ✓ (27 files).
  Open: `VECTOR_DIM = 1536` still provisional (no embedding model served locally yet).
- 2026-08-26 · **S0.5 — SQLAlchemy + Alembic + seed** (commit this session):
  `qa_copilot_repository` — `models.py` (18 §10 core tables + `prompt_versions`, typed
  `sa.Uuid(as_uuid=False)` → `str` ids, `metadata_` attr / `metadata` col, enums as
  plain `VARCHAR(32)` of domain wire values), `db.py` (`get_database_url()` → env/.env
  fallback, `make_engine()`, `session_scope()`); `infra/migrations` (Alembic, `env.py`
  reads URL via `db.get_database_url()`); initial migration `60fa1027d8d2` enables pgvector
  (`CREATE EXTENSION IF NOT EXISTS vector`) — **not dropped on downgrade**; `scripts/seed.py`
  idempotent (natural-key lookups; deterministic `uuid5` ids). Verified: `alembic upgrade head`
  ✓, `alembic current` → head, seed ×2 no duplicates, live vector ops ✓, **41 tests green**
  (incl. 2 DB smoke tests) · ruff ✓ · mypy strict ✓.
  Open: `VECTOR_DIM = 1536` provisional until the embedding model is chosen (S0.6).
- 2026-08-26 · **S0.4 — domain package** (commit `f2fccea`): `qa_copilot_domain` now holds the
  §10 core model as pydantic v2 — `enums.py` (TestType, Priority, RiskLevel, JobType, JobStatus,
  FailureCategory (§16), ArtifactType; all `StrEnum`, snake_case wire values matching §12) +
  `entities.py` (Project, Requirement, TestCase, Failure, Artifact, Job) + `base.DomainModel`
  (`extra="forbid"`, `from_attributes`); `NonBlankStr` = `StringConstraints(min_length=1,
  strip_whitespace=True)`; `confidence`/`progress` bounded 0.0–1.0; ids `str | None` (server-assigned);
  `TestCase.requirement_refs` mirrors the §10 M:N join. `pydantic>=2.9` added to domain deps;
  `TestCase`/`TestType` carry `__test__ = False` (pytest). **31 tests green · ruff ✓ · mypy strict ✓.**
- 2026-08-26 · **S0.3 — FastAPI skeleton** (commit `cbd623d`): `create_app(settings)` factory +
  module `app`; `Settings` (pydantic-settings, `.env` + fallbacks); stdlib JSON structured logging
  with uvicorn routed through it; `GET /health` → **200 verified live**; `py.typed`. 14 tests green.
- 2026-08-26 · **S0.2 — compose infra up** (commit `4446f1a`): native PG16 kept on 5432 → compose db
  on **5433** (`.env`); redis :6379; both healthy; pgvector **0.8.6** available (extension at S0.5).
- 2026-08-26 · **S0.1 — Monorepo skeleton (python half)** (commit `ed6dcaf`): uv workspace
  (7 members), ruff + mypy strict + pytest, pre-commit, `.env.example`.
- 2026-08-26 · Bootstrap: build bible **v1.1** (canonical) + `agent-memory/` + `README.md`.

## 3. NEXT STEP (start here)

**S0.7 — React shell** (build bible §19): React 18 + Vite + TypeScript + Tailwind,
pnpm, ESLint + Prettier — **blocked: Node LTS (20+) not installed on this machine**
(user action; also unblocks the S0.1 web-half linting).
- **Exit criterion:** `pnpm dev` serves the shell; `pnpm lint` + `pnpm build` clean.
- **If Node is still missing:** next doable work is S0.8 auth prep or the S0.9 jobs
  layer (202 + SSE) against the existing API — check with the user which to take.

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- Docker **running** (Desktop, per-user; CLI v29.7.2, Compose v5.4.0; on the **USER** PATH — refresh
  `$env:Path` in long-lived shells)
- **Infra up:** `qa-copilot-db` pgvector/pg16 **0.0.0.0:5433→5432** (qa/qa @ qa_copilot) ·
  `qa-copilot-redis` :6379 · named volumes
- Native PG16 service `postgresql-x64-16` running on 5432 (user decision; no pgvector)
- **Node/npm/pnpm: NOT installed** → S0.7 (React shell) needs Node 20+
- Toolchain in `.venv`: ruff 0.16.4 · mypy 2.3.1 · pytest 9.1.1 · pre-commit 4.6.2
- LLM (verified S0.6): **LM Studio (llama.cpp) `http://localhost:8080/v1`** · model id
  `.\Models\lmstudio-community\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q4_K_M.gguf` · 27.3B params ·
  n_ctx 100,096 · completion-only (no local embedding model → `VECTOR_DIM` stays 1536
  placeholder) · §31.1 budgets hold.
- python-docx ✗ — Markdown is the doc source of truth

## 5. Key decisions (full list: build bible §29)

- Local LLM only, no cloud · hard context budgets · text-first failure analysis
- Async jobs mandatory (202 + SSE) — local inference is slow
- One step per session · verify exit criterion · commit `step S#.x` · update this file
- Markdown is canonical; do not re-derive decisions already in §29
- Keep native PG16 on 5432; compose db on 5433 (S0.2, user decision)
- S0.4 domain: pydantic v2 · `StrEnum` wire strings · `extra="forbid"` · ids `str | None`
  (server-assigned) · `Project.repository_id` (bible §10 says `repo_id` — clearer name kept)
- S0.5 repo: SQLAlchemy 2.0 typed ORM · ids `sa.Uuid(as_uuid=False)` → `str` (matches domain) ·
  enums stored as plain `VARCHAR(32)` of the domain wire strings (no DB-side vocab duplication) ·
  Alembic URL centralized in `qa_copilot_repository.db` (never hardcoded in `alembic.ini`) ·
  pgvector enabled in migration, **not dropped on downgrade** · seed idempotent by
  natural-key lookups + deterministic `uuid5` ids
- S0.6 AI: gateway is the **only** LLM call site (§31.1) · one retry on transport errors
  only, hard `LLMError` otherwise (no silent model-swap) · redaction before wire + logs
  (§31.7) · `usage` from server with char-estimate fallback · prompt rendering fails
  loudly on missing variables · `ai_actions` payload = the `ai_call` log record

## 6. Pointers (paths only — no code here)

- Build bible: `docs/AI_QA_Copilot_Build_Bible_v1.1.md`
- Session history: `agent-memory/SESSION_LOG.md`
- v1.0 original (PDF, historical): `c:\Users\manve\Desktop\AI_QA_Copilot_Build_Bible.pdf`
- Demo app (later, S0.10): separate repo `ai-qa-copilot-demo-app`
- Domain entities: `packages/domain/src/qa_copilot_domain/{base,enums,entities}.py`
- ORM models: `packages/repository/src/qa_copilot_repository/models.py` (18 core tables)
- Migrations: `infra/migrations/` (initial: `60fa1027d8d2_initial_core_schema.py`)
- DB URL: `packages/repository/src/qa_copilot_repository/db.py` (env → `.env` → default)
- AI gateway: `packages/ai/src/qa_copilot_ai/{gateway,prompts,redaction}.py` · live check:
  `scripts/llm_live_check.py` · `ai_actions` writer: `qa_copilot_repository.audit`

## 7. Open questions / gotchas

- **PATH gotcha:** docker CLI lives on the USER PATH (per-user install) — terminals opened before
  install don't see it; refresh `$env:Path` from Machine+User (or open a new shell).
- **S0.1 web half pending:** pnpm workspace + ESLint + Prettier + `pnpm lint` — blocked on
  Node LTS (also needed for S0.7 React shell).
- **pydantic v2:** `Field(..., strip_whitespace=True)` is a deprecated v1 kwarg (mypy strict
  rejects it) — use `Annotated[str, StringConstraints(...)]` (learned in S0.4).
- **ruff isort:** `qa_copilot_*` packages are NOT detected as first-party (src-layout workspace) —
  they sort in the third-party block, no blank line between `pydantic`/`pytest` and them.
- **mypy strict + pydantic:** wire-string/negative cases must go through `model_validate` — typed
  constructors are arg-checked (`status="completed"` → arg-type error).
- **SQLAlchemy:** `metadata` is reserved on DeclarativeBase — JSONB `metadata` columns use the
  `metadata_` Python attribute (learned in S0.5).
- **ruff B023:** factory lambdas in loops must bind the loop vars
  (`lambda title=title, i=i: ...`) — hit in S0.5 seed.
- **Alembic + pgvector:** generated migrations import `pgvector.sqlalchemy` by *attribute* — add
  `import pgvector.sqlalchemy` at the top of the migration or import fails on apply (learned S0.5).
