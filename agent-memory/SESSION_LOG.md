# SESSION LOG — AI QA Copilot

> Append-only. Newest last. One entry per session/step.
> When a step completes, its detail moves here out of STATE.md.

## 2026-08-26 — Bootstrap session (review + build bible v1.1 + memory protocol)

- **Goal:** review v1.0 build bible (PDF) and refine it per user direction: local LLM via
  llama server, agent-memory folder for session continuity, token-efficient step-by-step build.
- **Did:**
  - Extracted + reviewed all 11 pages of v1.0 (pypdf, since it's a binary PDF).
  - Wrote `docs/AI_QA_Copilot_Build_Bible_v1.1.md` — all v1.0 sections carried forward plus:
    §10 data-model fixes (requirement↔test M:N join, `jobs`, `prompt_versions`,
    `ai_actions` model/tokens/latency), §11 async 202+SSE contract, §16 text-first failure
    analysis, §19 step plan (S0.1–S4.3, phases 5–8 detail-on-demand), §21/§22 numeric eval
    targets + reproducible defect flags, §23 demo-app spec, §29 new decision rows,
    §31 Phase 0 decisions (LLM strategy/budgets, auth baseline, repo model, observability,
    prompt registry, tooling, a11y, execution), §32 agent-memory protocol.
  - Created `agent-memory/STATE.md`, `agent-memory/SESSION_LOG.md`, `README.md`.
  - Environment probe: Python 3.11.9 ✓ · uv 0.11.32 ✓ · git 2.55.0 ✓ · Docker ✗ · Node/pnpm ✗.
- **Verified:** v1.1 file written in full (583 lines, sections 1–32 present, section numbering
  continuous); environment facts recorded in STATE.md §4.
- **Decisions:** Markdown = canonical doc (skip .docx) · local LLM + hard budgets ·
  async jobs mandatory · text-first failure analysis · one-step-per-session protocol.
- **Next session start:** S0.1 (monorepo skeleton) — see `STATE.md` §3.

## 2026-08-26 — S0.1 Monorepo skeleton (python half)

- **Goal:** build the §19 S0.1 monorepo skeleton; scoped to the python half per user
  direction ("use python here") because Node/npm/pnpm are not installed.
- **Did:**
  - uv workspace: virtual root `pyproject.toml` (`tool.uv.package = false`) with
    `[tool.uv.sources]` `workspace = true` for each member; members = `apps/api` +
    `packages/{domain,ai,repository,execution,knowledge,integrations}`, each a hatchling
    src-layout package (`qa-copilot-*`, `src/qa_copilot_*/__init__.py` + `py.typed`).
  - Tooling config in root `pyproject.toml`: ruff (B,E,F,I,UP,W; line-length 100; py311),
    mypy (strict, explicit_package_bases, namespace_packages), pytest (testpaths=tests).
  - `.pre-commit-config.yaml` (ruff + ruff-format, mypy, gitleaks, pre-commit-hooks),
    `.gitignore`, `.env.example` (LLM_BASE_URL, LLM_MODEL, DATABASE_URL, REDIS_URL,
    APP_UNDER_TEST), `scripts/.gitkeep`, `apps/web/README.md` placeholder.
  - `tests/unit/test_scaffold.py` — asserts all 7 packages import with `__version__`.
  - `uv sync` → `uv.lock` + `.venv` (ruff 0.16.4, mypy 2.3.1, pytest 9.1.1, pre-commit 4.6.2).
- **Verified:** `uv run ruff check .` ✓ · `uv run ruff format --check .` ✓ (13 files) ·
  `uv run mypy apps packages` ✓ (strict, 7 source files) · `uv run pytest -q` ✓ (2 passed).
- **Commit:** `ed6dcaf step S0.1: monorepo skeleton (python half; web tooling pending Node)`.
- **Decisions:** S0.1 scoped to python half (no Node on this machine). §29 decision
  React + TypeScript for the frontend is KEPT — web tooling is deferred, not dropped.
- **Next session start:** S0.2 (docker-compose: PostgreSQL+pgvector, Redis) — see
  `STATE.md` §3. Blocker: Docker absent → user must choose Docker Desktop (A) vs local
  PostgreSQL/Redis on Windows (B) before S0.2.

## 2026-08-26 — S0.2 (in progress) infra decision + docker-compose.yml

- **Goal:** S0.2 — docker-compose: PostgreSQL+pgvector, Redis (build bible §19, Phase 0).
- **Decision (user, recorded):** **Option A — Docker Desktop** (standard build-bible path:
  pgvector prebuilt in image, portable compose artifact). User installing; WSL2 not yet
  installed (Docker Desktop installer offers it, or `wsl --install`).
- **Environment probe (new facts):** **PostgreSQL 16 installed + RUNNING** natively
  (service `postgresql-x64-16`, `C:\Program Files\PostgreSQL\16`, port 5432,
  scram-sha-256 auth, **no pgvector**) → port-conflict risk at bring-up; Redis absent;
  Docker absent; WSL absent; no MSVC (pgvector cannot be built locally → another reason
  the Docker path is correct for this machine).
- **Did:**
  - Wrote `docker-compose.yml` (repo root, build bible tree L120): service `db` =
    `pgvector/pgvector:pg16` (qa/qa @ qa_copilot, named volume, pg_isready healthcheck,
    `CREATE EXTENSION vector` deferred to S0.5 per comment) + service `redis` = `redis:7`
    (named volume, redis-cli ping healthcheck); ports env-overridable
    (`${POSTGRES_PORT:-5432}`, `${REDIS_PORT:-6379}`); header comment documents the
    native-PG16 port conflict and the S0.2 exit-criterion commands.
  - `.env.example` verified: `DATABASE_URL=postgresql+psycopg://qa:qa@localhost:5432/qa_copilot`
    and `REDIS_URL=redis://localhost:6379/0` match compose defaults ✓ (S0.2 work item).
  - Updated `STATE.md` (position, decisions, §3 next-step checklist, §4 env facts, §7).
- **Verified:** `docker-compose.yml` parses as valid YAML (pyyaml) · `ruff check` +
  `ruff format --check` · `mypy strict` · `pytest -q` all green (scaffold untouched).
- **Pending (next session):** `docker --version` OK → resolve 5432 conflict (stop
  `postgresql-x64-16` or `POSTGRES_PORT=5433` in `.env` + update DATABASE_URL) →
  `docker compose up -d` → **exit criterion:** `docker compose exec db psql -U qa -d
  qa_copilot -c 'SELECT 1'` → `1` and `docker compose exec redis redis-cli ping` → `PONG`
  → commit `step S0.2: compose infra up (pgvector/pg16 + redis7)` → start **S0.3**
  (FastAPI skeleton, `GET /health` → 200).

## 2026-08-26 — S0.2 (cont.) Docker Desktop installed; engine blocker diagnosed (BIOS)

- **Goal:** finish S0.2 — user installed Docker Desktop; bring up compose infra.
- **Did / found:**
  - Docker Desktop installed **per-user** (`C:\Users\manve\AppData\Local\Programs\DockerDesktop`)
    — CLI v29.7.2 + Compose v5.4.0 work; they're on the **USER PATH**, so pre-existing
    terminal sessions don't resolve `docker` (refresh `$env:Path` from Machine+User, or new shell).
  - Docker Desktop app IS running (processes `Docker Desktop`, `com.docker.backend`),
    but the Linux engine returns **500** on every API call
    (`http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/...`) → engine not up.
  - **Diagnosed root cause:** `Win32_Processor.VirtualizationFirmwareEnabled = **False**`
    → hardware virtualization (Intel VT-x) is **disabled in UEFI**. WSL2 and Hyper-V
    backends both need it, so the engine cannot start until the user enables it in BIOS.
  - Hardware: Intel Core Ultra 9 275HX · ASUS ROG Strix SCAR 18 (G835LX) → BIOS entry key F2.
  - WSL2 kernel still not installed; Docker Desktop first-start may offer to set it up
    (or `wsl --install` as admin + reboot).
- **Verified:** CLI/Compose versions · engine 500 reproducible across `version`/`info`/`ps` ·
  virtualization flag False via CIM.
- **Blocked on (user action):** shut down → power on → **F2** → Advanced → CPU Configuration →
  **Intel Virtualization Technology = Enabled** (VT-d optional) → F10 save → boot → start
  Docker Desktop → engine up. Then resume at STATE.md §3 steps 3–6 (5432 conflict decision,
  `docker compose up -d`, exit criterion, commit, S0.3).

## 2026-08-26 — S0.2 (completed) — compose infra up, exit criterion verified

- **Goal:** finish S0.2 — bring up compose infra + verify exit criterion (unblocked from BIOS).
- **Did:**
  - User enabled **Intel VT-x in UEFI** → Docker Desktop engine up (29.7.2).
  - **User decision:** keep native PG16 on 5432 → `.env` (gitignored) sets `POSTGRES_PORT=5433`
    + `DATABASE_URL=postgresql+psycopg://qa:qa@localhost:5433/qa_copilot`; `.env.example` documents
    the override.
  - `docker compose up -d` → both containers **healthy**: `qa-copilot-db`
    (pgvector/pgvector:pg16, 0.0.0.0:5433→5432, qa/qa @ qa_copilot) + `qa-copilot-redis` (redis:7, :6379).
- **Verified (exit criterion):** `psql SELECT 1` → `1` · `redis-cli ping` → `PONG` ·
  pgvector **0.8.6** available (extension enabled at S0.5) · native PG16 still up on 5432.
- **Commit:** `4446f1a step S0.2: compose infra up (pgvector/pg16 + redis7), exit criterion verified`.

## 2026-08-26 — S0.3 — FastAPI skeleton

- **Goal:** S0.3 — FastAPI skeleton: app factory, pydantic-settings, JSON structured logging,
  `GET /health` → 200 (build bible §19).
- **Did:**
  - `apps/api/src/qa_copilot_api/main.py` — `create_app(settings)` factory + module-level `app`;
    `GET /health` → `HealthResponse` (`schemas.py`).
  - `config.py` — `Settings` (pydantic-settings): `LLM_BASE_URL`, `LLM_MODEL`, `DATABASE_URL`,
    `REDIS_URL`, `APP_UNDER_TEST`; `QA_COPILOT_ENV`/`QA_COPILOT_LOG_LEVEL` with `ENV`/`LOG_LEVEL`
    fallbacks; reads `.env`.
  - `logging_config.py` — stdlib-only **JSON structured logging** (bible §31.5): `JsonFormatter`,
    `configure_logging`; uvicorn loggers routed through it.
  - deps added to `apps/api`: fastapi 0.141.1 · pydantic-settings 2.15 · uvicorn 0.52.4 ·
    httpx 0.28.1 · pydantic 2.13.4; `py.typed` added.
  - mypy config → `files` + `mypy_path` pattern (root pyproject) to avoid mypy 2.x
    "source file found twice" collisions from passing bare `apps`/`packages`.
  - tests: `tests/unit/test_health.py` (in-process via httpx ASGITransport — no port binding),
    `tests/unit/test_logging.py`.
- **Verified:** `GET /health` → **200 live** (uvicorn + curl) + JSON server logs confirmed ·
  `uv run pytest -q` → **14 passed** · `ruff check` ✓ · `ruff format --check` ✓ · `mypy strict` ✓.
- **Commit:** `cbd623d step S0.3: FastAPI skeleton (app factory, pydantic-settings, JSON logging, GET /health)`.
- **Note:** that session ended with the STATE.md update uncommitted and no SESSION_LOG entry —
  backfilled here during the S0.4 session (memory now committed per protocol).

## 2026-08-26 — S0.4 — domain package (pydantic entities + enums)

- **Goal:** S0.4 — domain package: pydantic entities + enums (project, requirement, test_case,
  failure, artifact, job). Exit criterion: schema unit tests green.
- **Did:**
  - `packages/domain/src/qa_copilot_domain/`:
    - `enums.py` — 7 `StrEnum`s with snake_case wire values: `TestType`
      (functional/negative/boundary/risk/accessibility/security), `Priority`, `RiskLevel`,
      `JobType` (7 §11 AI endpoints), `JobStatus`, `FailureCategory` (§16 taxonomy),
      `ArtifactType` (§15 kinds).
    - `base.py` — `DomainModel`: `extra="forbid"` (strict AI outputs) + `from_attributes=True`
      (S0.5 ORM mapping).
    - `entities.py` — `Project`, `Requirement`, `TestCase` (`requirement_refs` mirrors the §10
      M:N join), `Failure` (defaults unknown / `confidence=None` / `needs_human_approval=True`),
      `Artifact`, `Job` (defaults pending / progress 0.0). `NonBlankStr` =
      `Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]`; `confidence`/
      `progress` bounded 0.0–1.0.
    - `__init__.py` — public re-exports + `__all__`.
  - `packages/domain/pyproject.toml` — `pydantic>=2.9` added to deps.
  - `tests/unit/test_domain.py` — 17 schema tests: enum coverage vs bible §3/§16, §12 example
    payloads (test case + failure analysis), JSON round-trip, bounds, `extra="forbid"`,
    non-blank text, wire-string enum serialization.
  - `TestCase`/`TestType` carry `__test__ = False` (stops pytest collection warnings).
- **Verified (exit criterion):** `uv run pytest -q` → **31 passed** (14 prior + 17 new) ·
  `ruff check` ✓ · `ruff format --check` ✓ (23 files) · `mypy strict` ✓ (18 source files).
- **Commit:** `f2fccea step S0.4: domain package (pydantic entities + enums), schema tests green`.
- **Learnings (recorded in STATE.md §7):**
  - `strip_whitespace` as a `Field(...)` kwarg is pydantic v1 / **deprecated in v2** (mypy strict
    rejects it) — v2-correct: `Annotated[str, StringConstraints(...)]`.
  - Negative/wire-format test cases go through `model_validate` — typed constructors are
    arg-checked by mypy strict (`status="completed"` → arg-type error).
  - ruff isort treats `qa_copilot_*` as third-party (src-layout workspace) — import block: no
    blank line between `pydantic` and `qa_copilot_*`.
  - Naming: `Project.repository_id` (bible §10 says `repo_id`) — kept the clearer name.
- **Next session start:** S0.5 (SQLAlchemy + Alembic: §10 core tables + `jobs` +
  `requirement_test_cases` + `prompt_versions`) — see `STATE.md` §3.

## 2026-08-26 — S0.5 — SQLAlchemy + Alembic + idempotent seed

- **Goal:** S0.5 — define the SQLAlchemy core data model, apply Alembic migrations,
  verify with an idempotent seed. Exit criterion: `alembic upgrade head` on the compose
  DB; seed script runs.
- **Did:**
  - `packages/repository/src/qa_copilot_repository/`:
    - `models.py` — all 18 §10 core tables + `prompt_versions` as SQLAlchemy 2.0 typed
      ORM (`Mapped`/`mapped_column`): organizations, users, repositories, projects, files,
      requirements, test_cases, requirement_test_cases (M:N), test_runs, test_results,
      failures, artifacts, knowledge_documents, embeddings (`vector` column via
      `pgvector.sqlalchemy.VECTOR(VECTOR_DIM)`), ai_sessions, ai_actions, jobs.
    - `db.py` — `get_database_url()` (env `DATABASE_URL` → `.env` via `python-dotenv` →
      safe default), `make_engine()` (psycopg 3.2, `pool_pre_ping`), `session_scope()`.
  - `infra/migrations/` — `alembic init` (root `alembic.ini`), `env.py` rewired to
    `qa_copilot_repository.db.get_database_url()` + `models.Base.metadata`; autogenerate
    initial revision `60fa1027d8d2_initial_core_schema` (pgvector import added manually —
    generated migrations reference `pgvector.sqlalchemy` by attribute only).
  - `scripts/seed.py` — idempotent dev fixtures: 1 org/user/repo/project, 2 requirements,
    2 test cases (linked), 1 knowledge doc + 1 placeholder embedding (zero vector),
    1 prompt version (`requirement-analyst@1`), 1 job (`requirement_analysis`, pending),
    1 run (1 passed / 1 failed with diagnosis + artifact).
- **Verified (exit criterion):**
  - `alembic upgrade head` on compose DB (5433) ✓ · `alembic current` → `60fa1027d8d2 (head)`.
  - `vector` extension enabled; `embeddings.vector` is a real pgvector column (ops work).
  - Seed run **twice** — second run created no duplicates (natural-key lookups +
    deterministic `uuid5` ids).
  - `tests/unit/test_repository.py` — 10 tests: URL resolution (env wins / default
    fallback / engine URL), 18-table registration, mapper `configure()` (relationship
    wiring), `metadata` column / `metadata_` attribute, plain-VARCHAR enum columns,
    pgvector column type, **2 live DB smoke tests** (seed rows + `vector_dims = 1536`).
  - `uv run pytest -q` → **41 passed** · `ruff check` ✓ · `ruff format --check` ✓ ·
    `mypy strict` ✓ (21 files).
- **Decisions / gotchas (recorded in STATE.md §5, §7):**
  - SQLAlchemy `metadata` is reserved on DeclarativeBase → JSONB metadata columns use the
    `metadata_` Python attribute (DB column name stays `metadata`).
  - Domain enums stored as plain `VARCHAR(32)` wire strings — `qa_copilot_domain` stays
    the single source of truth (no DB-side vocabulary duplication).
  - Alembic URL resolution centralized in `qa_copilot_repository.db`, never hardcoded in
    `alembic.ini` (bible §7).
  - Migration enables pgvector but **does not drop it on downgrade** (extension is
    infrastructure, not schema state).
  - ruff B023: factory lambdas in loops must bind loop vars
    (`lambda title=title, i=i: ...`) — hit in the seed script.
  - mypy strict: `_enum_column(enum_cls: type[StrEnum]) -> sa.Enum` (not `type[str]` /
    `sa.Enum[Any]`).
- **Open item:** `VECTOR_DIM = 1536` is provisional until the embedding model is chosen
  (S0.6 / Phase 5); changing it requires a new migration.
- **Next session start:** S0.6 (AI gateway: local llama server, streaming, token
  accounting → `ai_actions`, redaction hook, prompt-registry loader) — see `STATE.md` §3.

## 2026-08-26 — S0.6 — AI gateway (LM Studio / Qwen3.8-27B)

- **Goal:** S0.6 — OpenAI-compatible LLM gateway (streaming, token accounting →
  `ai_actions`), secret redaction, DB-backed prompt registry. Exit criterion: unit tests
  green with a fake server; one live call logs `tokens_in/out`.
- **Runtime discovery (user-confirmed):** LM Studio (llama.cpp) at
  `http://localhost:8080/v1` serving `Qwen3.8-27B-Q4_K_M.gguf` (27.3B params, n_ctx
  100,096, completion-only — no local embedding model, so `VECTOR_DIM = 1536` stays
  provisional). Model id is the full file path — verified against `GET /models`.
- **Did:**
  - `packages/ai/src/qa_copilot_ai/` (new package, deps `httpx` + `pydantic>=2.9`):
    - `gateway.py` — async `LLMGateway` over `/chat/completions`: `chat()` (JSON) and
      `chat_stream()` (NDJSON/SSE `data:` lines, `[DONE]` sentinel), `usage` from the
      server (`stream_options.include_usage`) with a char-count estimate fallback,
      120 s per-call timeout, one retry on transport errors only (timeouts/HTTP errors
      → hard `LLMError` with status — no silent model-swap), per-call structured
      `ai_call` log record (`agent, model, tokens_in, tokens_out, usage_source,
      latency_ms, redactions, retries, input_hash, stream`) — the exact payload an
      `ai_actions` row stores.
    - `redaction.py` — bearer/GitHub/OpenAI/AWS/JWT tokens, DSN passwords,
      `api_key`/`password` key-value pairs → `***REDACTED***` (idempotent); applied
      before the wire and to logged content (bible §31.7, leaks=0).
    - `prompts.py` — `PromptSpec`, `PromptStore` protocol, `InMemoryPromptStore`,
      `render_prompt(**variables)`: strict `{{var}}` substitution, missing variable
      raises `PromptRenderError`.
  - `packages/repository` (now depends on `qa_copilot_ai`):
    - `prompts.py` — `load_prompt(session, name, version)` from `prompt_versions`
      (bible §31.6 registry) → `PromptSpec`; `PromptVersionNotFound`.
    - `audit.py` — `record_ai_action(session, ...)` → `ai_actions`;
      `record_ai_call(session, *, session_id, call: AICallResult)` convenience wrapper.
  - `scripts/llm_live_check.py` — live exit-criterion check: non-streaming + streaming
    call, prints `ai_call` payload + model reply.
  - `.env`/`.env.example` — `LLM_BASE_URL=http://localhost:8080/v1`, `LLM_MODEL` =
    exact model id; build bible §31.1 note updated.
  - Tests: `tests/unit/test_ai_gateway.py` (14 tests: redaction, prompt store/rendering,
    non-streaming, streaming assembly, estimated usage, retry, timeout/4xx errors) +
    3 new DB-backed tests in `test_repository.py` (prompt load, 404, `record_ai_call`
    → `ai_actions` row).
- **Verified (exit criterion):**
  - `uv run pytest -q` → **60 passed** (incl. live-DB tests).
  - `ruff check` ✓ · `ruff format --check` ✓ · `mypy` strict ✓ (27 source files).
  - **Live check passed:** `scripts/llm_live_check.py` → 2× `ai_call` records,
    `usage_source: "reported"` (LM Studio reports usage on both paths), tokens_in=80,
    tokens_out=104/93, latencies 8.9 s / 2.8 s; model replied
    "QA copilot S0.6 live check OK." on both paths.
- **Decisions / gotchas:**
  - `httpx.MockTransport` is sync-only → tests for `AsyncClient` need a custom
    `httpx.AsyncBaseTransport` + an async byte stream for SSE-style bodies.
  - Gateway `transport` param typed `httpx.AsyncBaseTransport` (mypy).
  - mypy: closures returning chunk/usage fields need concrete return types + local
    binding (`final = chunks[-1]; assert final.usage is not None`).
  - LM Studio reports `usage` on streaming too (last chunk) — estimate path is a
    fallback, not the norm.
- **Next session start:** S0.7 React shell — **blocked on Node LTS install** (see
  `STATE.md` §3/§7).

## 2026-08-27 — S0.7 — React shell (Node LTS unblocked 2026-08-26)

- **Goal:** S0.7 — React shell (Vite): layout + pipeline view + SSE client (mocked).
  Exit criterion: shell renders; mocked SSE updates a progress bar. Also closes the
  S0.1 web half (pnpm workspace + ESLint + Prettier + `pnpm lint`).
- **Did:**
  - Root: `pnpm-workspace.yaml` (`packages: [apps/*]` + `onlyBuiltDependencies:
    [esbuild]`) and a private root `package.json` (one-command `dev`/`build`/
    `preview`/`lint`/`format` via `pnpm --filter qa-copilot-web`; `packageManager:
    pnpm@11.24.0`; node ≥ 22.12) — bible §29 "one-command up: `pnpm dev`".
  - `apps/web`: React 18.3 + Vite 6.4 + TypeScript 5.8 (strict; `tsconfig.json`
    for `src`, `tsconfig.node.json` for `vite.config.ts`; build = `tsc --noEmit` ×2
    + `vite build`) + Tailwind CSS 4 (configless, `@import 'tailwindcss'` +
    `@tailwindcss/vite`).
  - ESLint 9 flat config (`eslint.config.js`): JS recommended + typescript-eslint
    recommended + react-hooks + react-refresh + eslint-config-prettier; Prettier 3
    (`.prettierrc.json`: printWidth 100 to match ruff, single quotes) +
    `.prettierignore` (node_modules, dist).
  - Shell components: `Header` (branding + SSE connection badge), `PipelineView`
    (six §4 stages — requirement → test design → automation → execution → failure
    analysis → fix — per-stage progress bars, `role=progressbar`,
    `aria-current=step`), `EventLog` (live event feed, `aria-live=polite`),
    `useJobEvents` hook (native `EventSource`, strict `StageId` validation,
    reducer over the SSE stream, capped event log, replay button),
    `src/lib/pipeline.ts` (single source for the stage contract).
  - Mock SSE: dev-only Vite middleware plugin (`qa-copilot:mock-sse`,
    `apply: 'serve'`) at `GET /mock/events` — standard SSE framing
    (`event:`/`data:` JSON): `job.started` → per stage `stage.started`,
    `progress` ×4 (0.25→1.0), `stage.completed` → `job.completed`; client
    disconnect handled. Same shape the S0.9 jobs API will serve (`GET /events`),
    so the browser client is contract-compatible.
  - `vite.config.ts`: `/api` dev proxy → FastAPI :8000 (S0.9+).
  - Docs: `apps/web/README.md` rewritten (scripts/layout); root README quickstart
    gains step 5 (web).
- **Verified (exit criterion):**
  - `pnpm install` ✓ (192 packages; esbuild postinstall approved via
    `onlyBuiltDependencies` — pnpm 11 blocks build scripts by default).
  - `pnpm format` + `pnpm format:check` ✓ (17 files) · `pnpm lint` ✓ (exit 0, no
    findings).
  - `pnpm build` ✓ — tsc strict on both projects + Vite production build
    (32 modules; dist ≈ 164 kB raw / ≈ 52 kB gzip).
  - `pnpm dev` live: `curl http://localhost:5173/` → shell HTML (`#root`,
    react-refresh, `/@vite/client`, `<title>AI QA Copilot</title>`);
    `curl http://localhost:5173/mock/events` → full SSE timeline
    (`job.started` → `requirement` 25/50/75/100% → `stage.completed` →
    `test_design` …). Progress-bar animation is contract-level verified
    (mock frames ↔ hook handlers match exactly); visual check needs a browser.
- **Decisions / gotchas (recorded in STATE.md §7):**
  - pnpm 11: the `pnpm` field in `package.json` is IGNORED — settings like
    `onlyBuiltDependencies` belong in `pnpm-workspace.yaml`.
  - Vite dev server binds `[::1]:5173` — `curl http://127.0.0.1:5173` → connection
    refused; use `http://localhost:5173`.
  - SSE client uses native `EventSource` (no custom parser) — matches an
    `EventSourceResponse`-style S0.9 contract; cookie auth (S0.8) works with it,
    but header-based JWT would need a fetch-based reader (flagged for S0.9).
- **Commit:** `4d8840b step S0.7: React shell (pnpm workspace, React18+Vite+TS+
  Tailwind, mocked SSE pipeline view)`.
- **Next session start:** S0.8 (auth baseline: dev user, JWT middleware, project
  roles owner/member/viewer; exit: 401/200/403 matrix tested) — see `STATE.md` §3.

## 2026-08-27 — S0.8 auth baseline (JWT login + project-scoped RBAC)

- **Goal:** S0.8 — dev-mode login (email+password → JWT) and project-scoped RBAC
  (`owner` / `member` / `viewer` per project, §31.3); exit: 401/200/403 matrix tested.
- **Did:**
  - `apps/api/src/qa_copilot_api/auth.py`: PBKDF2-SHA256 password hashing/verify
    (stdlib `hashlib`, `pbkdf2_sha256$<iter>$<salt>$<digest>`), HS256 JWT
    create/decode (PyJWT, `sub`/`email`/`iat`/`exp`, `TOKEN_TTL`), Bearer parsing,
    `get_current_user()` dependency (401 on missing/invalid/expired/wrong-secret),
    `require_role(minimum)` factory — looks up `project_members` FIRST, so
    non-members get 403 without leaking project existence; role ladder
    viewer < member < owner.
  - `routes.py`: `POST /api/v1/auth/login` (dummy hash-verify for unknown users →
    constant-ish timing), `GET /api/v1/auth/me` (user + project roles),
    `GET /api/v1/projects` (memberships), `GET /projects/{id}` (viewer+),
    `DELETE /projects/{id}` (owner). Missing `AUTH_TOKEN_SECRET` → 500 with readable
    detail (`_require_secret` raises; login catches → `HTTPException(500)`).
  - `config.py`: `auth_token_secret` (env `AUTH_TOKEN_SECRET`, no default — fail loud);
    `.env` gets generated machine-local secret + `AUTH_DEV_PASSWORD`; `.env.example`
    documents both.
  - Schema: migration `2d783f832c48` adds `project_members` (composite PK
    `(project_id, user_id)` + `user_id` index, role = plain VARCHAR of the domain wire
    string per S0.5 convention, CASCADE FKs, `created_at`); `users.password_hash`
    added in the same migration (nullable — pre-auth rows kept).
    `packages/repository/.../membership.py`: `get_user_by_email` /
    `get_project_role` helpers (single DB entry point for API + seed).
  - `scripts/seed.py` (idempotent): sets `dev@local.dev` `password_hash` from
    `AUTH_DEV_PASSWORD` when missing; links dev user as **owner** of the seeded
    project (natural-key lookup, no duplicate rows).
  - `tests/unit/test_auth.py` (21 tests, function-scoped scratch DB
    `qa_copilot_auth_test` on 5433, migrated per test via Alembic): password
    roundtrip + reject; JWT create/decode/expiry/tamper/wrong-secret; login
    ok/bad-password/unknown-user/missing-hash; `/me` token matrix; projects list;
    RBAC matrix (owner/member/viewer read 200; non-member 403 on real AND ghost
    project; member/viewer/non-member delete 403; owner delete 204 → gone);
    unauthenticated delete 401; missing-secret → 500 mentioning `AUTH_TOKEN_SECRET`.
- **Verified (exit criterion):** `uv run pytest tests/unit -q` → **82 passed** (21 auth
  + 61 prior) · `ruff check` + `ruff format --check` clean · `alembic current` →
  `2d783f832c48 (head)` · seed ×2 idempotent · live uvicorn smoke (Python/urllib):
  login 200 + token + owner project · `/me` 200 · projects list · project detail ·
  401 (no token / bad password / garbage token) — **ALL PASS**.
- **Decisions / gotchas (recorded in STATE.md §5/§7):**
  - Authorization is project-scoped (`project_members.role`); `users.role` is a
    default only. Non-member → 403 (not 404) even for deleted/unknown projects.
  - `db.delete(project)` with `ondelete="CASCADE"` children breaks the ORM
    (tries to null out the composite PK) → delete `project_members` rows explicitly
    first in the route.
  - Test fixtures: UUID columns need real UUIDs (deterministic `uuid5`); `DROP
    DATABASE` needs `engine.dispose()` on ALL engines (incl. `app.state.engine`)
    first.
  - PowerShell mangles JSON bodies for `curl.exe` through the tool shell — smoke
    tests go through a Python urllib script.
  - S0.9 note: web shell uses native `EventSource` (no `Authorization` header) —
    header-based JWT means S0.9's real SSE endpoint needs a fetch-based reader in
    `apps/web` (flagged S0.7).
- **Commit:** `6df40a6 S0.8: auth baseline (JWT login + project-scoped RBAC)`.
- **Next session start:** S0.9 (jobs API: 202 + SSE) — see `STATE.md` §3.

## 2026-08-27 — S0.9 jobs API (async 202 + SSE)

- **Goal:** S0.9 — async job submission (202) + SSE event stream (build bible §19);
  exit: POST returns 202 with a job id; events stream over `GET /events` in the S0.7
  shell contract shape (`job.started`/`stage.*`/`progress`/`job.completed`).
- **Did:**
  - `apps/api/src/qa_copilot_api/jobs.py` (new): `JobAgent` protocol — the single
    replaceable seam (S1.x implements it against `qa_copilot_ai` without touching the
    API) + `StubAgent` (deterministic, no LLM: six §4 stages with `progress` steps and
    `stage.completed`, ends `job.completed`). Job state machine `queued → running →
    completed|failed` (`InvalidJobTransition` on illegal edges). `start_job()` inserts
    the Job row and emits `job.started` BEFORE the agent runs (no event loss), runs the
    agent in a daemon thread, flips state on completion/failure, emits `job.completed`.
    `reap_orphans()` — startup recovery of stale `running` jobs (PID-scoped prefix).
    `sse_stream(scope)` — job/project/`all` scopes, snapshot-replay from DB + live tail
    via in-process pub/sub (`JobBus`, `threading.Condition`), 15s heartbeat;
    `sse_frame()` matches the S0.7 mock frame shape byte-for-byte.
  - `routes.py`: `POST /api/v1/projects/{project_id}/requirements/analyze` (member+;
    202 `{job_id}`; requirement persisted as `analyzing`, re-analyze idempotent);
    `GET /api/v1/jobs/{job_id}` (viewer+; non-member → 404 — no existence leak);
    `GET /api/v1/events?scope=job|project` (SSE; 422 on bad/missing scope params).
    `main.py`: `create_app` builds `JobBus` + `StubAgent` on `app.state`; lifespan reaps
    orphans on startup and registers shutdown.
  - `schemas.py`: `AnalyzeRequest` / `AnalyzeResponse` (202 payload) / `JobResponse`
    (+`scope`).
  - `tests/unit/test_jobs.py` (15 tests, function-scoped scratch DB
    `qa_copilot_jobs_test` migrated per test via Alembic): 202 contract + persisted
    requirement + idempotent re-analyze · job status transitions + `job.completed`
    event · non-member 404 / unknown 404 / member 200 · job-scoped SSE timeline (all
    four event types in order, incl. `progress`) · project-scoped SSE · invalid-scope
    422 · `reap_orphans` flips stale running → failed, leaves fresh ·
    `InvalidJobTransition` on illegal edge · stub event contract (stage names = §4,
    progress 0.0–1.0, `job.completed` last).
  - Mypy strict cleanup across the configured scope (34 errors → 0, new + S0.8 debt):
    `jobs.py` `CursorResult` cast for `.rowcount` (SQLAlchemy `Result[Any]` base has no
    `rowcount`); `routes.py` `assert project_id is not None` narrowing in the project
    branch; `auth.py` `iterations` → `iterations_str` (str→int reassignment); test
    fixtures/annotations in `test_jobs.py` + `test_auth.py`; `test_repository.py`
    iterate `Table.primary_key` directly; 3× `_env_file=None` carry a commented
    `# type: ignore[call-arg]` — reproduced with a minimal `BaseSettings` subclass:
    pydantic-settings' private init kwarg is invisible to mypy (stub limitation).
- **Verified (exit criterion):** `uv run pytest tests/unit -q` → **97 passed** (15 jobs
  + 82 prior) · `uv run mypy` → **no issues in 34 source files** · `ruff check .` ✓ ·
  `ruff format --check .` ✓. SSE contract verified in-test by driving
  `jobs.sse_stream()` directly (a `TestClient` streaming hang on an intentionally
  open-ended SSE response is a client-side limitation, not a production bug).
- **Decisions / gotchas (recorded in STATE.md §5/§7):**
  - `StubAgent` is a placeholder by design — S1.x replaces it through the same
    `JobAgent` protocol (API surface unchanged).
  - SSE bus is in-process (single-process uvicorn is the current deployment);
    multi-worker deploy will need Redis pub/sub (Redis already up per S0.2).
  - `sse_stream()` typed `AsyncGenerator[str, None]` so `aclose()` typechecks.
  - pydantic-settings + mypy: `_env_file` private init kwarg not in stubs → targeted
    ignores (drop when stubs improve — will flag under `warn_unused_ignores`).
  - Web shell still consumes the mock SSE (`/mock/events`) — pointing `useJobEvents`
    at the real `GET /events` needs a fetch-based reader (EventSource can't set
    `Authorization`); queued as a follow-up.
- **Commit:** `2051749 step S0.9: async jobs API (202 + SSE feed, stub agent, state
  machine, mypy/ruff/pytest green)`.
- **Next session start:** S0.10 (demo app v0, separate repo `ai-qa-copilot-demo-app`)
  — see `STATE.md` §3.

## 2026-08-27 — S0.10 Demo app v0 (separate repo)

- **Goal:** S0.10 — demo app v0 in a separate repo `ai-qa-copilot-demo-app` (build bible §19/§23):
  Express + SQLite + React; `/login /products /cart /checkout`; defect injection via env flags.
  Exit: manual smoke passes; one defect flag changes behavior.
- **Did:**
  - New repo `c:\Users\manve\Workspace\ai-qa-copilot-demo-app` (git init, pnpm workspace:
    `server` + `client`).
  - `server/`: Express 4 + `better-sqlite3` (prebuilds; `node:sqlite` rejected — experimental on
    Node 22). `createApp({db, defects})` factory; routes: auth (`POST /api/login`, demo user
    `qa`/`qa1234`, Bearer token in SQLite `sessions`), products (4 seeded), cart (per-session),
    orders (`POST /api/checkout` → 201, `GET /api/orders[/:id]`); `GET /api/config` exposes active
    defect flags; `GET /health`; serves `client/dist` + SPA fallback in prod mode.
  - `client/`: React 18 + Vite 6 + react-router 6; pages Login/Products/Cart/Checkout; `data-testid`
    vocabulary in pure `src/testids.js` (BASE vs DRIFTED maps); drift applied at runtime from
    `GET /api/config` (one server env flag changes the rendered ids).
  - Defect flags (`server/src/defects.js`, 1:1 with §16 taxonomy): `DEFECT_API_500` (checkout → 500,
    product defect) · `DEFECT_BAD_DATA` (order `items: []`, test-data defect) · `DEFECT_FLAKY`
    (300ms–3s delay on `/api/*`, flaky behavior) · `DEFECT_LOCATOR_DRIFT` (renamed/removed test ids,
    automation defect).
  - `scripts/smoke.mjs` (11-check happy path) + `scripts/defect_check.mjs` (spawns the server per
    flag, in-memory DB, verifies all 4 flags). `Dockerfile` for the S3.1 compose service
    (not build-verified yet).
- **Verified (exit criteria):** `pnpm install` ✓ (esbuild + better-sqlite3 builds approved via
  `allowBuilds`) · `pnpm build` ✓ (37 modules) · **smoke: 11/11 PASS** · **defect-check: 7/7 PASS**
  (500 on checkout; `items: []` on checkout + order detail; 1206ms flaky delay; drift flag + id
  renames/removals) · prod single-process: `/` + `/checkout` SPA → 200.
- **Commit:** `43739a5 step S0.10: demo app v0 (Express + SQLite + React, /login /products /cart
  /checkout, defect-injection env flags)` (separate repo).
- **Decisions / gotchas:** `better-sqlite3` over `node:sqlite` (experimental warning + stderr
  noise) · `DEFECT_LOCATOR_DRIFT` applied client-side at runtime via `/api/config` (one server flag
  changes the UI) · server :4000, client dev :5174 (5173 = copilot web shell) · PowerShell treats
  child-process stderr as an error (NativeCommandError) — read the actual output.

## 2026-08-27 — S1.1 Requirement Agent (prompt v1 + schema-validated analysis)

- **Goal:** S1.1 — Requirement Agent (build bible §19 Phase 1): prompt v1 in the prompt
  registry (§31.6) + schema-validated output through the S0.6 gateway; runs inside the
  S0.9 job pipeline (replace `StubAgent` at the `JobAgent` seam). Exit: 10 fixture
  requirements → 10/10 schema-valid.
- **Did:**
  - `packages/ai/src/qa_copilot_ai/prompts.py` (extended): `PromptSpec` + `PromptStore`
    protocol, `InMemoryPromptStore`, `FilePromptStore` (front-matter
    `name/version/description` + `input_variables` + `output_contract`);
    `render_prompt` fails loud on missing variables.
  - `packages/ai/src/qa_copilot_ai/agents/requirement.py` (new): `RequirementInput` /
    `RequirementAnalysis` (strict pydantic; `suggested_test_types` validated against
    `SUGGESTED_TEST_TYPES` = the domain `TestType` taxonomy); `RequirementAgent.analyze()`
    — renders the registered prompt, calls the gateway, tolerates markdown fences,
    `model_validate_json` → loud `ValueError` on schema violations; `LLMCall.audit_dict()`
    for the audit trail.
  - `packages/ai/prompts/requirement-analyst.v1.md` (new): v1 requirement-analyst prompt
    (input variables: title/content/criteria; JSON output contract).
  - `apps/api/src/qa_copilot_api/jobs.py`: `RequirementJobAgent` implements the S0.9
    `JobAgent` protocol — same six §4 stages, but `requirement_analysis` runs the real
    agent, persists the analysis as a JSON artifact, and records the `ai_actions` audit
    row against the job's `ai_sessions` anchor. `StubAgent` unchanged — still the
    fallback when no LLM is configured.
  - `main.py`: `_build_jobs_agent()` — `RequirementJobAgent` when `llm_base_url` +
    `llm_model` are set, `StubAgent` otherwise (logs which one is wired).
  - `tests/unit/test_requirement_agent.py` (new, 6 tests): **exit criterion — 10 fixture
    requirements → 10/10 schema-valid** (fake gateway transport; prompt pulled from the
    registry; invalid JSON rejected; schema violation rejected; unknown test type
    rejected; markdown fence tolerated).
  - `tests/unit/test_jobs.py`: fixture now pins `llm_base_url=None, llm_model=None` —
    these tests assert the S0.9 stub contract and must stay hermetic against a real LLM
    in the environment.
- **Verified (exit criterion):** `uv run pytest -q` → **103 passed** (6 new) ·
  `uv run mypy` → **no issues in 37 source files** · `ruff check .` ✓ ·
  `ruff format --check .` ✓.
- **Decisions / gotchas (also in STATE.md §7):**
  - **Env leak — root cause of the 8 job-test failures (prior session):**
    `qa_copilot_repository.db.get_database_url()` → `_load_dotenv()` writes repo `.env`
    values (incl. `LLM_BASE_URL`/`LLM_MODEL`) into `os.environ`; the test fixture ran
    Alembic first, and pydantic-settings reads env vars even with `_env_file=None` →
    `create_app` wired `RequirementJobAgent` → real LM Studio call → jobs hung past the
    10s test window. Fix: pass `llm_base_url=None, llm_model=None` explicitly in the
    test `Settings` (init kwargs beat env vars — verified empirically).
  - mypy strict: `audit_dict()` returns `dict[str, object]` → `int(audit["x"])` fails
    `call-overload`; use `cast(int, ...)` (jobs.py `_record_action`).
  - ruff isort: `qa_copilot_ai` is NOT first-party (src-layout workspace) — it sorts in
    the third-party block (main.py, test_requirement_agent.py).
- **Commit:** `6a1bf88 step S1.1: requirement agent (prompt registry v1 +
  schema-validated analysis, wired into the S0.9 job pipeline; mypy/ruff/pytest green)`.
- **Next session start:** S1.2 (Test Design Agent — functional/negative/boundary/risk/
  a11y/security; exit: step coverage ≥ 85% vs oracle on 10 requirements) — see
  `STATE.md` §3.

## 2026-08-27 — S1.2 Test Design Agent

- **Goal:** Test Design Agent (build bible §19 Phase 1): generate functional/negative/
  boundary/risk/a11y/security test cases from a requirement (plus the optional S1.1
  `RequirementAnalysis`) through the S0.6 gateway; run inside the S0.9 job pipeline
  behind a new endpoint. Exit: step coverage ≥ 85% vs oracle on 10 requirements.
- **Did:**
  - `packages/ai/src/qa_copilot_ai/agents/test_design.py` (new): `TestCase` /
    `TestSuite` — the §12 schema in strict pydantic (`TC-###` ids, unique; fixed
    type/priority/risk vocabularies; non-empty steps and expected results;
    `min_length=1` test cases); `TestDesignInput` (requirement + optional analysis);
    `TestDesignAgent.generate()` — loads `test-designer@1` from the registry,
    renders with title/content/acceptance_criteria/analysis, calls the gateway,
    tolerates markdown fences, `model_validate_json` → loud `ValueError` on schema
    violations (same contract as the S1.1 agent; no re-prompt loop — §31.6).
  - `packages/ai/prompts/test-designer.v1.md` (new): v1 test-designer prompt
    (input variables: title/content/acceptance_criteria/analysis; JSON output
    contract mirroring §12).
  - `apps/api/src/qa_copilot_api/jobs.py`: `TestDesignJobAgent` implements the S0.9
    `JobAgent` protocol (`stages = ("test_design",)`); `run()` builds the
    `Requirement` from the job input, runs the real agent, returns the suite JSON as
    `output_ref`, and records the `ai_actions` audit row against the job's
    `ai_sessions` anchor (same pattern as `RequirementJobAgent`).
  - `main.py`: `_build_test_design_jobs_agent()` — `TestDesignJobAgent` when
    `llm_base_url` + `llm_model` are set, `StubAgent` otherwise (logs which one is
    wired); exposed as `app.state.jobs_test_design_agent`.
  - `routes.py` + `schemas.py`: `POST /api/v1/requirements/test-cases` → 202 +
    `{job_id}` with `Location` — same 202/SSE contract as S0.9; RBAC member+;
    ghost project → 403; `TestDesignRequest` body (title/content/acceptance_criteria).
    The route only enqueues a `JobType.TEST_CASE_GENERATION` job — no LLM inline.
  - `tests/unit/test_test_design_agent.py` (new, 20 tests): **exit criterion — 10
    fixture requirements → schema-valid suites + per-requirement step coverage ≥ 85%
    vs the hand-authored oracle** (fake OpenAI-compatible httpx transport; prompt
    pulled from the registry; optional-analysis passthrough; invalid JSON / missing
    `test_cases` / empty steps / unknown type / duplicate ids rejected; markdown
    fences tolerated).
  - `tests/unit/test_jobs.py`: +5 tests — 202+Location+`completed` with
    `stub-output/test_case_generation`, auth 401 / non-member 403 / ghost-project 403,
    422 validation, `ai_sessions` row with `task_type=test_case_generation`.
  - `packages/ai/src/qa_copilot_ai/__init__.py`: export `TestDesignAgent`,
    `TestDesignInput`, `TestCase`, `TestSuite` (kept alphabetical for ruff I001).
- **Verified (exit criterion):** `uv run pytest -q` → **127 passed** (25 new) ·
  `uv run mypy` → **no issues in 39 source files** · `ruff check .` ✓ ·
  `ruff format --check .` ✓.
- **Decisions / gotchas (also in STATE.md §7):**
  - **Oracle vs output:** the "Order history" oracle carries 8 ground-truth steps
    (auth, sorting, pagination, status after shipping, CSV export, …) — a competent
    designer covers them, so the fake model output was extended (TC-004/TC-005)
    rather than trimming the oracle. The oracle stays the independent reference.
  - **pytest collection:** `Test*`-named non-test classes need `__test__ = False`
    (otherwise PytestCollectionWarning).
  - **ruff B905:** `zip(FIXTURES, suites, strict=True)` in the coverage gate test.
- **Commit:** `bb5bb2f step S1.2: test design agent (schema-validated test suites,
  >=85% oracle step coverage on 10 fixtures, POST /api/v1/requirements/test-cases
  job; mypy/ruff/pytest green)`.
- **Next session start:** S1.3 (UI flow: requirement → structured test cases,
  persisted; exit: manual E2E through the UI) — see `STATE.md` §3.

## 2026-08-27 — S1.3 Persisted UI flow (persistence + web shell on the real API)

- **Goal:** finish S1.3 (build bible §19 Phase 1): the web shell drives requirement →
  test-cases against the **real** API (202 + SSE) and renders the suite from the
  **persisted** rows. Exit: manual E2E through the UI.
- **Did (persistence half, previous session — commit `022fb6b`):**
  - `qa_copilot_repository.requirements.persist_requirement_with_suite(...)`: one
    `requirements` row + N `test_cases` rows + §10 M:N `requirement_test_cases` join;
    AI strings → domain enums.
  - `TestDesignJobAgent.run()` returns the persisted requirement id as `output_ref`
    (suite JSON kept as the `ai_actions` audit payload).
- **Did (UI-flow half, this session — commit `8c0ed5b`):**
  - **Backend:** `GET /api/v1/requirements/{requirement_id}` (auth + project role
    check; non-member → 403, no existence leak) with `RequirementOut`/`TestCaseOut`
    schemas; +6 unit tests (success, unauthenticated, viewer, non-member, unknown
    UUID, malformed id).
  - **Web client (`apps/web`):** `src/lib/api.ts` (Bearer-token fetch client:
    `login`/`me`/`createTestCaseJob`/`getRequirement` + `streamJobEvents` — SSE via
    `fetch` + streaming reader because `EventSource` cannot set `Authorization`);
    `src/hooks/useAuth.ts` (token boot via `/me`, login/logout, strongest project);
    `src/hooks/useJobEvents.ts` — `start(jobId)` streams real
    `GET /api/v1/events?job_id=…`, tracks outcome/output_ref/error/stages/log;
    `LoginForm`/`RequirementForm`/`TestCaseList` (new) + `App.tsx` gates
    (booting / unauthenticated / authenticated-without-project / main flow);
    `Header` shows user/project/live-SSE status; mock-SSE plugin removed from
    `vite.config.ts` (`/api` proxy to :8000 kept).
  - **Prompt fix (E2E-driven):** `packages/ai/prompts/test-designer.v1.md` — Qwen-27B
    (LM Studio) truncated its JSON at the 4000-token `output_budget` on 2 live runs
    (schema `EOF` failure). Now: "at most six test cases", ≤2 preconditions,
    ≤3 expected results, one short sentence per step. Fits the budget with margin.
  - **E2E harness:** `scripts/e2e_s13.py` — API-level walk of the whole chain
    (login → 202 → SSE until terminal → read-back → job-row consistency).
- **Verified (exit criterion):** live E2E **green** — login `dev@local.dev` (owner,
  Demo App) → job `1a23dacf…` 202 → SSE `job.started → stage.started → progress
  0.5 → 1.0 → stage.completed → job.completed(output_ref)` → read-back
  "Order history" with 4 persisted cases (functional/negative/security/a11y) → job
  row `completed` with matching `output_ref`. Failure paths: bad login **401** ·
  SSE without auth **401** · unknown requirement **404**.
  `pytest -q` → **131 passed** · `mypy` → **no issues in 40 source files** ·
  `ruff check/format` ✓ · web `tsc` (both configs) ✓ · `eslint .` ✓ · `pnpm build` ✓.
- **Decisions / gotchas (also in STATE.md §7):**
  - **Local-model output budget:** silent truncation at `output_budget` is the
    failure mode for local inference — prompt must cap case count + field lengths;
    re-run `scripts/e2e_s13.py` whenever prompt/budget/model changes.
  - **SSE + auth:** `EventSource` cannot send headers → fetch-streaming reader with
    manual SSE-frame parsing (comment keepalives skipped, terminal event ends loop;
    server also closes the stream after the terminal event).
  - `output_ref` on completed `test_case_generation` is the **persisted requirement
    id** — the UI reads the suite from `GET /requirements/{id}`, never from the SSE
    payload.
- **Commit:** `8c0ed5b step S1.3: web shell on the real API (login, 202 job
  creation, fetch-based SSE with Bearer, persisted test-case read-back) +
  GET /requirements/{id} endpoint + test-designer prompt compactness fix
  (local model fit); live E2E green, 131 tests, mypy strict + ruff green`
  (persistence half: `022fb6b`).
- **Next session start:** S1.4 (Eval runner CLI + golden set v1, §22; exit:
  `eval run` emits JSON report vs §31.7 targets) — see `STATE.md` §3.

## 2026-08-27 — S1.4 Eval runner CLI + golden set v1 (commit `74a733d`)

- **Goal:** build bible §19 S1.4 — `eval run` emits a JSON report against the §31.7
  targets (schema-valid ≥ 0.99 · oracle step coverage ≥ 0.85).
- **Did:**
  - `qa_copilot_ai.eval` package: `golden.py` (golden-set loader/validator + shared
    `step_coverage` helper), `runner.py` (per-fixture eval with failure isolation,
    `EvaluationReport`), `cli.py` (`python -m qa_copilot_ai.eval` → JSON report on
    stdout, human summary on stderr, exit 0/1/2).
  - **`packages/ai/golden/golden_v1.json` — 12 fixtures across 7 workflow categories;
    single source of truth for the S1.2 offline fakes AND the S1.4 live eval** (S1.2
    test file refactored onto it; the old 10-fixture inline set + local coverage
    helper removed).
  - `scripts/eval_run.py` — persistent runner reading `.env`; live run vs LM Studio
    Qwen-27B → `reports/eval_v1.json` (`reports/` is gitignored).
- **Verified (exit criterion):** `uv run pytest -q` → 145 passed · `uv run mypy` →
  no issues in 46 source files · `ruff check .` + `ruff format --check .` ✓ · live
  eval run emitted the JSON report with the §31.7 gates evaluated.
- **Next session start:** S2.1 (repository scanner) — see `STATE.md` §3.

## 2026-08-28 — S2.1 Repository scanner (commit `aa47408`)

- **Goal:** build bible §19 S2.1 — deterministic, LLM-free repository scanner:
  language/framework detection, test-structure detection, package managers, monorepo
  signals. **Exit: correct on 3 sample repos.**
- **Did:**
  - `qa_copilot_repository.scanner` (new module, ~530 lines) —
    `scan_repository(root) -> RepositoryProfile` + `main()` CLI
    (`python -m qa_copilot_repository.scanner <root>` → JSON profile). Sorted
    `os.walk` (deterministic order), symlinks never followed, `SKIP_DIRS` pruning
    (node_modules/.git/dist/build/caches/venvs), 50k-file cap, manifests read ≤512KB
    (larger ones noted + skipped). **Source files are classified by name only —
    never read.**
  - Detection:
    - **languages** — extension map (py/ts/js/go/rust/ruby/java/…); `languages`
      ordered by file count desc, then name.
    - **frameworks** — npm manifest deps (`NODE_FRAMEWORKS`: react/next/nuxt/express/
      fastify/vue/svelte/… + tailwind/vite/webpack) · Python manifest deps
      (`PYTHON_FRAMEWORKS`: fastapi/django/flask/starlette/…) from `project`/poetry/
      uv/`dependency-groups` + `requirements*.txt` · Go/Ruby/Rust/Spring via
      `MANIFEST_MARKERS` (go.mod/Gemfile/Cargo.toml/pom.xml) · config-file names
      (vite/webpack/tailwind/svelte/next/nuxt/astro/angular).
    - **test structure** — Vitest/Jest/Playwright/Mocha from npm deps, pytest from
      Python deps + `[tool.pytest.ini_options]` + `requirements*.txt`; test files by
      convention (`*.test.*`/`*.spec.*`/`__tests__/`, `test_*.py`/`*_test.py`,
      `*_test.go`) → `test_dirs` + `test_file_count`; Playwright `testDir` resolved
      from the config (recorded in `notes`).
    - **package managers** — root lockfiles/manifests: pnpm-lock.yaml→pnpm,
      package-lock.json→npm, yarn.lock→yarn, bun.lockb→bun, uv.lock→uv,
      poetry.lock→poetry, Pipfile→pipenv, else pyproject/setup/requirements→pip.
    - **monorepo** — pnpm-workspace.yaml `packages:` (line-based parse that stops at
      the next top-level key, so `onlyBuiltDependencies`/`allowBuilds` are ignored;
      quotes stripped) · npm `workspaces` (list or map) · uv `workspace.members` ·
      lerna/nx/rush markers; member globs recorded in `notes`.
  - `qa_copilot_domain.RepositoryProfile` (new domain entity) — shared contract for
    the S2.2 convention extractor, S2.3 automation agent, and the later §10
    `repositories` persistence; exported from `qa_copilot_domain` and
    `qa_copilot_repository` (`scan_repository`).
  - **3 golden samples** under `packages/repository/samples/sample_repos/` (same
    version-controlled golden-set precedent as S1.4's `packages/ai/golden`):
    - `js-web-app` — React + Vite + TypeScript + Tailwind, Vitest unit tests
      (`src/__tests__/`) + Playwright e2e (`e2e/`, `testDir` in config), pnpm.
    - `python-api` — FastAPI + uv, pytest (`tests/unit` + `tests/integration`,
      `conftest.py`).
    - `js-monorepo` — pnpm workspaces (React client + Express server),
      **no test framework** (manual smoke script) — exercises the
      "no test framework detected" note path.
  - `tests/unit/test_repository_scanner.py` (16 tests): full-profile golden
    assertions for all 3 samples (languages order, frameworks, test frameworks/dirs,
    counts, managers, monorepo flag, notes), determinism (two runs equal after
    stripping `scanned_at`), sorted-unique list invariants, str-root acceptance,
    missing/non-directory root → ValueError, empty repo, `node_modules`/`.git`
    pruning, pnpm-workspace regression (`onlyBuiltDependencies` ignored), Go +
    pytest naming conventions.
- **Verified (exit criterion):** `uv run pytest -q` → **161 passed** (16 new) ·
  `uv run mypy` → no issues in 48 source files · `ruff check .` ✓ ·
  `ruff format --check .` ✓ (71 files) · sanity scans of real repos
  (`ai-qa-copilot-demo-app`, `ai-qa-copilot`, `WheelDesk`) → profiles as expected.
- **Decisions / gotchas (also in STATE.md §5/§7):**
  - **pnpm-workspace.yaml:** pnpm 11 puts `onlyBuiltDependencies`/`allowBuilds` in
    the same YAML as `packages:` — the line-based parser reads only list items under
    the top-level `packages:` key (regression test added for this bug class).
  - **`_is_test_file` stem math:** for `*.test.*`/`*.spec.*` the stem is
    `name[:rfind(".")]` — a naive single-suffix strip broke `counter.spec.ts`
    (fixed + covered by the js-web-app golden profile).
  - **Determinism:** sorted walk + sorted output lists; `languages` ordered count
    desc then name; `scanned_at` is the only non-deterministic field (tests strip
    it before comparing).
  - **Safety:** no dependency installation, no symlink following, capped walk,
    bounded manifest reads — scanning an untrusted repo is read-only.
- **Commit:** `aa47408 step S2.1: repository scanner + samples + tests (…)` —
  36 files, 1089 insertions.
- **Next session start:** S2.2 (convention extractor — locators, page objects,
  fixtures, helpers; exit: golden outputs match on 2 repos) — see `STATE.md` §3.

## 2026-08-28 — S2.2 Convention extractor (deterministic, LLM-free)

- **Goal:** build bible §19 S2.2 — extract the target repo's test conventions
  (locators, page objects, fixtures, helpers) on top of the S2.1 scanner.
  Exit: golden outputs match on 2 repos.
- **Did:**
  - `packages/repository/src/qa_copilot_repository/conventions.py` (new):
    `extract_conventions(root) -> TestConventions` + CLI
    `python -m qa_copilot_repository.conventions <root>` → JSON. Reuses the
    scanner's safety rules (pruned walk, 50k file cap, 512KB capped reads,
    symlink-safe) and its test-file detection.
  - Domain (S2.3/§10 shared contract): `LocatorStyle`, `TestScript`,
    `TestConventions` in `qa_copilot_domain.entities` (both `Test*` classes
    carry `__test__ = False` — pytest collection gotcha) · exported from
    `qa_copilot_domain` and `qa_copilot_repository` (`extract_conventions`).
  - Scanner refactor: `read_text_capped()` and `is_test_file()` promoted to
    public helpers; `SKIP_DIRS`/`MAX_FILES`/`TEST_EXTENSIONS` exposed for reuse
    (S2.1 behavior unchanged — all 16 scanner tests still green).
  - `tests/unit/test_conventions.py` (18 tests): **golden outputs on 2 real repos** —
    `js-web-app` (Vitest + Playwright: `*.test.*`/`*.spec.*` patterns,
    `getByRole` > `locator` > `getByTestId` ordering, `e2e/helpers.ts` helper,
    `playwright.config.ts` config) and the demo app `ai-qa-copilot-demo-app`
    (Playwright `test.extend` fixture, `data-testid` vocabulary, `baseURL` from
    `playwright.config.js`); synthetic Playwright (locator ordering,
    `base.extend`, `page-objects/`, `data-testid` quoted-usage only); synthetic
    pytest (pytest fixtures, `conftest.py`, `tests/unit` + `tests/integration`
    test-tree dirs, `test_*.py`/`conftest.py` patterns); `package.json` test
    scripts (filter + monorepo dedupe); no-framework repo (empty conventions +
    note); determinism; str-root; missing root → ValueError; empty repo;
    `node_modules`/`.git` pruning; `src/__tests__` NOT a test-tree dir
    (name-gated rule).
- **Verified (exit criterion):** golden outputs match on 2 repos ✓ (CLI run on
  `js-web-app` + `ai-qa-copilot-demo-app`, values hand-checked against the repos)
  · `uv run pytest -q` → **179 passed** (18 new) · `uv run mypy` → no issues in
  50 source files · `ruff check .` ✓ · `ruff format --check .` ✓.
- **Decisions / gotchas (also in STATE.md §5/§7):**
  - **Test-tree name gate:** the demo app has `src/__tests__/` — the ancestor
    rule must not classify `src` as a test-tree dir (explicitly excluded);
    `tests/unit` + `tests/integration` ARE test-tree dirs.
  - **`data-testid` false-positive guard:** only quoted attribute usage
    (`data-testid="..."` / `data-testid: '...'`) is captured — a test-ID object
    map (`testids.js`) must not leak its keys into the vocabulary.
  - **Fixture detection:** both `test.extend(...)` and `base.extend(...)`
    (Playwright) + pytest `@pytest.fixture`; `conftest.py` always a fixture file.
  - **Locator attribution:** `getByRole`/`getByTestId`/… attributed to
    `playwright` or `testing-library` when the file imports that toolkit, else
    `generic`; ordered count-desc then name.
  - **`package.json` scripts:** only test-related names/commands (vitest,
    playwright, cypress, mocha, jest, pytest, …) captured, deduped by name
    across monorepo manifests.
- **Commit:** `c9d41f2 step S2.2: add deterministic test convention extractor (…)`.
- **Next session start:** S2.3 (Automation Agent — generate tests using the
  extracted conventions; exit: generated code passes lint + type ≥ 95%) — see
  `STATE.md` §3.

## 2026-08-28 — S2.3 Automation agent — live evaluation closed (gate PASS)

- **Exit criterion met:** live S2.3 eval vs LM Studio `Qwen3.8-27B-Q4_K_M`
  (`http://127.0.0.1:8080/v1`): **2/2 fixtures pass on every axis**
  (schema ✓, conventions ✓, ESLint ✓, strict tsc ✓) —
  `lint_type_pass_fraction = 1.0 ≥ 0.95` → report `passed: true` (exit 0).
  Artifact: `reports/s23_live_report.json` (2026-08-28T07:47Z; ~11 s/fixture;
  176–226 completion tokens — no thinking bloat).
- **Root cause of the earlier empty-output failures (and the fix):** Qwen3.8
  thinking mode consumed the entire 4000-token budget (prompt spec
  `test-automator@1` `output_budget`) in `reasoning_content`, leaving
  `content` empty → loud schema failure. Verified both levers: `/no_think`
  in-prompt works; body param `chat_template_kwargs: {"enable_thinking":
  false}` is honored by LM Studio (llama.cpp) with `reasoning_content` empty.
  Chose the body param so the versioned prompt stays untouched. Raising
  `max_tokens` was rejected: thinking needs 10k–30k+ tokens.
- **Gateway/CLI:** new opt-in `LLMGateway(extra_body: Mapping[str, object] |
  None)` — server-specific fields merged into every chat-completions body;
  canonical fields (`model`, `messages`, `stream`, …) always win. CLI flag
  `--extra-body '<json object>'` (validated: JSON object or exit 2). Live
  invocation: `python -m qa_copilot_ai.automation.cli … --extra-body
  '{"chat_template_kwargs": {"enable_thinking": false}}'`.
- **Convention-expectation fix (eval-design bug, not a model failure):**
  golden v1 pinned the reference answer's exact file names
  (`counter-increment.spec.ts` / `counter-initial.spec.ts`), but
  test-automator@1 rule 1 leaves `<name>` to the model
  (`e2e/<name>.spec.ts`) — the live model's `e2e/counter.spec.ts` conformed.
  `AutomationExpectations` now supports exact `file_path` **or**
  `file_path_pattern` (fnmatch; validator requires one); golden v1 switched
  to `e2e/*.spec.ts`; `conventions_respected()` checks accordingly. New tests
  pin both directions (conforming name passes; wrong dir fails).
- **Matcher question resolved:** `toHaveTextContent` is a **Cypress**
  assertion, not a Playwright one — the stub's omission is *correct* (real
  `@playwright/test` types reject it; Playwright uses `toHaveText` /
  `toContainText`). Kept the stub as-is; documented in the stub
  (`index.d.ts` note) and the negative-probe test docstring.
