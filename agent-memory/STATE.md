# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19 · per-session detail: `SESSION_LOG.md`

## 1. Current position

- **Phase:** 0–7 **complete** (S0.1–S7.5 ✓ — one line each in §2) ·
  **Phase 7 — Integrations: complete** (S7.0 ✓ step table · S7.1 ✓ GitHub core ·
  S7.2 ✓ PR→regression · S7.3 ✓ webhook · S7.4 ✓ Jira core + **API/persistence leg
  (owner-only failure→issue link, create/update/recreate, `jira_issue_key`)** ·
  S7.5 ✓ live E2E baseline)
- **next:** **Phase 8 — Commercialization** (auth/billing/teams/RBAC/deployment
  hardening — deferred until MVP validation, bible §19) · S7.5's failure→Jira-issue
  leg is now driven **live** (create→QA-1, read-back, re-link→updated), so MVP
  validation (Phase 0–7) is complete

## 2. Just completed (one line per step — full detail: SESSION_LOG.md)

- **2026-09-08 · S7.4-API — failure→Jira issue linking (the deferred leg) — complete + gated.**
  `JobType.JIRA_LINK` + `JiraLinkJobAgent` (LLM-free; create-or-update idempotent; stale
  key 404-on-update → recreate-and-relink, self-healing) · `failures.jira_issue_key`
  nullable indexed column + migration `d5a1b9c7e3f2` (**new head**) · `POST
  /projects/{id}/failures/{failure_id}/jira` (owner-or-above; 403 non-member/unknown
  never-404 · 409 no Jira integration · 404 failure-not-in-project; body validated before
  side effects; 202 + `Location`) · `jira.issue` SSE (`action`/`key`/`url`/`project_key`)
  · `FailureOut.jira_issue_key` exposed on the failure read model ·
  `tests/unit/test_s74_jira_link.py` **18 tests** (real agent via in-process `FakeJira`;
  RBAC/validation/config/scoping/202/create/update/recreate/token-leak/read-model/
  agent-events) · gates green (ruff check+format, mypy strict 160 files, **914 passed**)
  · `a7ba016`.
- **2026-09-08 · S7.5 live E2E + baseline report — complete + gated.**
  `scripts/_s75_live.py` + `_s75_seed.py` (S6.5 evidence pair) — signed HMAC
  webhook → `regression_analysis` job (202+Location) → `regression.set` SSE →
  "Run this set" through S3 → Playwright **1/1** · fake GitHub (PAT-checked) +
  changed-file pair (`e2e/fixtures.js`+`e2e/demo.spec.js`) → impact
  **direct+generated+referenced** · live LLM advisor (LM Studio) ·
  **baseline `reports/integrations_v1.json` committed** (29/29 checks pass;
  `.gitignore` exception per S6.5) · **S7.4 Jira leg driven LIVE** (create→QA-1,
  read-back, re-link→updated; see SESSION_LOG 2026-09-08) · fixed driver
  stdout-PIPE SSE stall (API now → `logs/api_s75.log`) + pre-existing
  mypy in `test_jira_client.py`/`test_s73_webhook.py` · gates green (ruff, mypy
  strict 159 files, **896 passed**) · `38405d2` + `baa0271` (Jira leg live).
- **2026-09-06 · S7.4 Jira core (LLM-free) — complete + gated.**
  `qa_copilot_integrations.jira` — typed Jira REST v2 client (create/update/fetch issue;
  `JiraAuthError` 401/403 · `JiraNotFoundError` 404 · §17 token redaction) · deterministic
  failure→issue mapping (fixed `Bug` type, `[QA]` summary prefix, stable labels, ADF
  description from the S4.1 diagnosis) · golden `jira_v1.json` **8/8, score 1.0** ·
  `FakeJiraServer` runner · CLI `map|golden` · **61 hermetic tests** · ruff/mypy clean.
  **API/persistence half deliberately NOT built (user decision):** no Jira route, no
  `jira_link` job, no `failures.jira_issue_key` column/migration — verified nothing
  half-wired. **Deferred design call:** persisted key + Jira 404 on update →
  recreate-and-relink vs. stale-link fail. Scratch `scripts/tmp_s74_{sanity,gen_payload,
  gen_golden}.py` — awaiting user delete call.
