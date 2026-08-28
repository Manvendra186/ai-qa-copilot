# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation **complete** · Phase 1 — Requirement → Test Design → Eval **complete** ·
  Phase 2 — Playwright Copilot **complete** · Phase 3 — Execution (S3.1 ✓)
- **Step:** S0.1–S3.1 ✓ (S3.1 = execution worker: Playwright run + §15 artifacts) ·
  **S3.2 next** (Runs API + run history + artifacts UI)

## 2. Just completed

- 2026-08-28 · **S3.1 (execution worker) — live exit PASS** (1 test on demo app →
  all artifacts stored): `qa_copilot_execution` (database-free) — `run_playwright`
  spawns the target repo's `playwright test --reporter=json` (resolved via the
  target's `node_modules/.bin` shim; the config's `webServer` boots the demo
  servers) → parses the JSON report → §15 artifact set (trace/screenshot/video/
  console/network/dom/log) → `ArtifactStore` under §31.11 layout
  `runs/{run_id}/{test_id}/{name}` (segment-validated, no overwrites) → frozen
  `RunReport` · `qa_copilot_repository.runs.persist_run` maps it onto
  `test_runs`/`test_results`/`artifacts` (flush-only; `duration` ms→s) ·
  CLI `python -m qa_copilot_execution <target-dir> [--filter TEXT] [--store PATH]
  [--run-id ID] [--json]` — exit 0 (all pass) / 1 (tests failed) / 2 (usage) /
  3 (worker failed: spawn/timeout/no JSON) · demo-app e2e suite now also feeds
  the S2.2 conventions golden (`test_conventions.py` updated: `e2e/*.spec.js`,
  `playwright.config.js`, `e2e/fixtures.js`) · **live: demo app 1/1 pass,
  exit 0, 5 artifacts stored under `data/artifacts/runs/s31-live-verify`** ·
  **288 tests ✓ · mypy strict (71 files incl. tests) ✓ — also fixed the 18
  pre-existing errors in `test_automation_agent.py` ✓ · ruff ✓.**

- 2026-08-28 · **S2.4 (generated-test review) — apply + reject flows tested** (commit `d52b8f7`):
  `generated_tests` review rows — state machine `pending → approved → applied` /
  `pending|approved → rejected` (applied/rejected terminal; migration `7e9a4b2c1d3f`) ·
  `POST /api/v1/automation/generate` → 202 + `automation_generation` job (S2.3 agent,
  hermetic `AutomationStub` when no LLM) → the job's `output_ref` is the **pending**
  review row · review queue `GET /projects/{id}/generated-tests` + row detail
  (viewer+; 401/403/404 matrix) · approve / reject / apply (member+; optional note
  body; audited `ai_sessions`/`ai_actions`) · apply = file write under the row's
  `repository_path`: no silent overwrite (existing target 409), path-escape +
  missing-repo guards (409), row rolled back on every failure path ·
  tests caught 3 real defects, all fixed (stub f-string `{ page }` → `NameError`;
  `FileExistsError` is NOT a `FileNotFoundError` subclass → 500 instead of 409;
  500 when the optional review-note body was omitted) ·
  **244 tests ✓ · mypy strict (49) ✓ · ruff ✓ · web build/lint/format ✓.**

- 2026-08-28 · **S2.3 (automation agent) — live gate PASS**: `AutomationAgent`
  (test-automator@1) + §21 gate runner (`python -m qa_copilot_ai.automation.cli`):
  golden v1 (2 fixtures, `js-web-app`) scored by schema + conventions + real
  `tsc --strict`/ESLint. Live vs LM Studio Qwen3.8-27B: **2/2 pass,
  lint+type fraction 1.0 ≥ 0.95** (artifact `reports/s23_live_report.json`).
  Qwen3 thinking disabled via gateway `extra_body` / CLI `--extra-body`
  (`chat_template_kwargs.enable_thinking=false`) — do NOT raise `max_tokens`.
  Expectations now accept `file_path_pattern` (test-automator@1 rule 1 leaves
  `<name>` to the model). **231 tests ✓ · mypy (48) ✓ · ruff ✓.**