- **Verified (gates):** `uv run pytest tests -q` → **231 passed** (was 228;
  +3: `extra_body` wire test, `--extra-body` config test, conforming-name
  convention test) · `ruff check packages tests` ✓ ·
  `ruff format --check packages tests` ✓ · `mypy packages apps` → no issues
  in 48 source files.
- **Gotchas:** CLI module is `qa_copilot_ai.automation.cli` (no
  `__main__.py`; old docstrings in `cli.py`/`golden.py` said
  `qa_copilot_ai.automation` — fixed). `Start-Process -ArgumentList` mangles
  JSON args (splits on spaces, drops quotes) — for live runs, pass argv via a
  small Python launcher (`scripts/_s23_live.py`, deleted after the run).
- **Next session start:** S2.4 — diff review UI + human approval (apply /
  reject flows) — see `STATE.md` §3.

## 2026-08-28 — S2.4 Generated-test review (diff review + human approval) — exit met

- **Goal:** build bible §19 S2.4 — review generated tests (S2.3 output) and
  apply them to the workspace or reject them. Exit: apply + reject flows tested.
- **Did:**
  - Schema: `generated_tests` table (migration `7e9a4b2c1d3f`) — one review row
    per S2.3 output: `file_path` / `file_path_pattern` / `language` / `framework`
    / `content` / `notes` (JSONB) / `repository_path` + reviewer trail
    (`status`, `reviewed_by`, `reviewed_at`, `review_note`) + `project_id` /
    `job_id` / `test_case_id` links. Domain state machine
    (`qa_copilot_domain.enums`): `pending → approved → applied`,
    `pending|approved → rejected`; applied/rejected terminal.
  - `qa_copilot_repository.generated_tests`: `persist_generated_test` (S2.4
    persistence entry point) + `get`/`list` + `set_review_status` — enforces the
    state machine (no-op or illegal transition → `ValueError` → 409; no-op gets
    its own "already {status}" message), sets the reviewer trail on every
    transition, flushed-not-committed.
  - API (`routes.py` + `schemas.py`):
    - `POST /api/v1/automation/generate` (member+; unknown project → 403, not
      404 — no existence leak; unknown/cross-project case → 404) → **202 +
      job_id + Location** for an `automation_generation` job.
    - `jobs.py` `AutomationJobAgent`: loads the approved case → S2.1
      `scan_repository` + S2.2 `extract_conventions` → runs the S2.3 agent
      through the `AutomationRunner` protocol (real `AutomationAgent` when LLM
      is configured, deterministic `AutomationStub` otherwise) → persists a
      **pending** review row → `ai_actions` audit on the job's `ai_sessions`
      anchor (`agent=test-automator`, `output_ref` = the row id).
    - Review queue `GET /projects/{id}/generated-tests` + row detail
      `GET /generated-tests/{id}` (viewer+; 401/403/404 matrix, malformed id →
      404 without a 500).
    - `POST /generated-tests/{id}/approve|reject` (member+; review note body
      fully optional — `note = body.note if body is not None else None`) +
      `POST /generated-tests/{id}/apply` (member+; writes the file under the
      row's `repository_path`; audited like the other two).
  - **Apply guards (V1 policy — no silent ship):** existing target file → 409
    (re-generating creates a NEW row) · missing `repository_path` → 409 ·
    `file_path` escaping the repository root → 409 · missing repo dir → 409 ·
    row rolled back to its pre-apply state on EVERY failure path (review still
    possible afterwards).
  - `tests/unit/test_generated_tests.py` (13 tests): hermetic per-test scratch
    Postgres DB (alembic to head) + deterministic `uuid5` ids + seeded
    users/projects/roles; app wired with `AutomationStub` pinned via settings
    (`llm_base_url=None, llm_model=None, _env_file=None` — init kwargs beat env
    or a `.env` leaked by alembic's `_load_dotenv`). Covers: 202 → job →
    pending row (job `output_ref` = row id) + audit; RBAC/validation matrix;
    list/detail matrix; approve → apply (file written); apply directly from
    pending; reject terminal; no-op + illegal transitions → 409 with the
    domain's exact messages; audit rows (anchor + action per review); apply
    guards + rollback.
  - **Defects the new tests caught (all real, all fixed):**
    1. `_stub_test_content` f-string bug — `f'... async ({ page }) ...'`
       evaluated `page` as a Python expression → `NameError` → every stub job
       failed. Doubled the braces: `{{ page }}` emits the literal Playwright
       fixture syntax.
    2. `FileExistsError` is a **sibling** of `FileNotFoundError` (both extend
       `OSError`), not a subclass — the existing-target guard fell into
       `except OSError` → 500 instead of the documented 409. Added an explicit
       `except FileExistsError` → 409 clause.
    3. Review endpoints 500'd when the optional note body was omitted — now
       `body: schemas.GeneratedTestReviewIn | None` with a None-safe note.
    Plus: migration used bare `sa.JSONB()` (not a column type) →
    `postgresql.JSONB(astext_type=sa.Text())`.
  - **Gate fixes:** mypy strict — `AutomationInput.test_case` is now
    `TestCase | DomainTestCase` (S2.3 golden/runner pass the suite-local ai
    `TestCase`; the S2.4 job passes the DB-loaded domain entity; both render to
    the same prompt variables via `model_dump`); two `db.get(...)` results
    (`GeneratedTest | None`) replaced with the already-typed row in
    approve/apply handlers. `tests/unit/test_repository.py` `EXPECTED_TABLES`
    += `generated_tests` (and the dev DB migrated via `alembic upgrade head`).
    Prettier pass on `apps/web` (9 files) to green `format:check`.
  - Removed temp debug artifacts (`tmp_debug_s24.py`,
    `tests/unit/test_zz_debug_tmp.py`) before gates/commit.
- **Verified (exit criterion):** apply + reject flows tested ✓ —
  `uv run pytest tests -q` → **244 passed** (13 new) · `uv run mypy packages
  apps` → no issues in 49 source files · `ruff check packages apps tests` ✓ ·
  `ruff format --check packages apps tests` ✓ · web: `npm run build` ✓ ·
  `npm run lint` ✓ · `npm run format:check` ✓.
- **Commit:** `d52b8f7 step S2.4: generated-test review (…)` — 24 files,
  1745 insertions.
- **Next session start:** S3.1 — execution worker (Playwright run,
  trace/screenshot/video/console/network capture; exit: 1 test on the demo app
  → all artifacts stored) — see `STATE.md` §3.

## 2026-08-28 — S3.1 Execution worker (Playwright run + §15 artifacts) — exit met

- **Goal:** build bible §19 S3.1 — execution worker: Playwright run,
  trace/screenshot/video/console/network capture. **Exit: 1 test on the demo
  app → all artifacts stored.**
- **Did:**
  - `qa_copilot_execution` (new package, database-free per §15/§31.11):
    - `store.py` — `ArtifactStore`: §31.11 layout `runs/{run_id}/{test_id}/{name}`,
      segment-validated (`check_segment`: alnum + `._-`, no `..`/slashes),
      overwrites rejected; `ArtifactStoreError` on any layout violation.
    - `report.py` — frozen worker contract: `RunReport` / `TestResultReport` /
      `ArtifactReport` / `RunTotals` (durations in milliseconds).
    - `runner.py` — `run_playwright(PlaywrightConfig)`: spawns the target
      repo's `node_modules/.bin/playwright(.cmd) test [--filter]
      --reporter=json` (`_resolve_command` — PATH `playwright` only as
      fallback), reads the JSON report from stdout (tolerates leading log
      noise) or `PLAYWRIGHT_JSON_OUTPUT_FILE`; status semantics: `completed`
      = Playwright produced a JSON report (even with failing tests — outcomes
      are per-test data), `failed` = the worker itself could not get a report
      (spawn error / timeout / no JSON); captures the §15 artifact set
      (trace/screenshot/video/console/network/dom/log) into the store.
    - `cli.py` + `__main__.py` — `python -m qa_copilot_execution <target-dir>
      [--filter TEXT] [--timeout S] [--store PATH] [--run-id ID] [--json]`;
      exit 0 = all tests pass · 1 = run completed with test failures ·
      2 = usage error · 3 = worker failed. The CLI is database-free.
  - `qa_copilot_repository.runs` — `persist_run` maps a `RunReport` onto the
    §10 `test_runs`/`test_results`/`artifacts` rows: one run row, one result
    per test (`duration` in *seconds* — converted from `duration_ms`), one
    artifact row per artifact (URI only, never contents — §15); flushed, not
    committed (caller owns the transaction — same convention as
    `generated_tests`). Domain S3.1 enums (`RunStatus`, `TestResultStatus`,
    `ArtifactType`) added to `qa_copilot_domain.enums` + exports.
  - Demo app (`ai-qa-copilot-demo-app`) gained a Playwright e2e suite:
    `e2e/demo.spec.js`, `e2e/fixtures.js`, `playwright.config.js` (its
    `webServer` block boots the demo servers), `test:e2e` / `test:e2e:headed`
    scripts — which updated the S2.2 conventions golden for the demo app
    (`tests/unit/test_conventions.py`: `test_file_patterns=["*.spec.js"]`,
    locator styles, fixture file, test config, e2e scripts).
  - Tests: `tests/unit/test_execution.py` (store layout/escape/overwrite
    rejections, report parsing incl. JSON with leading noise and the report
    FILE path, run/test status semantics, CLI exit codes 0/1/2/3 — the CLI
    tests use a fake target repo whose `playwright` shim writes a prepared
    JSON report to `PLAYWRIGHT_JSON_OUTPUT_FILE`, so no real Playwright
    launches, but the real worker path is exercised: spawn → read report →
    classify → store → exit code) + `tests/unit/test_repository.py`
    (`persist_run`: row counts, statuses, duration ms→s, artifact URIs,
    `metadata_` column).
- **Verified (gates):** `uv run pytest -q` → **288 passed** (S3.1 targeted:
  **59 passed** in `test_execution.py` + `test_repository.py`) ·
  `ruff check` + `ruff format --check` clean on the new/updated files ·
  `mypy packages apps tests` → **Success: no issues found in 71 source
  files** — this session also fixed the 18 pre-existing errors in
  `tests/unit/test_automation_agent.py` (missing return annotations on
  `_agent_run`/`_do`; `object`-indexed JSON body; `file_path_pattern`
  `str | None` narrowing; `TOOLCHAIN is not None` asserts in the
  `@GATE_SKIP` tests + `_eval_run`; `_FakeLLMServer` typed subclass for the
  `seen_bodies` capture list). The mypy gate now passes fully.
- **Live run (exit criterion):** worker against `ai-qa-copilot-demo-app` →
  **exit 0, Playwright 1/1 passed, 5 artifacts stored** under
  `data/artifacts/runs/s31-live-verify`. Local Playwright 1.62.1 + Chromium
  available; the worker used the demo app's own `playwright.cmd` shim and
  its `webServer` block to start the demo servers.
- **Gotchas:** Windows — the target's `node_modules/.bin/playwright(.cmd)`
  shim is NOT the PATH-level `playwright`; resolve per target. Windows
  text-mode writes gain `\r\n` → on-disk size ≠ bytes written (assert the
  on-disk size). SQLAlchemy artifact column is `metadata_`, not `metadata`.
  mypy — parameterized generics cannot be used in `isinstance` checks;
  `ThreadingHTTPServer` extras need a subclass annotation (a declared
  return type widens away inner-class attributes).
- **Next session start:** S3.2 — Runs API + run history + artifacts UI
  (exit: a run is visible with its artifacts) — see `STATE.md` §3.