- **2026-09-05 · S7.3 CI/CD webhook** — `POST /api/v1/webhooks/github`, HMAC-SHA256
  `X-Hub-Signature-256` **is** the auth (secret via env var named in the project's
  `integration_configs` row, provider `github_webhook`) → `pull_request`
  opened/synchronize → `regression_analysis` job (202) → `regression.set` SSE ·
  `webhook_events` delivery dedupe · 409 no-match / 400 no PR number · workflow template
  `infra/github/workflows/qa-copilot.yml` · 14 tests, **835 green** · `ab5f87a`.
- **2026-09-05 · S7.2 exit tests** — PR input → 202 → `regression.set` with PR-derived
  impact: 3 tests added to `tests/unit/test_regression_analysis.py` (now 18;
  `pull_request` + second source → 422 · no GitHub integration → 409 · happy path 202 +
  PR-derived set; `FakePrGitHub`, PAT asserted absent from body/job/SSE) · **821 green**
  (818+3) + pending `ruff format` fixes committed. (S7.2 itself shipped 2026-09-02,
  `a9fd1`: `pull_request` as third exclusive analyze source + web PR input.)
- **2026-09-02 · S7.1 GitHub core (LLM-free)** — typed `GitHubClient` (repo resolve, PR
  files with `Link` pagination, PAT redacted from all errors/messages) · golden
  `github_v1.json` **10/10** · `integration_configs` PAT-safe API (env-ref only, token
  never stored/echoed) + migration `9f3c5d7a1b2e` · `test_s71_no_llm.py` import pin ·
  55 targeted tests, **797 green** · fixed real redaction-regex bug (`\b([?&]token=)`).
- **2026-09-02 · S7.0 Phase 7 defined** — bible §19 step table (S7.1–S7.5) drafted +
  approved; stance: integration cores deterministic + LLM-free · PR files = S6.1
  `files[]` input · out of scope: other providers, OAuth, Jira sync beyond failure-linking.
- **2026-09-02 · S6.5 live regression E2E + baseline** — live E2E **38/38** (analyze →
  202 → `regression.set` SSE → run set → S3 Playwright 1/1) · evidence pair
  `scripts/_s65_live.py` + `_s65_seed.py` · **baseline `reports/regression_v1.json`
  committed** (Phase 6 closeout).
- **2026-09-01/02 · S6.4 regression API + web** — `POST /projects/{id}/regression/analyze`
  (202+job) → `regression.set` SSE + Regression tab ("Run this set" via S3) · `b3fc68c`.
- **2026-09-01 · S6.3 recommender (deterministic top-N)** — impact × history × risk →
  ranked recommendations; golden `packages/ai/golden/regression_v1.json` (6 fixtures);
  gate `python -m qa_copilot_ai.regression` (LLM advisor + deterministic stub fallback) ·
  24 tests, **727 green**.
- **2026-09-01 · S6.2 flaky + risk core (LLM-free)** — `qa_copilot_repository.history`:
  flaky/failing flags (MIN_SAMPLE=3, RECENT_WINDOW=5, FLAKY=0.25, FAILING=0.50) + bounded
  monotonic risk score (impact direct 40 / generated 25 / referenced 15 · failure 30 ·
  flaky 20 · requirement risk 10 · test-case priority 5/2) + `rank_tests` · 18 tests.
- **2026-09-01 · S6.1 change-impact core (LLM-free)** — `qa_copilot_repository.impact`:
  direct / generated / referenced (JS+Python static imports, `data-testid` vocabulary) ·
  pure core (no DB/git/network/LLM) + ORM seam + CLI (`--changed` / `--range`) ·
  40 tests over real sample repos.
- **2026-09-01 · Phase 6 defined** — bible §19 Regression Intelligence step table +
  S6.0 contract, approved.
- **2026-08-31 · S5.5 Ask API + web Q&A view** — knowledge Q&A endpoint + web view;
  live E2E green.
- **2026-08-30 · S5.4 RAG Q&A agent** — retrieve + answer with citations; live gate passed.
- **2026-08-30 · S5.3 Knowledge API + web** — knowledge endpoints + web tab; live E2E passed.
- **2026-08-30 · S5.2 embeddings / vector seam** — gateway embedding call site + vector
  pipeline (no local embedding model → `VECTOR_DIM` stays 1536 placeholder);
  httpx NaN-body guard.
- **2026-08-30 · S5.1 deterministic knowledge core** — chunking + evidence capping;
  golden gate **13/13**.
- **2026-08-30 · S4.3 Approve → re-run loop** — diagnosis approval gates re-run;
  live full-loop E2E PASSED.
- **2026-08-30 · S4.2 Fix Agent** — fix suggestions from diagnosis; live gate **8/10**
  (target ≥ 5/10), 8/8 applicable.
