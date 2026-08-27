# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.1 ✓ · S0.2 ✓ · S0.3 ✓ · S0.4 ✓ · S0.5 ✓ · S0.6 ✓ · S0.7 ✓ · **S0.8 ✓ — next: S0.9**

## 2. Just completed

- 2026-08-27 · **S0.8 — auth baseline** (commit `6df40a6`): `qa_copilot_api.auth` —
  PBKDF2-SHA256 password hashing (stdlib `hashlib`) + HS256 JWT (PyJWT) + Bearer parsing;
  `get_current_user()` and `require_role()` dependencies — authorization is
  **project-scoped** via `project_members.role` (§31.3; `users.role` is a default only);
  non-members get 403 (checked before project lookup — no existence leak); login runs a
  dummy hash for unknown users (timing). Routes: `POST /api/v1/auth/login`,
  `GET /api/v1/auth/me`, `GET /api/v1/projects`, `GET /projects/{id}` (viewer+),
  `DELETE /projects/{id}` (owner; deletes memberships explicitly first — ORM would
  otherwise try to null out the composite PK). `AUTH_TOKEN_SECRET` is REQUIRED (no
  fallback; missing → 500 with readable body); `AUTH_DEV_PASSWORD` sets the dev user's
  password in seed (`dev@local.dev`, linked as project owner). Migration `2d783f832c48`
  adds `project_members` (composite PK `(project_id, user_id)`, role = plain VARCHAR of
  the domain wire string, CASCADE FKs). Tests: `tests/unit/test_auth.py` (21 tests,
  scratch DB per test, real Postgres, full 401/200/403 matrix) — **82 unit tests green**
  · ruff check+format ✓ · alembic head ✓ · seed idempotent ✓ · live smoke ✓ (login/me/
  list/detail + 401/bad-token/bad-password cases via urllib script).

- 2026-08-27 · **S0.7 — React shell** (commit `4d8840b`): pnpm workspace at root
  (`pnpm-workspace.yaml` + private root `package.json` → one-command
  `dev/build/preview/lint/format`); `apps/web` = React 18 + Vite 6 + TypeScript
  (strict; separate `tsconfig.json` / `tsconfig.node.json`) + Tailwind CSS 4
  (configless, via `@tailwindcss/vite`). ESLint 9 flat config (TS recommended +
  react-hooks + react-refresh, Prettier-clean) + Prettier 3 (width 100, single
  quotes) — **S0.1 web half closed**. Shell: `Header` (SSE connection badge),
  `PipelineView` (six §4 stages, per-stage progress bars, `role=progressbar` +
  `aria-current=step`), `EventLog` (live feed), `useJobEvents` (native
  `EventSource` → strict reducer over `job.started`/`stage.started`/`progress`/
  `stage.completed`/`job.completed`; replay button). Mock SSE: dev-only Vite
  middleware `GET /mock/events` streaming standard SSE frames — same shape S0.9
  will serve from `GET /events`; `/api` dev proxy → FastAPI :8000. Verified:
  `pnpm install` ✓ · `pnpm format:check` ✓ · `pnpm lint` ✓ · `pnpm build` ✓
  (tsc strict ×2 + vite build) · `pnpm dev` live: shell HTML served + full mock
  SSE timeline via curl (progress-bar animation is contract-level verified).
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

**S0.9 — jobs API** (build bible §19): async job submission (202) + SSE event stream.
- **Exit criterion:** per §19 — job POST returns 202 with id; events stream over
  `GET /events` in the S0.7 shell contract shape (`job.started`/`stage.*`/`progress`/
  `job.completed`).
- Notes: `useJobEvents` (S0.7 shell) already speaks the event contract — point it at the
  real endpoint and drop the mock SSE. S0.8 landed **header-based JWT**, so the web
  client needs a fetch-based SSE reader (`EventSource` can't set `Authorization`;
  flagged in S0.7 session log).

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
