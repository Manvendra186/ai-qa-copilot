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
