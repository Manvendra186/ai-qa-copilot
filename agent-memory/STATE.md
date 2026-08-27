# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.1 ✓ · S0.2 ✓ · S0.3 ✓ · S0.4 ✓ · S0.5 ✓ · S0.6 ✓ · S0.7 ✓ · S0.8 ✓ · **S0.9 ✓ — next: S0.10**

## 2. Just completed

- 2026-08-27 · **S0.9 — jobs API** (commit `2051749`, details: SESSION_LOG.md):
  `qa_copilot_api.jobs` — async job submission + SSE (build bible §11/§19).
  `POST /projects/{id}/requirements/analyze` → **202** `{job_id}` (member+; requirement
  persisted, re-analyze idempotent) · `GET /jobs/{id}` (viewer+; non-member 404) ·
  `GET /events?scope=job|project` (SSE in the S0.7 shell shape: `job.started`/`stage.*`/
  `progress`/`job.completed`; 15s heartbeat). `JobAgent` protocol + `StubAgent`
  (deterministic six §4 stages — **S1.x swaps in the LLM agent through the same
  protocol**). State machine queued→running→completed|failed; `reap_orphans()` on
  startup (PID-scoped). In-process pub/sub bus (multi-worker → Redis, noted).
  **97 unit tests green** · mypy strict clean (34 files — 34 errors fixed, incl. S0.8
  debt) · ruff check+format ✓.
- 2026-08-27 · **S0.8 — auth baseline** (commit `6df40a6`, details: SESSION_LOG.md):
  `qa_copilot_api.auth` — PBKDF2-SHA256 + HS256 JWT + Bearer; **project-scoped** RBAC via
  `project_members.role` (non-member 403 before project lookup; `users.role` default
  only); routes login/me/projects (viewer+), project delete (owner); `AUTH_TOKEN_SECRET`
  required (missing → 500). Migration `2d783f832c48` (`project_members`,
  `users.password_hash`); seed links dev user as owner. **82 unit tests green** ·
  live smoke ✓.

- 2026-08-27 · **S0.7 — React shell** (commit `4d8840b`, details: SESSION_LOG.md): pnpm
  workspace at root (one-command dev/build/preview/lint/format); `apps/web` = React 18 +
  Vite 6 + TS strict + Tailwind 4 + ESLint 9/Prettier 3 — **S0.1 web half closed**.
  Shell: `Header` (SSE badge), `PipelineView` (six §4 stages, a11y progressbars),
  `EventLog`, `useJobEvents` (native `EventSource` → strict reducer over the §11 event
  contract). Dev-only mock SSE `GET /mock/events` (same shape as `GET /events`); `/api`
  dev proxy → :8000. Verified: format/lint/build ✓ · live mock SSE timeline ✓.
- 2026-08-26 · **S0.6 — AI gateway** (details: SESSION_LOG.md): `qa_copilot_ai`
  gateway/redaction/prompts + DB prompt registry + `ai_actions`; 60 tests green,
  live LM Studio check OK. Open: `VECTOR_DIM = 1536` still provisional (no
  embedding model served locally yet).
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