- **2026-08-29 · S4.1 Failure Investigator** — failure → diagnosis (LLM); live **30/30**.
- **2026-08-29 · S3.3 failure normalizer** — deterministic normalizer; golden **30/30**.
- **2026-08-29 · AI settings centralization** (`01a2851` + `d886b69`) — per-agent
  `ai_config` rows (base_url/model/params) + `ai_call` audit writer.
- **2026-08-28 · S3.2 Runs API + Runs UI** — `GET /projects/{id}/runs` + runs page.
- **2026-08-28 · S3.1 execution worker** — job orchestration → Playwright adapter →
  results persisted; live exit PASS.
- **2026-08-28 · S2.4 generated-test review** — apply/reject state machine + routes
  (`d52b8f7`).
- **2026-08-28 · S2.3 automation agent** — test cases → Playwright code (LLM);
  live gate PASS.
- **2026-08-28 · S2.2 conventions extractor** — deterministic test conventions (locator
  styles, page objects, `data-testid` vocab, test scripts) (`c9d41f2`).
- **2026-08-28 · S2.1 repo scanner** — deterministic `RepositoryProfile` (languages,
  frameworks, monorepo, test structure) (`aa47408`).
- **2026-08-27 · S1.4 Eval** (`74a733d`) — `qa_copilot_ai.eval` CLI + 12-fixture
  `packages/ai/golden/golden_v1.json` (S1.2/S1.4 shared truth); §31.7 gates 0.99/0.85.
- **2026-08-27 · S1.3 UI flow + persistence** (`8c0ed5b`/`022fb6b`) — web shell on the
  real API (login, 202+SSE, read-back); suite → §10 rows; prompt capped ≤ 6 cases for
  Qwen-27B's output budget.
- **2026-08-27 · S1.2 Test Design Agent** (`bb5bb2f`) — 10 fixtures → schema-valid +
  step coverage ≥ 85% vs oracle.
- **2026-08-27 · S1.1 Requirement Agent** (`6a1bf88`) — 10/10 schema-valid on S0.9 seam.
- **2026-08-27 · S0.9 jobs API** (`2051749`) — 202 + `GET /jobs/{id}` + SSE `/events`
  (15s heartbeat) + `JobAgent`/`StubAgent` seam + state machine + reaper.
- **2026-08-27 · S0.10 demo app v0** (`ai-qa-copilot-demo-app`, `43739a5`) — Express +
  better-sqlite3 + React; user `qa`/`qa1234`; smoke 11/11 · defects 7/7.
- **2026-08-26/27 · S0.1–S0.8** — monorepo (uv+pnpm) · compose infra (PG16+pgvector
  :5433, Redis) · FastAPI · domain (pydantic v2) · SQLAlchemy+Alembic+seed · AI gateway
  (LM Studio live) · React shell · JWT + project-scoped RBAC.

## 3. NEXT STEP (start here)

**S7.5 is complete and gated** (commit `38405d2`) — the live webhook → regression →
S3-run baseline is green and `reports/integrations_v1.json` is committed. Its Jira
issue-linking leg (deferred 2026-09-06) is now **built** — S7.4-API, commit `a7ba016`.

**Phase 8 — Commercialization** (bible §19; deferred until MVP validation):
auth, billing, teams, RBAC, deployment hardening.

The formerly-deferred **S7.4-API Jira leg is now complete** (`a7ba016`, 2026-09-08):
- `POST /projects/{id}/failures/{failure_id}/jira` (202 + job; owner-or-above) ✓
- `jira_link` job + `failures.jira_issue_key` (nullable column + Alembic migration
  `d5a1b9c7e3f2`) ✓
- the failure read model exposes `jira_issue_key` ✓
- 404-stale-link decision: **recreate-and-relink** (self-healing; the stored key always
  resolves to a live issue) — implemented ✓

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- Docker **running** (Desktop, per-user; CLI v29.7.2, Compose v5.4.0; on the **USER**
  PATH — refresh `$env:Path` in long-lived shells)
- **Infra up:** `qa-copilot-db` pgvector/pg16 **0.0.0.0:5433→5432** (qa/qa @ qa_copilot)
  · `qa-copilot-redis` :6379 · named volumes
- Native PG16 service `postgresql-x64-16` running on 5432 (user decision; no pgvector)
- **Node:** `v22.23.2` (LTS; `%LOCALAPPDATA%\hermes\node`, first on user PATH) + backup
  `v24.19.0` · `npm 12.0.2` · `pnpm 11.24.0`
