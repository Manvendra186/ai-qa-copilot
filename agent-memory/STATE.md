# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation **complete** → Phase 1 — Requirement → Test Design (in progress)
- **Step:** S0.1–S1.1 ✓ · **S1.2 next**

## 2. Just completed

- 2026-08-27 · **S1.1 — Requirement Agent** (commit `6a1bf88`, details: SESSION_LOG.md):
  prompt registry v1 (`PromptSpec`/`InMemoryPromptStore`/`FilePromptStore`,
  `packages/ai/prompts/requirement-analyst.v1.md`) + `RequirementAgent` —
  schema-validated `RequirementAnalysis` (strict pydantic; `suggested_test_types`
  validated against `SUGGESTED_TEST_TYPES` = domain `TestType`) through the S0.6
  gateway. `RequirementJobAgent` implements the S0.9 `JobAgent` protocol — real
  analysis + `ai_actions` audit row inside the job pipeline; `StubAgent` stays the
  fallback when no LLM is configured. **Exit: 10 fixture requirements → 10/10
  schema-valid ✓.** 103 tests · mypy strict clean (37 files) · ruff ✓.
- 2026-08-27 · **S0.9 — jobs API** (commit `2051749`): `POST .../analyze` → 202 ·
  `GET /jobs/{id}` · `GET /events` SSE (`job.started`/`stage.*`/`progress`/
  `job.completed`, 15s heartbeat) · `JobAgent`/`StubAgent` seam (S1.1 filled the
  real agent) · queued→running→completed|failed + `reap_orphans`.
- 2026-08-27 · **S0.10 — demo app v0** (separate repo `ai-qa-copilot-demo-app`,
  commit `43739a5`): Express 4 + `better-sqlite3` + React 18/Vite 6; `/login
  /products /cart /checkout`; demo user `qa`/`qa1234`; defect flags 1:1 §16.
  **Smoke 11/11 · defect-check 7/7 · build ✓.**
- 2026-08-26/27 · **S0.1–S0.8**: monorepo skeleton (uv+pnpm) · compose infra
  (PG16+pgvector :5433, Redis) · FastAPI skeleton · domain package ·
  SQLAlchemy+Alembic+seed · AI gateway (LM Studio live ✓) · React shell (mock SSE) ·
  auth baseline (JWT + project RBAC).

## 3. NEXT STEP (start here)

**S1.2 — Test Design Agent** (build bible §19 Phase 1): generate functional/negative/
boundary/risk/a11y/security test cases from the S1.1 `RequirementAnalysis`; persist
test cases through the same job-pipeline `JobAgent` seam (prompt registry §31.6).
- **Exit criterion:** step coverage ≥ 85% vs oracle on 10 requirements.
- Queued follow-ups (not S1.2 blockers): web shell still consumes the mock SSE
  (`/mock/events`) — point `useJobEvents` at `GET /events` with a fetch-based reader
  (EventSource can't set `Authorization`; JWT landed S0.8) · SSE bus is in-process —
  multi-worker deploy needs Redis pub/sub · demo-app `Dockerfile` unverified (S3.1).

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
- Demo app (S0.10 ✓): `c:\Users\manve\Workspace\ai-qa-copilot-demo-app` (separate repo;
  `pnpm dev` · `pnpm smoke` · `pnpm defect-check` · demo user `qa`/`qa1234` · server :4000)
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
- Requirement agent (S1.1): `packages/ai/src/qa_copilot_ai/agents/requirement.py`
  (`RequirementAgent`/`RequirementAnalysis`/`RequirementInput`) · `prompts.py`
  (`PromptSpec`/stores/`render_prompt`) · `packages/ai/prompts/requirement-analyst.v1.md`
  (v1 prompt) · `jobs.py` (`RequirementJobAgent` — real agent in the S0.9 pipeline;
  `StubAgent` fallback when no LLM) · `main.py` (`_build_jobs_agent`) ·
  `tests/unit/test_requirement_agent.py`

## 7. Open questions / gotchas

- **Env leak → wrong agent (S1.1):** `qa_copilot_repository.db.get_database_url()` → `_load_dotenv()` writes
  repo `.env` keys (incl. `LLM_BASE_URL`/`LLM_MODEL`) into `os.environ`; alembic's `env.py` calls it in test
  fixtures, and pydantic-settings reads env vars even with `_env_file=None` → the app silently used
  `RequirementJobAgent` (real LM Studio call; 8 job tests hung). Stub-contract tests must pass
  `llm_base_url=None, llm_model=None` explicitly (init kwargs beat env vars).
- **mypy strict + `dict[str, object]` (S1.1):** `audit_dict()` values are `object` — `int(audit["x"])` fails; use `cast(int, ...)`.
- **node:sqlite (S0.10):** experimental on Node 22 — demo app uses `better-sqlite3` (approve in `pnpm-workspace.yaml` `onlyBuiltDependencies`/`allowBuilds`).
- **PowerShell NativeCommandError (S0.10):** any child-process stderr makes the tool shell report
  failure — read the actual output/exit code; `git --no-pager`; servers via `Start-Process -PassThru`.
- **PATH gotcha:** docker CLI is on the USER PATH — old terminals don't see it; refresh `$env:Path` from Machine+User.
- **pnpm 11:** the `pnpm` field in `package.json` is IGNORED — `onlyBuiltDependencies` (esbuild) goes in `pnpm-workspace.yaml`.
- **Vite dev binds `[::1]:5173`:** `curl http://127.0.0.1:5173` refused — use `http://localhost:5173`.
- **pydantic v2 (S0.4):** `Field(..., strip_whitespace=True)` is a deprecated v1 kwarg — use `Annotated[str, StringConstraints(...)]`.
- **ruff isort (S1.1):** `qa_copilot_*` is NOT first-party (src-layout workspace) — sorts in the third-party block.
- **mypy strict + pydantic:** wire-string/negative cases must go through `model_validate` — typed
  constructors are arg-checked (`status="completed"` → arg-type error).
- **pydantic-settings + mypy (S0.9):** the private `_env_file` init kwarg is invisible to mypy →
  test calls carry `# type: ignore[call-arg]`; drop when stubs improve.
- **SQLAlchemy (S0.5/8):** `metadata` is reserved on DeclarativeBase — use the `metadata_` attribute ·
  `db.delete(parent)` with composite-PK children NULLs the PK instead of cascading — delete children first ·
  `engine.dispose()` (ALL engines) before `DROP DATABASE` or it fails `ObjectInUse`.
- **ruff B023 (S0.5):** factory lambdas in loops must bind loop vars (`lambda title=title, i=i: ...`).
- **Alembic + pgvector (S0.5):** migrations import `pgvector.sqlalchemy` by *attribute* — add `import pgvector.sqlalchemy` at top.
- **Postgres UUID columns (S0.8):** fixtures must seed real UUIDs (`uuid5`) — string ids → `invalid input syntax for type uuid`.
- **PowerShell + curl.exe (S0.8):** JSON bodies get mangled through the tool shell — use a Python (urllib) script for API smoke.