**S0.10 — demo app v0** (build bible §19, spec §23): separate repo
`ai-qa-copilot-demo-app` — Express + SQLite + React; `/login /products /cart /checkout`;
defect-injection via env flags.
- **Exit criterion:** manual smoke passes; one defect flag changes behavior.
- Queued follow-ups (not S0.10 blockers): web shell still consumes the mock SSE
  (`/mock/events`) — point `useJobEvents` at `GET /events` with a fetch-based reader
  (EventSource can't set `Authorization`; JWT landed S0.8) · `StubAgent` → real LLM
  agent (S1.1/S1.2) through the `JobAgent` protocol · SSE bus is in-process —
  multi-worker deploy needs Redis pub/sub.

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- Docker **running** (Desktop, per-user; CLI v29.7.2, Compose v5.4.0; on the **USER** PATH — refresh
  `$env:Path` in long-lived shells)
- **Infra up:** `qa-copilot-db` pgvector/pg16 **0.0.0.0:5433→5432** (qa/qa @ qa_copilot) ·
  `qa-copilot-redis` :6379 · named volumes
- Native PG16 service `postgresql-x64-16` running on 5432 (user decision; no pgvector)
- **Node (installed 2026-08-26):** `node v22.23.2` (LTS; `%LOCALAPPDATA%\hermes\node`,
  first on user PATH) + backup `v24.19.0` (winget `OpenJS.NodeJS.LTS`, user scope) ·
  `npm 12.0.2` · `pnpm 11.24.0` (npm -g, prefix = hermes node dir)
- **Web toolchain (S0.7):** pnpm 11 workspace at repo root · `apps/web` =
  React 18.3 + Vite 6.4 + TS 5.8 (strict) + Tailwind 4 · ESLint 9 (flat) + Prettier 3
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
- S0.8 auth: dev-mode JWT HS256 (PyJWT) + PBKDF2-SHA256 (stdlib `hashlib`) ·
  `AUTH_TOKEN_SECRET` REQUIRED, no code fallback (missing → 500 with readable body) ·
  RBAC is **project-scoped** via `project_members.role` — `users.role` is a default only
  (§31.3) · role check precedes project lookup (non-members → 403, no existence leak) ·
  login dummies the hash verify for unknown users (timing)
- S0.9 jobs: `JobAgent` protocol is the **only** replaceable seam (S1.x swaps in the LLM
  agent without API changes) · `StubAgent` = deterministic placeholder (six §4 stages) ·
  state machine queued→running→completed|failed (illegal edge → `InvalidJobTransition`) ·
  `job.started` emitted before the agent runs (no event loss) · SSE bus in-process
  pub/sub (multi-worker deploy → Redis) · `sse_stream()` typed `AsyncGenerator` so
  `aclose()` typechecks

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
- Web shell: `apps/web/` (React 18 + Vite 6 + TS + Tailwind 4; mock SSE:
  `vite.config.ts` → `GET /mock/events`; pipeline contract: `src/lib/pipeline.ts`)
- Auth (S0.8): `apps/api/src/qa_copilot_api/auth.py` (hash/JWT/deps) · `routes.py`
  (login/me/projects) · `config.py` (`auth_token_secret`) · membership lookups:
  `packages/repository/src/qa_copilot_repository/membership.py` · migration
  `infra/migrations/versions/2d783f832c48_*.py`
- Jobs (S0.9): `apps/api/src/qa_copilot_api/jobs.py` (JobAgent/StubAgent/JobBus/
  `sse_stream`/`reap_orphans`) · `routes.py` (analyze/jobs/events) · `schemas.py`
  (Analyze/JobResponse) · `tests/unit/test_jobs.py`

## 7. Open questions / gotchas

- **PATH gotcha:** docker CLI lives on the USER PATH (per-user install) — terminals opened before
  install don't see it; refresh `$env:Path` from Machine+User (or open a new shell).
- **pnpm 11:** the `pnpm` field in `package.json` is IGNORED — settings like
  `onlyBuiltDependencies` (esbuild, needed by Vite) go in `pnpm-workspace.yaml`.
- **Vite dev binds `[::1]:5173`:** `curl http://127.0.0.1:5173` → connection refused;
  use `http://localhost:5173`.
- **pydantic v2:** `Field(..., strip_whitespace=True)` is a deprecated v1 kwarg (mypy strict
  rejects it) — use `Annotated[str, StringConstraints(...)]` (learned in S0.4).
- **ruff isort:** `qa_copilot_*` packages are NOT detected as first-party (src-layout workspace) —
  they sort in the third-party block, no blank line between `pydantic`/`pytest` and them.
- **mypy strict + pydantic:** wire-string/negative cases must go through `model_validate` — typed
  constructors are arg-checked (`status="completed"` → arg-type error).
- **pydantic-settings + mypy (S0.9):** the private `_env_file` init kwarg is invisible to mypy
  (stub limitation — reproduced with a minimal `BaseSettings` subclass) → test calls carry
  `# type: ignore[call-arg]` (3× in tests/unit); drop when stubs improve.
- **SQLAlchemy:** `metadata` is reserved on DeclarativeBase — JSONB `metadata` columns use the
  `metadata_` Python attribute (learned in S0.5).
- **ruff B023:** factory lambdas in loops must bind the loop vars
  (`lambda title=title, i=i: ...`) — hit in S0.5 seed.
- **Alembic + pgvector:** generated migrations import `pgvector.sqlalchemy` by *attribute* — add
  `import pgvector.sqlalchemy` at the top of the migration or import fails on apply (learned S0.5).
- **Postgres UUID columns (S0.8):** test fixtures must seed real UUIDs — string ids like
  `prj-acme` → `invalid input syntax for type uuid`. Use deterministic `uuid5` values.
- **SQLAlchemy `db.delete(parent)` (S0.8):** with `ondelete="CASCADE"` children whose FK is a
  composite PK, the ORM tries to NULL out the PK instead of letting Postgres cascade —
  delete the child rows explicitly first (S0.8 project delete route).
- **Test DB teardown (S0.8):** `DROP DATABASE` fails with `ObjectInUse` if engine pools
  still hold connections — `engine.dispose()` (ALL engines, incl. `app.state.engine`) first.
- **PowerShell + curl.exe (S0.8):** JSON bodies get mangled through the tool shell —
  write a small Python (urllib) script for API smoke tests instead.