- 2026-08-28 · **S2.2 (conventions extractor) — deterministic, LLM-free** (commit `c9d41f2`):
  `qa_copilot_repository.conventions` (on top of the S2.1 scanner) →
  `qa_copilot_domain.TestConventions` — test-file patterns · locator styles
  (Playwright/testing-library/generic, ordered by usage) · page objects ·
  fixtures (`conftest.py`, `test.extend`/`base.extend`) · helpers · test configs ·
  `data-testid` vocabulary (quoted attribute usage only) · Playwright `baseURL` ·
  test-related `package.json` scripts (deduped across monorepo manifests) ·
  scanner refactor: `read_text_capped`/`is_test_file` shared helpers ·
  CLI `python -m qa_copilot_repository.conventions <root>` → JSON ·
  **exit: golden outputs match on 2 repos ✓** (js-web-app: Vitest+Playwright;
  demo app: Playwright + `data-testid`) · **179 tests ✓ · mypy strict (50) ✓ · ruff ✓.**
- 2026-08-28 · **S2.1 (repo scanner) — deterministic repository scan** (commit `aa47408`):
  `qa_copilot_repository.scanner` (LLM-free) → `qa_copilot_domain.RepositoryProfile` —
  languages (count desc, then name) · frameworks (npm/Python/Go/Ruby/Rust/Spring
  manifests + config files) · test structure (Vitest/Jest/Playwright/Mocha/pytest
  signals, test-file conventions `*.test.*`/`*.spec.*`/`test_*.py`/`*_test.go`,
  Playwright `testDir`) · package managers (root lockfiles/manifests) · monorepo
  (pnpm-workspace.yaml `packages:`, npm `workspaces`, uv members, lerna/nx/rush) ·
  safety: SKIP_DIRS, no symlink follow, 50k file cap, manifests ≤512KB, source files
  classified by name only (never read) · CLI `python -m qa_copilot_repository.scanner
  <root>` → JSON · **3 golden samples** `packages/repository/samples/sample_repos/`
  (js-web-app: React+Vite+TS/Vitest+Playwright · python-api: FastAPI+uv/pytest ·
  js-monorepo: pnpm workspaces, no tests) · **exit: 161 tests ✓ · mypy strict (48) ✓
  · ruff ✓ · real-repo sanity scans ✓.**
- 2026-08-27 · **S1.4 (Eval)** (`74a733d`): `qa_copilot_ai.eval` CLI + 12-fixture `packages/ai/golden/golden_v1.json` (S1.2/S1.4 shared truth) + `scripts/eval_run.py`; §31.7 gates 0.99/0.85; live LM Studio run → `reports/eval_v1.json`.
- 2026-08-27 · **S1.3 (UI flow)** (`8c0ed5b`): web shell on the real API — login, 202+SSE, persisted read-back `GET /api/v1/requirements/{id}`; test-designer prompt capped (≤6 cases) for Qwen-27B's output budget; live E2E green.
- 2026-08-27 · **S1.3 (persistence)** (`022fb6b`): AI suite → §10 rows (`persist_requirement_with_suite` + M:N join); job `output_ref` = requirement id.
- 2026-08-27 · **S1.2 — Test Design Agent** (`bb5bb2f`): `TestDesignAgent` + `POST /api/v1/requirements/test-cases` job; exit: 10 fixtures → schema-valid + step coverage ≥ 85% vs oracle ✓.
- 2026-08-27 · **S1.1 — Requirement Agent** (`6a1bf88`): `RequirementAgent` + schema-validated `RequirementAnalysis` on the S0.9 seam; exit: 10/10 schema-valid ✓.
- 2026-08-27 · **S0.9 — jobs API** (`2051749`): 202 + `GET /jobs/{id}` + SSE `/events` (15s heartbeat) + `JobAgent`/`StubAgent` seam + state machine + reaper.
- 2026-08-27 · **S0.10 — demo app v0** (repo `ai-qa-copilot-demo-app`, `43739a5`): Express + better-sqlite3 + React; user `qa`/`qa1234`; smoke 11/11 · defects 7/7 ✓.
- 2026-08-26/27 · **S0.1–S0.8**: monorepo (uv+pnpm) · compose infra (PG16+pgvector :5433, Redis) · FastAPI · domain · SQLAlchemy+Alembic+seed · AI gateway (LM Studio live) · React shell · auth baseline (JWT + project RBAC). *(details: SESSION_LOG.md)*