- **Web toolchain (S0.7):** pnpm 11 workspace at repo root · `apps/web` = React 18.3 +
  Vite 6.4 + TS 5.8 (strict) + Tailwind 4 · ESLint 9 (flat) + Prettier 3
- Toolchain in `.venv`: ruff 0.16.4 · mypy 2.3.1 · pytest 9.1.1 · pre-commit 4.6.2
- LLM (verified S0.6): **LM Studio (llama.cpp) `http://localhost:8080/v1`** ·
  Qwen3.8-27B-Q4_K_M (27.3B, n_ctx 100,096) · completion-only (no local embedding model
  → `VECTOR_DIM` stays 1536 placeholder) · §31.1 budgets hold
- python-docx ✗ — Markdown is the doc source of truth

## 5. Key decisions (full list: build bible §29)

- Local LLM only, no cloud · hard context budgets · text-first failure analysis ·
  async jobs mandatory (202 + SSE — local inference is slow)
- One step per session · verify exit criterion · commit `step S#.x` · update this file ·
  Markdown is canonical; do not re-derive decisions already in §29
- Keep native PG16 on 5432; compose db on 5433 (S0.2, user decision)
- S0.4 domain: pydantic v2 · `StrEnum` wire strings · `extra="forbid"` · ids `str | None`
  (server-assigned) · `Project.repository_id` (bible §10's `repo_id` — clearer name kept)
- S0.5 repo: SQLAlchemy 2.0 typed ORM · `sa.Uuid(as_uuid=False)` → `str` · enums stored as
  plain `VARCHAR(32)` of domain wire strings · Alembic URL centralized in
  `qa_copilot_repository.db` · pgvector enabled in migration, **not dropped on downgrade**
  · seed idempotent (natural-key lookups + deterministic `uuid5`)
- S0.6 AI: gateway is the **only** LLM call site (§31.1) · one retry on transport errors
  only, hard `LLMError` otherwise (no silent model-swap) · redaction before wire + logs
  (§31.7) · `usage` from server with char-estimate fallback · prompt rendering fails
  loudly on missing variables · `ai_actions` payload = the `ai_call` log record
- S0.8 auth: dev-mode JWT HS256 + PBKDF2-SHA256 (stdlib) · `AUTH_TOKEN_SECRET` REQUIRED,
  no code fallback · RBAC is **project-scoped** via `project_members.role`
  (`users.role` default only) · role check precedes project lookup (no existence leak) ·
  login dummies the hash verify for unknown users (timing)
- S0.9 jobs: `JobAgent` protocol is the **only** replaceable seam (S1.x swapped in LLM
  agents without API changes) · `StubAgent` = deterministic placeholder ·
  queued→running→completed|failed state machine (illegal edge →
  `InvalidJobTransition`) · `job.started` emitted before the agent runs · SSE bus
  in-process pub/sub (multi-worker → Redis)
- S2.1/S2.2: scanner + conventions extractor stay **deterministic/LLM-free** · shared
  contracts (`RepositoryProfile`, `TestConventions`) live in the **domain** package ·
  source files classified by name only, never read · test-tree name-gated (`tests` dirs
  only) · `data-testid` from quoted attribute usage only · `package.json` scripts
  filtered to test-related commands

## 6. Pointers (paths only — no code here)

- Build bible: `docs/AI_QA_Copilot_Build_Bible_v1.1.md` · session history:
  `agent-memory/SESSION_LOG.md` · v1.0 PDF (historical):
  `c:\Users\manve\Desktop\AI_QA_Copilot_Build_Bible.pdf`
- Demo app (S0.10): `c:\Users\manve\Workspace\ai-qa-copilot-demo-app` (separate repo;
  `pnpm dev`/`smoke`/`defect-check` · user `qa`/`qa1234` · server :4000)
- Domain: `packages/domain/src/qa_copilot_domain/{base,enums,entities}.py` · ORM:
  `packages/repository/src/qa_copilot_repository/models.py` (18 core tables — new tables
  must be added to `tests/unit/test_repository.py` `EXPECTED_TABLES`) · migrations
  `infra/migrations/` (initial `60fa1027d8d2`) · DB URL `.../repository/db.py`
  (env → `.env` → default)
- API: `apps/api/src/qa_copilot_api/` — `app.py` (factory) · `auth.py` (hash/JWT/deps) ·
  `jobs.py` (JobAgent/StubAgent/bus/`sse_stream`/reaper + all `*JobAgent`s) · `routes.py`
  · `schemas.py` · `config.py` · `main.py` (agent wiring) · tests `tests/unit/`
- Web: `apps/web/src/` — `lib/{api,pipeline}.ts` (Bearer fetch + SSE reader) ·
  `hooks/{useAuth,useJobEvents}.ts` · `components/{LoginForm,PipelineView,RequirementForm,
  RequirementHistory,TestCaseList,GeneratedTests,RunsView,ProjectKnowledge,
  RegressionAnalysis,EventLog,Header}.tsx` · `scripts/e2e_s13.py` (S1.3 chain E2E)
- Agents (`packages/ai/src/qa_copilot_ai/agents/`): `requirement.py` (S1.1) ·
  `test_design.py` (S1.2) · `automation.py` (S2.3) · `failure_investigator.py` (S4.1) ·
  `fixer.py` (S4.2) · `knowledge_qa.py` (S5.4) · `regression_advisor.py` (S6.3) · prompts
  `packages/ai/prompts/*.v1.md` · eval `packages/ai/src/qa_copilot_ai/eval/` (S1.4) ·
  regression runner `packages/ai/src/qa_copilot_ai/regression/` (S6.3) ·
  `packages/ai/src/qa_copilot_ai/{gateway,prompts,redaction}.py` · live check
  `scripts/llm_live_check.py` · `ai_actions` writer `qa_copilot_repository.audit`
- Repository cores (`packages/repository/src/qa_copilot_repository/`): `scanner.py` (S2.1)
  · `conventions.py` (S2.2) · `generated_tests.py` (S2.4) · `impact.py` (S6.1) ·
  `history.py` (S6.2) · `regression.py` (S6.3) · `membership.py` (S0.8) · sample repos
  `packages/repository/samples/sample_repos/{js-web-app,python-api,js-monorepo}`
- Goldens: `packages/ai/golden/golden_v1.json` (S1.2/S1.4) ·
  `packages/ai/golden/regression_v1.json` (S6.3) ·
  `packages/integrations/golden/{github_v1,jira_v1}.json` (S7.1/S7.4)
- Regression S6.5 evidence pair: `scripts/_s65_live.py` (38 assertions) +
  `scripts/_s65_seed.py` · baseline `reports/regression_v1.json` (tracked — the single
  `.gitignore` exception under `reports/`; re-run after any prompt/model/regression-core
  change and diff the report)
- Integrations (S7.1–S7.4): `packages/integrations/src/qa_copilot_integrations/` —
  `github/` (S7.1) · `jira/` (S7.4 core) · `webhook.py` (S7.3) · `secrets.py` (§17
  redaction) · CLI `uv run python -m qa_copilot_integrations.{github,jira} <sub>`
  (github: `repo|pr-files|golden` · jira: `map|golden`) · S7.1 config API
  `apps/api/src/qa_copilot_api/{routes,schemas}.py` (`.../integrations`) ·
  **S7.4 Jira = core only** — API/persistence deferred 2026-09-06 (see §2 2026-09-06
  entry + §3

## 7. Open questions / gotchas

**Tooling / environment**
- PowerShell: `Set-Content -Encoding utf8` writes a **BOM** (S7.3's first commit `c6413e0`
  was BOM'd → amended; final `ab5f87a`) — write commit-message files via Python /
  `utf-8-sig` read · backtick-`n` in PS replacement strings inserts literal `` `n ``
  (repair with Python) · any child stderr makes the shell report "NativeCommandError" —
  judge by actual output, not `$LASTEXITCODE` · `git --no-pager` · background jobs via
  `Start-Process` + `Out-File` + append `"EXIT=$LASTEXITCODE"`.
- docker CLI is on the USER PATH — old terminals don't see it; refresh `$env:Path`.
- pnpm 11: the `pnpm` field in `package.json` is **IGNORED** — `onlyBuiltDependencies`
  (esbuild, better-sqlite3) + `allowBuilds` go in `pnpm-workspace.yaml`; a parser must
  read only list items under the top-level `packages:` key (regression test exists).
- Vite dev binds `[::1]:5173` — use `http://localhost:5173`, not `127.0.0.1:5173`.
- API health is `GET /health` at the root (`http://127.0.0.1:8000/health`), not
  `/api/v1`.
- `tests/unit/test_integrations_api.py` **hangs without live Postgres on :5433** → run
  targeted tests until PG is up (S7.x Jira/GitHub unit tests are hermetic and pass
  without it).
- **Subprocess SSE stall (S7.5):** spawning the API with `subprocess` `stdout=PIPE`
  without draining it → pipe backpressure blocks the worker → the SSE leg never
  reaches `job.completed` (looks like a hang, but the worker is fine). Redirect the
  spawned API's stdout/stderr to a log file (`logs/api_s75.log`) — never an undrained
  PIPE for a long-lived server subprocess.
- **Impact `referenced` kind (S7.5/S6.5):** a test is `referenced` only if it imports a
  *changed* source file or uses a `data-testid` defined in a changed file — the changed
  set must include the imported module (`e2e/fixtures.js`), not just the spec
  (`e2e/demo.spec.js`).

**Python / tests**
- ruff isort: `qa_copilot_*` is **not** first-party (src-layout workspace) — sorts in the
  third-party block · `ruff format` is repo-wide — mechanical wrap fixes outside the step
  belong in the step commit.
- Non-test classes named `Test*` need `__test__ = False` or pytest warns
  (`TestCase`, `TestSuite`, `TestDesignInput`, `TestConventions`, `TestScript`, ...) ·
  `test_repository.py::test_all_core_tables_registered` asserts `== EXPECTED_TABLES` —
  adding a model without the table entry fails the full suite (caught in S7.1).
- pydantic v2: `Field(strip_whitespace=True)` is v1 — use
  `Annotated[str, StringConstraints(...)]` · mypy strict + pydantic: wire-string /
  negative cases go through `model_validate` (typed constructors are arg-checked) ·
  pydantic-settings private `_env_file` kwarg invisible to mypy →
  `# type: ignore[call-arg]`.
- Env leak → wrong agent (S1.1): `get_database_url()` → `_load_dotenv()` injects `.env`
  LLM keys into `os.environ` even in tests → stub-contract tests must pass
  `llm_base_url=None, llm_model=None` (init kwargs beat env).
- SQLAlchemy/Alembic: `metadata_` not `metadata` (reserved) · delete children before
  `db.delete(parent)` · `engine.dispose()` (ALL engines) before `DROP DATABASE` ·
  fixtures seed real UUIDs (`uuid5`) · migrations import `pgvector.sqlalchemy` by
  attribute (add top-level `import pgvector.sqlalchemy`) · ruff B023: bind loop vars in
  factory lambdas.
- mypy strict: `dict[str, object]` values need `cast(int, ...)` · argparse handlers:
  annotate `handler: Callable[[argparse.Namespace], int]` before `return handler(args)`
  · `is_dataclass()` narrows to class OR instance — guard `isinstance(x, type)` before
  `asdict(x)` · `ThreadingHTTPServer.server_address` typed `tuple|str|Buffer` — guard
  with an `isinstance(addr, tuple)` helper returning `(str, int)`.
- httpx refuses to JSON-encode `float("nan")` in request bodies — post a raw text body
  for NaN fixtures.

**Domain contracts / LLM**
- Oracle gate (S1.2): the oracle is the independent reference — when a fixture missed
  the 85% step-coverage gate, extend the fake model output (it stands in for a competent
  LLM), never trim the oracle to fit.
- Local-model output budget: LM Studio silently truncates at the requested `max_tokens`
  → mid-JSON EOF → loud schema failure; `test-designer.v1.md` caps ≤ 6 cases · Qwen3
  thinking mode spends the whole budget in `reasoning_content` → empty `content`; fix via
  gateway `extra_body` / CLI `--extra-body`
  (`chat_template_kwargs.enable_thinking=false`) — **not** by raising `max_tokens`.
- S5.1: history evidence capped to the FIRST 2 lines (200 chars/line) · chunking
  hard-cuts any word longer than `max_chars`.
- S6.5: live-run rows have `test_case_id NULL` — flaky/fail→pass evidence comes from
  seeded `test_results` history (`_s65_seed.py`) · `regression.set` impact entries carry
  `path` (not `test_key`); ranked `recommendations[]` carry `test_key` — don't mix the
  two shapes · `started_at NULL` rows sort by `created_at` in `project_test_history` —
  assert the observed order, never insertion order · domain `TestHistoryStats` has no
  `last_status`/`insufficient_samples` — API serialization adds them.
- S7.1 PAT redaction: `\b([?&]token=)` missed `?token=` at line start / after a space
  (`\b` is not a boundary there) — drop the `\b` · goldens pin PAT-shaped secrets echoed
  in bodies, not one fixed token literal.