## 3. NEXT STEP (start here)

**S3.2 — Runs API + run history + artifacts UI** (build bible §19 Phase 3):
a run is visible with its artifacts. Wire `persist_run` into an API job
(S2.4-style 202 + job), list run history per project, fetch artifact
contents by URI from the store.
- Queued follow-ups (not blockers): SSE bus is in-process — multi-worker deploy
  needs Redis pub/sub · demo-app `Dockerfile` unverified (S3.1) · eval report
  artifacts live in gitignored `reports/` — commit one baseline after each
  prompt/model change if we want drift tracking.

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
- S2.1 scanner: deterministic + LLM-free · `RepositoryProfile` lives in the **domain**
  pkg (shared contract for the S2.2 extractor, S2.3 agent, and §10 persistence) ·
  source files classified by name only, never read · `languages` ordered count desc
  → name, all other lists sorted · `scanned_at` is the only time-varying field
  (tests strip it before comparing)
- S2.2 conventions: extractor stays **deterministic/LLM-free** (pure scan) ·
  test-tree is name-gated (`tests` dirs only — `src` never, even with `__tests__`
  inside) · `data-testid` from quoted attribute usage only (test-ID object maps
  like `testids.js` must not leak) · `package.json` scripts filtered to
  test-related commands, deduped across monorepo manifests · locator attribution:
  import-based (playwright/testing-library) else `generic`

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
- Web shell (S0.7/S1.3): `apps/web/` (React 18 + Vite 6 + TS + Tailwind 4) ·
  `src/lib/api.ts` (Bearer fetch client + fetch-streaming SSE reader) ·
  `src/hooks/{useAuth,useJobEvents}.ts` · pipeline contract `src/lib/pipeline.ts` ·
  `/api` dev proxy → :8000 · `scripts/e2e_s13.py` (API-level E2E of the S1.3 chain)
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
- Test design agent (S1.2): `packages/ai/src/qa_copilot_ai/agents/test_design.py`
  (`TestDesignAgent`/`TestSuite`/`TestCase`/`TestDesignInput`) ·
  `packages/ai/prompts/test-designer.v1.md` (v1 prompt) · `jobs.py`
  (`TestDesignJobAgent`) · `main.py` (`_build_test_design_jobs_agent`,
  `app.state.jobs_test_design_agent`) · `routes.py`
  (`POST /api/v1/requirements/test-cases`) · `schemas.py` (`TestDesignRequest`) ·
  `tests/unit/test_test_design_agent.py` (oracle step-coverage gate)
- Eval (S1.4): `packages/ai/src/qa_copilot_ai/eval/` (`golden.py` golden-set loader +
  shared `step_coverage` · `runner.py` `run_test_design_eval` + `EvaluationReport` ·
  `cli.py` `python -m qa_copilot_ai.eval` JSON report + exit 0/1/2) ·
  `packages/ai/golden/golden_v1.json` (12 fixtures, 7 categories — S1.2/S1.4 shared
  source of truth) · `tests/unit/test_eval_runner.py` (fake OpenAI server e2e,
  loader/CLI/isolation) · `scripts/eval_run.py` (persistent runner; `reports/`
  gitignored)
- Repo scanner (S2.1): `packages/repository/src/qa_copilot_repository/scanner.py`
  (`scan_repository` + CLI `python -m qa_copilot_repository.scanner <root>` → JSON) ·
  `qa_copilot_domain.RepositoryProfile` (S2.2/S2.3/§10 shared contract) · golden
  samples `packages/repository/samples/sample_repos/{js-web-app,python-api,js-monorepo}`
  · `tests/unit/test_repository_scanner.py` (16 tests: golden profiles, determinism,
  pruning, pnpm-workspace regression)
- Conventions extractor (S2.2): `packages/repository/src/qa_copilot_repository/conventions.py`
  (`extract_conventions` + CLI `python -m qa_copilot_repository.conventions <root>` → JSON) ·
  `qa_copilot_domain.TestConventions` (+ `LocatorStyle`, `TestScript` — shared S2.3
  contract) · `tests/unit/test_conventions.py` (18 tests: golden js-web-app + demo
  app, synthetic Playwright/pytest, locator/fixture/helper/page-object detection,
  `data-testid` false-positive guard, `package.json` scripts, edge cases)
- Generated-test review (S2.4): `packages/repository/src/qa_copilot_repository/
  generated_tests.py` (`persist_generated_test` / `get` / `list` /
  `set_review_status` — the state machine) + `models.GeneratedTest`
  (migration `7e9a4b2c1d3f`) · `qa_copilot_domain.enums.GeneratedTestStatus`
  (+ `ALLOWED_GENERATED_TEST_TRANSITIONS`, `can_transition_generated_test`) ·
  `apps/api/src/qa_copilot_api/jobs.py` (`AutomationJobAgent` + `AutomationStub`
  + `AutomationRunner` protocol — real S2.3 agent or hermetic stub) ·
  `main.py` (wires the runner from settings) · `routes.py`
  (`POST /api/v1/automation/generate`, `GET /projects/{id}/generated-tests`,
  `GET /generated-tests/{id}`, `/approve` `/reject` `/apply`) · `schemas.py`
  (`AutomationRequest`, `GeneratedTestOut`, `GeneratedTestReviewIn`) ·
  `tests/unit/test_generated_tests.py` (13 tests, hermetic scratch DB + stub pin)

## 7. Open questions / gotchas

- **Env leak → wrong agent (S1.1):** `get_database_url()` → `_load_dotenv()` injects `.env` LLM keys
  into `os.environ` even in tests (pydantic-settings reads env vars) → app silently wires the
  real agent. Stub-contract tests must pass `llm_base_url=None, llm_model=None` (init kwargs beat env).
- **mypy strict + `dict[str, object]` (S1.1):** `audit_dict()` values are `object` — `int(audit["x"])` fails; use `cast(int, ...)`.
- **node:sqlite (S0.10):** experimental on Node 22 — demo app uses `better-sqlite3` (approve in `pnpm-workspace.yaml` `onlyBuiltDependencies`/`allowBuilds`).
- **PowerShell NativeCommandError (S0.10):** any child-process stderr makes the tool shell report
  failure — read the actual output/exit code; `git --no-pager`; servers via `Start-Process -PassThru`.
- **PATH gotcha:** docker CLI is on the USER PATH — old terminals don't see it; refresh `$env:Path` from Machine+User.
- **pnpm 11:** the `pnpm` field in `package.json` is IGNORED — `onlyBuiltDependencies` (esbuild) goes in `pnpm-workspace.yaml`.
- **pnpm-workspace.yaml (S2.1):** that same file also carries `onlyBuiltDependencies`/`allowBuilds`
  lists — a parser must read only list items under the top-level `packages:` key
  (regression: `test_pnpm_workspace_ignores_other_keys`).
- **Vite dev binds `[::1]:5173`:** `curl http://127.0.0.1:5173` refused — use `http://localhost:5173`.
- **pydantic v2 (S0.4):** `Field(..., strip_whitespace=True)` is a deprecated v1 kwarg — use `Annotated[str, StringConstraints(...)]`.
- **ruff isort (S1.1):** `qa_copilot_*` is NOT first-party (src-layout workspace) — sorts in the third-party block.
- **pytest collection (S1.2/S2.2):** non-test classes named `Test*` (`TestCase`, `TestSuite`,
  `TestDesignInput`, S2.2's `TestConventions`/`TestScript`) need `__test__ = False`
  or pytest warns it cannot collect them.
- **oracle gate (S1.2):** the oracle is the independent reference — when a fixture
  missed the 85% step-coverage gate, extend the fake model output (it stands in for a
  competent LLM), never trim the oracle to fit the output.
- **Local-model output budget (S1.3):** LM Studio silently truncates at the requested
  `max_tokens` → mid-JSON `EOF` → loud schema failure. Qwen-27B needed >4000 tokens for
  the old uncapped prompt; `test-designer.v1.md` now caps ≤6 cases + compact fields.
  Re-run `scripts/e2e_s13.py` after any prompt/budget/model change.
- **mypy strict + pydantic:** wire-string/negative cases go through `model_validate` — typed
  constructors are arg-checked (`status="completed"` → arg-type error).
- **pydantic-settings + mypy (S0.9):** private `_env_file` kwarg invisible to mypy → tests carry `# type: ignore[call-arg]`.
- **SQLAlchemy (S0.5/8):** `metadata_` not `metadata` (reserved) · delete children before `db.delete(parent)`
  (composite-PK children NULL the PK) · `engine.dispose()` (ALL engines) before `DROP DATABASE`.
- **ruff B023 (S0.5):** factory lambdas in loops must bind loop vars (`lambda title=title, i=i: ...`).
- **Alembic + pgvector (S0.5):** migrations import `pgvector.sqlalchemy` by *attribute* — add `import pgvector.sqlalchemy` at top.
- **Postgres UUID columns (S0.8):** fixtures must seed real UUIDs (`uuid5`) — string ids → `invalid input syntax for type uuid`.
- **PowerShell + curl.exe (S0.8):** JSON bodies get mangled through the tool shell — use a Python (urllib) script for API smoke.
- **Qwen3 thinking mode (S2.3):** LM Studio's Qwen3.8 spends the whole `max_tokens`
  budget in `reasoning_content` → empty `content` → loud schema failure. Raising
  `max_tokens` is the wrong fix (thinking needs 10k–30k+). Fix: gateway
  `LLMGateway(extra_body=...)` / CLI `--extra-body` with
  `{"chat_template_kwargs": {"enable_thinking": false}}` (or `/no_think` in the
  prompt). Verified 2026-08-28: 176–245 completion tokens, clean contract.
- **Playwright matchers (S2.3):** `toHaveTextContent` is a **Cypress** API — not in
  real `@playwright/test` and deliberately absent from the type stub
  (`tests/unit/support/playwright-test/index.d.ts`); the gate must reject it.
- **f-string braces in generated code (S2.4):** `{ page }` inside an f-string is
  evaluated as a Python expression — emit literal braces with `{{ page }}`
  (regression: the S2.4 stub crashed with `NameError: name 'page' is not defined`).
- **`FileExistsError` ≠ `FileNotFoundError` (S2.4):** siblings under `OSError` —
  `except FileNotFoundError` does NOT catch "target exists"; the apply guard needs
  its own `except FileExistsError` clause for the 409 (else it leaks a 500).
- **mypy strict + `db.get` (S2.4):** `Session.get()` returns `Model | None` —
  pass the already-typed row through instead (or narrow) to satisfy strict mode.
- **Windows Playwright shim (S3.1):** `shutil.which("playwright")` finds a
  PATH-level shim, not the target repo's own CLI — resolve the target's
  `node_modules/.bin/playwright(.cmd)` first (runner `_resolve_command`).
- **Windows text-mode CRLF (S3.1):** writes in text mode gain `\r\n` → on-disk
  size ≠ bytes written; artifact-store tests assert the on-disk size.
- **mypy `isinstance` + generics (S3.1):** parameterized generics are rejected
  in `isinstance` ("cannot be used with class or instance checks") — narrow
  with plain `list`/`dict`, then index.
- **typed `ThreadingHTTPServer` extras (S3.1):** attach a capture list via a
  subclass with a class-level annotation, not a dynamic attribute (mypy
  `attr-defined`; the declared return type widens away inner-class attrs).
