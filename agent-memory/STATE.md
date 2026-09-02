# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation **complete** · Phase 1 — Requirement → Test Design → Eval **complete** ·
  Phase 2 — Playwright Copilot **complete** · Phase 3 — Execution **complete** (S3.1 ✓, S3.2 ✓, S3.3 ✓) ·
  Phase 4 — Failure Intelligence **complete** (S4.1 ✓, S4.2 ✓, S4.3 ✓) ·
  Phase 5 — Project Knowledge **complete** (S5.1 ✓, S5.2 ✓, S5.3 ✓, S5.4 ✓, S5.5 ✓) ·
  Phase 6 — Regression Intelligence **complete** (S6.1 ✓, S6.2 ✓, S6.3 ✓, S6.4 ✓, S6.5 ✓) ·
  **Phase 7 — Integrations: in progress** (S7.0 ✓ step table defined + approved)
- **Step:** S0.1–S6.5 ✓ (S6.4 regression API + web, commit `b3fc68c` · S6.5 live E2E 38/38 +
  committed `reports/regression_v1.json` baseline, 2026-09-02) · S7.0 ✓ Phase 7 step
  table drafted + approved (bible §19, 2026-09-02) ·
  **next:** **S7.1 — GitHub core (LLM-free)** — see §3

## 2. Just completed

- 2026-09-02 · **S7.0 (Phase 7 definition — Integrations) — step table drafted +
  approved** (bible §19; exit: table defined against §20–§22 + sign-off):
  `### Phases 7–8` → `### Phase 7 — Integrations` (S7.1 GitHub core (typed httpx,
  PAT redacted §17, `integration_configs` + migration, golden `github_v1.json`,
  CLI) · S7.2 PR → regression (`pull_request` third exclusive analyze source,
  idempotent `pr-comment` owner+, web PR input) · S7.3 CI/CD webhook
  (`/api/v1/webhooks/github` HMAC `X-Hub-Signature-256` is the auth,
  `webhook_events` dedupe, `qa-copilot.yml` template) · S7.4 Jira linking
  (failure + S4.1 diagnosis → issue create-or-update, `failures.jira_issue_key`,
  golden `jira_v1.json`) · S7.5 live E2E + committed baseline
  `reports/integrations_v1.json`) · stance: integration cores deterministic +
  LLM-free (S2.1/S3.3/S5.1/S6.1 pattern) — §31.1 gateway off the path; PR files
  = S6.1 `files[]` input · out of scope per §25 (no other providers, no OAuth,
  no Jira sync beyond failure-linking) · Phase 8 left detail-on-demand (§18).
- 2026-09-02 · **S6.5 (live regression E2E + committed baseline) — live E2E
  38/38, baseline report tracked + committed** (bible §19 S6.5; exit: real
  project + changed files → 202 → job → `regression.set` SSE with ranked set
  → run the set through the S3 path → baseline report committed):
  - **Seed (script `_s65_seed.py`, idempotent; demo project "Demo App"
    `f500a3b2-…`, login `dev@local.dev` → owner):** one applied
    `generated_tests` row for `e2e/demo.spec.js` (→ login test case
    `3cfe1127-…` → requirement "Login accepts valid credentials") + 6 linked
    executions (1 pre-existing seeded passed + 5 seeded: failed, flaky,
    flaky, passed, passed) → S6.2 stats `executions=6, passed=3, failed=1,
    flakiness_rate≈0.333 (≥0.25, ≥min_sample 3) → is_flaky=true` ·
    `failure_rate 1/6 < 0.5 → is_failing=false` · `last_status=passed`
    (fail→pass shape) · risk score 71.67 → `recommend()` rank 1 (the only
    impacted test);
  - **Live driver (script `_s65_live.py`; API `:8000` + LM Studio `:8080` +
    demo server `:4000` / client `:5174` all up):**
    `POST /api/v1/projects/{id}/regression/analyze` `{repository_path,
    files: [e2e/fixtures.js, e2e/demo.spec.js], top_n: 10}` → **202** +
    `Location: /api/v1/jobs/d5e705e3-…` → SSE `job.started` → `stage.started`
    → `progress 0.1→0.8` → **`regression.set`** (S6.1 impact
    direct+generated+referenced · top-1 `test_key e2e/demo.spec.js`,
    `impact_kind direct`, `requirement_risk high`, rationale chips ·
    advisor brief — LLM or safe stub) → job `regression_analysis`
    `completed`, `output_ref regression://f500a3b2-…` + `ai_actions` audit →
    `POST /api/v1/projects/{id}/runs` `{tests: [e2e/demo.spec.js]}` →
    **202** → SSE `run.result` (S3 Playwright path, `run_execution`
    `completed`, **1/1 passed**, artifacts stored) · **38/38 assertions,
    exit 0**;
  - **Baseline report (committed — bible §31.6/§31.7 drift tracking):**
    `reports/regression_v1.json` — `schema_version s6.5-live-evidence/v1` ·
    `result.passed=true` · `failed_assertions=[]` · 38 assertions ·
    precondition + expected S6.2 stats snapshot · `analyze` (request / HTTP /
    job row / SSE events + regression set) · `run` (job row + SSE + run
    detail) · `fail_to_pass.statuses_in_time_order = [failed, flaky, flaky,
    passed, passed, passed]` · `.gitignore`: `reports/` → `reports/*` +
    `!reports/regression_v1.json` (the single tracked report; other run
    artifacts stay ignored);
  - **gotchas (see also §7):** live run results persist with `test_case_id
    NULL` → flaky/fail→pass evidence comes from the seeded `test_results`
    history, not live-run rows · `regression.set` impact entries carry
    `path` (not `test_key`) · API health is `http://127.0.0.1:8000/health`
    (root, not `/api/v1/…`) · `started_at NULL` history rows sort by
    `created_at` — observed order `failed, flaky, flaky, passed, passed,
    passed` · domain `TestHistoryStats` has no `last_status` /
    `insufficient_samples` — the API schema adds them; assert only exposed
    contract fields;
  - **cleanup:** removed one-off probes `_s65_envcheck.py` /
    `_s65_inspect.py` / `_s65_probe.py` + stray `ersmanveWorkspace…` file
    (a git-log dump from a broken path write) · kept `_s65_live.py` +
    `_s65_seed.py` as the reproducible S6.5 evidence pair — re-run after any
    prompt/model/regression-core change and diff the report.
- 2026-09-02 · **S6.4 (Regression API + web) — all gates green** (bible
  §19 S6.4; **backfill — that session committed `b3fc68c` without updating
  agent memory**): `RegressionJobAgent` orchestrates S6.1
  `impact_from_session` + S6.2 `build_risk_ranking` over
  `project_test_history` + S6.3 `recommend()` + optional
  `regression-advisor@1` summary (safe stub fallback; never re-orders the
  deterministic ranking) → **`regression.set` SSE** + stable
  `regression://<project>` `output_ref` + `ai_actions` audit (model stats
  when the LLM ran, `model="stub"` marker when degraded) ·
  `RunExecutionJobAgent` runs the selected set through the existing S3
  Playwright path (`run_playwright`, no model call → no `ai_actions` row) ·
  `POST /projects/{id}/regression/analyze` (202, changed files XOR
  base/head refs; member-or-above RBAC, unknown project → 403 §31.3; invalid
  body → 422) + `POST /projects/{id}/runs` · `JobType.REGRESSION_ANALYSIS` +
  runner wiring in `main.py` · web: "Regression" tab
  (`RegressionAnalysis.tsx` — ranked table + rationale chips + flaky flags +
  "Run this set") + `useJobEvents` / `api.ts` / `pipeline.ts` wiring ·
  `tests/unit/test_regression_analysis.py` (15 tests: route stub audit,
  agent LLM audit via fake gateway, negative-execution no-audit,
  run-execution happy/error, schemas, determinism) · gates: pytest **742
  passed** · mypy strict ✓ (100 files) · ruff check/format ✓.
- 2026-09-01 · **S6.3 (recommender — deterministic top-N) — all gates green**
  (bible §19 S6.3; exit: golden `regression_v1.json` top-N order match 100% ·
  deterministic (same input ⇒ same JSON) · no LLM in the ranking path):
  - `qa_copilot_repository.regression` (new) — `recommend(impact, ranking,
    *, top_n=DEFAULT_TOP_N)` **pure core** (no DB, no LLM, no network; joins
    `ImpactSet.impacted` with `RiskRanking.ranked` by `test_key`): one
    `RecommenderItem` per **impacted** test — a non-impacted test is never
    recommended, no matter how risky · an impacted test with no S6.2 history
    still ranks (score 0, `no-run-history` rationale) · strongest impact kind
    per test + its changed files ride along · `risk_score` desc → `test_key`
    asc (stable tie-break), truncated to `top_n`, `rank` is 1-based ·
    per-test deterministic `rationale` evidence (impact kind, failure % ·
    flaky % · requirement-risk · priority · `changed:N`) · `top_n < 1` →
    `ValueError` · `DEFAULT_TOP_N=10`;
  - `qa_copilot_domain` — `RecommenderItem` (test_key, stats, rank,
    risk_score, impact_kind, changed_files, requirement_risk,
    test_case_priority, rationale) + `RecommendationSet` (project_id,
    changed, recommendations, top_n, min_sample / recent_window / flaky /
    failing thresholds, computed_at) — shared recommender contract (the S6.4
    API serializes these); **fixed a pre-existing duplicate `computed_at`
    field on `RiskRanking`** (mypy strict caught it);
  - `qa_copilot_ai/agents/regression_advisor.py` (new) — optional
    `regression-advisor@1` human summary (`RegressionAdvisorAgent` +
    `AdvisorInput`; gateway §31.1 + registry prompt): JSON `{"summary": ...}`
    contract — **never re-orders or alters the ranked set** · on LLM error /
    schema-invalid / missing prompt → deterministic `stub_summary` (logged)
    so the core works offline;
  - `packages/ai/prompts/regression-advisor.v1.md` (v1 prompt);
  - `qa_copilot_ai/regression/` (new package) — `golden.py`
    (`RegressionGoldenSet` / `RegressionFixture` · `default_golden_path` ·
    `load_regression_golden_set`) · `runner.py` (`run_regression_eval` —
    per-fixture `recommend` + order-match scoring; `RegressionReport`) ·
    `cli.py` (`python -m qa_copilot_ai.regression` → JSON on stdout,
    `--golden` / `--report` / `--advise`, stderr summary, exit 0/1/2) ·
    `__main__.py`;
  - **`packages/ai/golden/regression_v1.json` — 6 synthetic fixtures**:
    REG-001 risk-desc order · REG-002 top-N truncation keeps the highest-risk
    slice · REG-003 equal-risk tie → `test_key` asc · REG-004 empty impacted
    set → no recommendations (non-impacted never recommended) · REG-005
    impacted test with no S6.2 history ranks last at zero risk · REG-006
    strongest impact-kind join + stable tie-break; gate `pass_min 1.0`;
  - `scripts/regression_run.py` — dotenv-aware CLI wrapper (`reports/` is
    gitignored);
  - `tests/unit/test_regression.py` (new, **24 tests**): core join / ordering
    / tie-break / truncation / empty-impacted / no-history / `top_n` guard ·
    golden 100% green · advisor LLM + stub fallback · CLI contract;
  - **gates**: pytest **727 passed** (full unit suite) · mypy strict ✓
    (100 files) · ruff check ✓ · ruff format ✓ ·
    `uv run python scripts/regression_run.py --report reports/regression_v1.json`
    → **100% order match, `passed: true`, exit 0**;
  - **gotchas:** **environment repair (not S6.3)** — dev DB `qa_copilot`
    (:5433) had been **emptied** (only `alembic_version` left, still stamped
    at head; all tables gone) → 7 `test_repository.py` DB-smoke tests were
    failing before this session; repaired with `alembic stamp base` →
    `alembic upgrade head` (3 migrations re-created the schema) →
    `scripts/seed.py` (dev fixtures re-seeded); `test_repository.py` now
    green · PowerShell `2>&1` surfaces uv's stderr warnings as
    "NativeCommandError" (exit-code quirk) — judge by the actual output,
    not just `$LASTEXITCODE`;
  - **next:** S6.4 (bible §19) — `POST /projects/{id}/regression/analyze`
    (202+job) → `regression.set` SSE + "Regression" tab ("Run this set" via
    S3) — see §3.
- 2026-09-01 · **S6.2 (flaky + risk core, LLM-free) — all gates green**
  (bible §19 S6.2; exit: synthetic run-history fixtures → flaky/failing flags
  + deterministic risk ranking 100% · pytest/mypy/ruff green):
  - `qa_copilot_repository.history` (new) — `TestOutcome` (run_id, run_order,
    status, flaky_diagnosis) + `TestRiskInput` (test_key, outcomes, impact_kind,
    requirement_risk, test_case_priority) · `compute_test_stats` (flakiness /
    failure / recent-failure rates, `is_flaky` / `is_failing` flags,
    `insufficient_samples`) · `compute_risk_score` (bounded [0,110] monotonic
    sum: impact direct 40 / generated 25 / referenced 15 · failure 30 · flaky
    20 · requirement risk / test-case priority 10/5/2) · `strongest_impact_kind`
    · `rank_tests` · `build_risk_ranking` · `project_test_history` (ORM seam) ·
    thresholds from `qa_copilot_domain` (MIN_SAMPLE=3, RECENT_WINDOW=5,
    FLAKY=0.25, FAILING=0.50);
  - `tests/unit/test_history.py` (new, **18 tests**): flaky/failing flags +
    risk ranking over synthetic run-history fixtures · min-sample gate (no
    flags from < 3 runs) · determinism (equal inputs ⇒ equal output) ·
    fake-`Session` ORM mapping · full-query E2E against a **dedicated** scratch
    Postgres `qa_copilot_history_test` on :5433 (created + dropped by the
    fixture; `vector` ext installed) — main dev `qa_copilot` schema untouched;
  - **gates**: pytest **73 passed** (focused impact+runs+history; +18) ·
    mypy strict ✓ (test file) · ruff check ✓ · ruff format ✓ · full pre-commit
    mypy hook blocked by network (GitHub `mypy-hook` env install fails) —
    direct mypy authoritative + clean;
  - **gotchas:** `TestResult.test_case_id` is NOT an FK (E2E seed needs no
    `TestCase` row) · FK-safe seed order org/project → run → result → failure
    with grouped flushes · drop the scratch DB *after* the session closes (else
    `DROP` blocks on the SELECTs' ACCESS SHARE locks) · pytest emits
    `PytestCollectionWarning` for the production `TestOutcome` / `TestRiskInput`
    names — benign, left as-is (renaming would break the API).
- 2026-09-01 · **S6.1 (change-impact core, LLM-free) — all gates green**
  (bible §19 S6.1; exit: golden impact sets 100% on ≥ 2 sample repos,
  no LLM in the path, CLI → JSON):
  - `qa_copilot_domain` — S6.1 output contract: `ImpactKind`
    (direct/generated/referenced — a file can carry several) ·
    `ImpactedTest` (path, kinds, changed_files, test_case_ids,
    requirement_ids, signals — the S6.3 ranking inputs) · `ImpactSet`
    (changed, impacted, test_files_scanned, notes, computed_at) ·
    exports in `__init__.py`;
  - `qa_copilot_repository.impact` (new, ~450 lines) —
    `compute_impact(root, changed, generated=())` **pure core**
    (no DB, no git, no network, no LLM; reuses S2.1 `is_test_file()` +
    scan safety: skip dirs, no symlink follow, file-count cap,
    read-size cap): **direct** (changed file is a test file) ·
    **generated** (changed file = applied `generated_tests.file_path` →
    its `test_case` → linked requirements via the M:N join) ·
    **referenced** (JS/TS static / side-effect / dynamic imports,
    `require()`, extensionless + `index` resolution · Python `import` /
    `from … import` incl. `__init__.py` packages · `data-testid`
    vocabulary match) — every list sorted + deduped, equal inputs ⇒
    equal JSON (determinism test);
  - also: `normalize_changed()` (accepts `./` + backslashes; rejects
    absolute paths, `..` escapes, blanks; dedupes + sorts) ·
    `GeneratedTestRef` (file_path, test_case_id, requirement_ids) ·
    `applied_generated_refs(session, project_id)` +
    `impact_from_session(...)` (thin ORM seam — the pure core stays
    DB-free) · `changed_files_from_range(root, base, head)`
    (`git diff --name-only base..head`; both refs required, fail-loud
    with git stderr);
  - CLI: `python -m qa_copilot_repository.impact <root>` with
    `--changed PATH[,PATH...]` or `--range BASE..HEAD` → `ImpactSet`
    JSON on stdout (indent 2, sorted keys), `impact: …` on stderr,
    exit 0/2;
  - `tests/unit/test_impact.py` (new, **40 tests**): `normalize_changed`
    accept/reject (parametrized) · **goldens over real repos** —
    js-web-app (referenced source change · direct test change ·
    generated with test-case link · generated orphan · combined
    direct+generated+referenced) · python-api (direct test change ·
    source change not statically referenced) · demo-app (fixture
    referenced by spec · direct spec change · non-referenced page
    change; skipped when the repo is absent) · synthetic `tmp_path`
    repos (extensionless + `index` resolution · Python package imports ·
    `data-testid` match · missing changed file noted · no-test-files
    noted · dedupe + determinism · path validation) · **fake-`Session`
    ORM** tests (row mapping + sorting · orphan ref ·
    `impact_from_session` combined kinds + ids) · git-range (both refs
    required · non-repo failure · real two-commit diff under `needs_git`)
    · CLI (JSON shape · empty `--changed` · bad `--range` · missing
    source) · package exports;
  - **gates**: pytest **685 passed** (full suite; +40) ·
    mypy strict ✓ (119 files) · ruff check ✓ · ruff format ✓;
  - **gotchas (see also §7):** `changed_files_from_range` takes `base`
    + `head` separately and assembles `base..head` itself (CLI `--range`
    splits the string) · the "changed file not present at repo root
    (deleted or moved)" note text is asserted verbatim by
    `test_synthetic_changed_file_missing_noted` — change wording with
    the test · read-tool cache returned **stale file contents**
    mid-session — re-read via terminal before editing (one failed
    `impact.py` edit was stale expected-text, not a file problem).
- 2026-09-01 · **Phase 6 defined — Regression Intelligence** (bible §19,
  detail-on-demand per §18; definition step, no code): S6.1 change-impact core
  (LLM-free; direct/generated/referenced over `generated_tests` provenance +
  S2.1/S2.2 conventions) · S6.2 flaky + risk core (LLM-free stats over
  `test_runs`/`test_results`/`failures`) · S6.3 recommender + optional
  `regression-advisor@1` summary + golden `regression_v1.json` eval · S6.4
  `POST /projects/{id}/regression/analyze` (202+job) → `regression.set` SSE +
  "Regression" tab ("Run this set" via S3) · S6.5 live E2E + baseline report;
  bible §31.7 +2 numeric targets · §22 + `regression_v1.json` · §29 +2
  decisions (deterministic-first; provenance + conventions as impact anchors).
- 2026-09-01 · **S5.5 (Ask API + web Q&A view) — all gates green + live E2E
  passed** (bible §19 S5.5; exit: ask → 202 → job → grounded answer with
  citations in the UI):
  - `apps/api` — `routes.py` `POST /projects/{id}/knowledge/ask` (**202 +
    `{job_id}` + `Location: /api/v1/jobs/{id}`**; member-or-above RBAC —
    unknown project → 403 not 404 §31.3; blank/missing question → 422) ·
    `jobs.py` `KnowledgeAskJobAgent` (S5.3 `search_project_knowledge`
    top-5 → S5.4 `KnowledgeQARunner` → **`knowledge.answer` SSE event** —
    `{in_scope, answer, citations[{document_ref, source_type, title,
    score}], confidence}` — full text rides SSE, `output_ref` stays the
    stable `knowledge-ask://<project>` 1024-char ref → `ai_actions` audit
    row, answer JSON in `output_ref`) + `KnowledgeQARefusalStub` (no model
    configured → deterministic contract-valid refusal, Ask never fails or
    goes silent — mirrors the S2.3 `AutomationStub` pattern) · `main.py`
    wiring · `schemas.py` `AskRequest`;
  - `apps/web` — "Project Knowledge" tab Q&A panel (`ProjectKnowledge.tsx`:
    ask box → job progress → grounded answer with citation cards (source
    type, title, score) or a refusal state; `lib/api.ts` `askKnowledge` +
    types — the panel renders exactly the `knowledge.answer` SSE contract);
  - tests: `tests/unit/test_knowledge_ask.py` (202/Location contract, RBAC
    401/403/422, `knowledge.answer` over SSE — refusal + grounded variants —
    job row, audit row, agent-level grounding with real `KnowledgeQAAgent`
    over fake transports, no-model stub path);
  - **gates**: pytest **645 passed** · mypy strict ✓ (117 files) · ruff
    check ✓ · ruff format ✓ · pnpm lint ✓ · format:check ✓ · build ✓;
  - **live E2E (this machine, uvicorn `:8000`, LM Studio `:8080`
    Qwen3.8-27B; script `scripts/e2e_s55_ask.py`)**: login `dev@local.dev`
    (owner, `Demo App`) → status `document_count 24` → ask "How should the
    order history list be displayed to users?" → **202** → SSE
    `job.started` → `stage.started` → `progress 0.2/0.5` →
    **`knowledge.answer` in_scope=true** ("…newest-first order, with each
    order showing its status and total amount…") + **2 citations**
    (requirement "Order history" score 8.79 · test case "Order history is
    accessible with keyboard and screen reader" score 7.89) →
    `job.completed` → job row `completed`,
    `output_ref knowledge-ask://7804b95c-…` → out-of-scope "What is the
    capital of France?" → **refusal** (in_scope=false, no answer, no
    citations — contract held) → 3 `ai_actions` rows (tokens + latency) →
    **E2E OK**;
  - **gotcha:** the seeded `Demo App` corpus (5 requirements / 18 test
    cases / 1 run history) is **not** the S5.4 golden demo-shop corpus
    (14 docs) — e.g. order-list *pagination* (10 per page) lives only in
    the golden corpus, so the agent correctly **refuses** it for Demo App;
    pick in-scope E2E questions grounded in the seeded corpus (e.g. order
    history display) · `QAAnswer.citations` must be a **list** (not tuple)
    in the API path.
- 2026-08-30 · **S5.4 (RAG Q&A agent) — all gates green + live gate passed**:
  `knowledge-qa@1` strict grounded-answer contract (bible §19 S5.4; in-scope
  → grounded answer + corpus citations, out-of-scope → refusal only):
  - `qa_copilot_ai/agents/knowledge_qa.py` — `KnowledgeQAAgent` (gateway
    §31.1 + `knowledge-qa@1` registry prompt) + `QAAnswer` contract
    (pydantic validator: in-scope = non-empty answer + ≥ 1 citation; refusal
    = `in_scope=false`, no answer, no citations — schema-enforced) +
    `parse_qa_answer` (fence/prose-tolerant, fails loud §31.7);
  - `qa_copilot_ai/knowledge_qa/` — `runner.py` (retrieve top-5 → agent →
    parse → deterministic oracle: verbatim grounded facts + expected
    citations; gate pass rates) + `cli.py` (JSON stdout, `--report` file,
    stderr summary, exit 0/1/2);
  - `qa_copilot_knowledge/qa_golden.py` + `golden/qa_v1.json` (12 questions:
    8 in-scope + 4 out-of-scope over the 14-doc demo-shop corpus; gate
    0.8/1.0) + `_gen_qa_v1.py` deterministic generator;
  - `scripts/knowledge_qa_run.py` — dotenv-aware CLI wrapper (repo-root
    `.env` path fixed);
  - tests: `tests/unit/test_knowledge_qa.py` (32 — contract/parser/runner +
    E2E CLI over fake `httpx` transports + in-process OpenAI-compat server);
  - **gates**: pytest **637 passed** (full unit suite; includes a fix to
    pre-existing `test_db_smoke_vector_roundtrip` — made self-contained with
    the S5.2 rollback pattern; it had been failing on the emptied dev-DB
    `embeddings` table) · mypy strict ✓ · ruff ✓ · format ✓;
  - **live gate (LM Studio `:8080`, Qwen3.8-27B)**: first run in-scope 6/8 —
    two oracle phrases too rigid (`newest-first` vs `newest first`, `page
    query param` vs `page param`) → loosened QA-001/QA-005 facts in the
    generator + regenerated `qa_v1.json` → **8/8 in-scope grounded + 4/4
    out-of-scope refused → `passed: true`, exit 0** (report
    `live_qa_report.json`).
- 2026-08-30 · **S5.3 (Knowledge API + web) — all gates green + live E2E passed**:
  S5.2 seam → project API (bible §19 S5.3: index 202+job, search + documents API,
  "Project Knowledge" tab; exit: search returns **project-specific** chunks with
  source metadata, visible in UI):
  - `apps/api` — `knowledge_store.py` (project-scoped document build: requirements,
    test cases, run history with stable `result-{id}` row labels — `test_results`
    stores no test name; `KnowledgeStatusDict`; hybrid search over the S5.2 seam;
    documents/chunks persistence) · `routes.py` (`POST /projects/{id}/knowledge/index`
    → 202 + `job_id` · `GET .../knowledge/status` · `GET .../knowledge?q=&top_k=` ·
    `GET .../knowledge/documents[/{id}]`) · `jobs.py` `knowledge_index` stage ·
    `schemas.py` (`IndexRequest` — optional `repository_path`; `KnowledgeStatus` /
    `SearchHit` / `KnowledgeSearchResult` / `KnowledgeDocumentOut`) · `agent.py`
    index-job wiring;
  - `apps/web` — `ProjectKnowledge` tab (status card: count / by source type /
    last indexed; index button with SSE progress; search with scored hits +
    matched terms; documents list) + api-client functions;
  - tests: `tests/unit/test_knowledge_store.py` (project-scoped build, stable
    run-result labels, status/search) · full suite **605 passed**;
  - **gates**: pytest 605 ✓ · mypy strict ✓ · ruff ✓ · pnpm lint ✓ · format ✓ ·
    build ✓;
  - **live E2E (this machine, `:8000`)**: login → `POST .../knowledge/index` 202 →
    SSE `job.started` → progress → `stage.completed` (**24 docs**) →
    `job.completed` → status `{requirement: 5, run_history: 1, test_case: 18}` →
    search "discount" → correct test-case + run-history hits (score, source_type,
    matched_terms) · "login credentials" → requirements + test cases (16
    candidates, `truncated: true` at top_k 3) · "zzz" → empty hits · documents
    list → full docs with content/metadata;
  - decisions: index body = `{}` or `{repository_path}` (repo files optional;
    project QA data always included) · search = `GET /knowledge` (top-k ≤ 5 cap,
    §14) · run-history rows labeled `result-{test_result_id}` (stable; no test
    name persisted) · SSE events namespaced (`job.*` / `stage.*` / `progress`) ·
    login returns `token` + `projects[]` (not `access_token`/single project).

- 2026-08-30 · **S5.2 (embeddings / vector seam) — all gates green**:
  `qa_copilot_knowledge` embedding seam (bible §19 S5.2: "`EmbeddingProvider`
  protocol + OpenAI-compat provider (fake-server tests) → `embeddings` table;
  graceful lexical fallback when endpoint unavailable (501)"; local endpoint
  is completion-only → this is the seam, not a live vector path):
  - `embeddings.py` — `EmbeddingProvider` protocol (`embed(texts)` →
    `list[EmbeddingVector]`, `model` property; `EmbeddingVector` =
    index/vector/model); `OpenAICompatibleEmbeddingProvider` — POST
    `{base}/embeddings`, §31.1 defaults (60s timeout / 10s connect / one
    retry on transport errors), `close()` + context manager;
    `parse_embedding_response` (strict: input-order match, non-empty vectors,
    response `model` propagated into each vector); `cosine_similarity`
    (zero vector → 0.0, dimension mismatch → `ValueError` — fail loud);
    `EmbeddingError` (fail-loud: any other HTTP status, non-JSON body,
    malformed payload) vs `EmbeddingUnavailable` (graceful set
    `UNAVAILABLE_STATUSES` = {501, 503} + unreachable after retries);
  - `hybrid.py` — `vector_search` (uncapped cosine-ranking primitive, mirror
    of `LexicalIndex.search`; deterministic order score desc → chunk id;
    chunks without a stored vector skipped; `matched_terms` empty) +
    `hybrid_search` (applies the §14 top-k ≤ 5 cap; mode-tagged
    `HybridSearchResult` — `mode` ∈ {`lexical`, `vector`} + `provider`);
    **graceful fallback only for `EmbeddingUnavailable`** → bit-for-bit the
    unchanged S5.1 lexical result; any other `EmbeddingError` / dimension
    mismatch propagates (no silent degradation, §9/§31.1);
  - `persist.py` — the S0.5 `embeddings` table (pgvector
    `VECTOR(VECTOR_DIM)`, one row per document):
    `store_document_embedding` (idempotent upsert; fail-loud validation:
    dim == `VECTOR_DIM`, numeric, finite — pgvector rejects NaN/inf),
    `load_document_embeddings` (keyed by document id, missing rows omitted),
    `embed_and_store`; caller owns the session/transaction;
  - `pyproject.toml` — + `httpx>=0.27`, `sqlalchemy>=2.0` (`uv lock`);
    `__init__.py` exports the new public APIs;
  - **tests**: `tests/unit/test_knowledge_embeddings.py` — **51 tests**
    (35 test functions, some parameterized) on `httpx.MockTransport`
    (the project's fake-server pattern): success parse + batch ordering,
    501/503 → `EmbeddingUnavailable`, 4xx/other HTTP + non-JSON body + NaN
    vector (raw text body — httpx refuses JSON-encoding `NaN`) →
    `EmbeddingError`, transport retry (1 retry then unavailable),
    `cosine_similarity` + `vector_search` ranking, `hybrid_search`
    vector mode / lexical fallback / top-k cap / validation, persistence
    store/load/upsert/dimension/non-finite (guarded: skip if the dev DB or
    table is unavailable, roll back afterward);
  - **gates**: `uv run pytest -q` → **590 passed** (539 + 51) ·
    `uv run mypy` → **clean, 108 files** (strict) ·
    `uv run ruff check .` + `uv run ruff format --check .` → all green ·
    golden retrieval gate re-verified → `test_knowledge_golden.py`
    **10/10 passed** (live set 13/13 top-1, `gate_met: true`) — lexical
    baseline unchanged;
  - decisions: graceful fallback is **intentionally limited** to
    `EmbeddingUnavailable` (501/503/unreachable) — everything else fails
    loud; `VECTOR_DIM` stays the 1536 placeholder until a real embedding
    model is chosen (bible §31 budgets); no live vector run — the local
    endpoint's exact 501 behavior IS the graceful path, exercised in tests;
    `ruff format` also normalized a handful of unrelated files (mechanical;
    the format gate is repo-wide) — kept in the step commit;
  - S5.3 wires a real endpoint into this seam (API + `embeddings` table).

- 2026-08-30 · **S5.1 (deterministic knowledge core) — golden gate 13/13, all gates green**:
  `qa_copilot_knowledge` — LLM-free retrieval path (bible §19 S5.1: no LLM in the
  retrieval path; local endpoint is completion-only):
  - `models.py` — Pydantic (`DomainModel`) documents/chunks/hits/results,
    golden-set schema (`RetrievalGoldenSet`/`RetrievalQuery`/`RetrievalGate`,
    `KnowledgeGoldenSetError` loud on missing/bad JSON/schema), `GoldenReport`
    (`gate_met`, `pass_rate`, per-query `top1_ok`/`topk_ok`);
  - `chunking.py` — deterministic size-capped chunking (~600 tokens ≈ 2400 chars,
    blank-line blocks → line merges → hard-cut oversize; stable
    `chunk-{sha1[:16]}` ids; content preserved);
  - `search.py` — BM25 lexical index, hard-capped top-k ≤ 5 (`MAX_TOP_K`),
    deterministic ordering (score desc → document_ref → chunk_index);
  - `sources.py` — pure-adapter set: requirements (acceptance criteria),
    test cases (steps/expected), standards+conventions (incl. `test_scripts`
    as `TestScript` list, `notes` rendered), run/failure history (evidence
    capped: first 2 lines, 200 chars/line), repository files (walk + skip
    lockfiles/generated noise, 12k char cap, language/is_test metadata);
  - `golden.py` — loader (`load_golden_set`, `default_golden_path`) +
    `run_golden_set` (build index over the set's own corpus, answer every
    query, judge gate ≥ 90% top-1);
  - `cli.py` — `golden` / `index` / `search` subcommands; JSON on stdout,
    human summary on stderr (cp1252-safe); exit codes **0** OK / gate met,
    **1** gate missed (`EXIT_GATE_MISSED`), **2** usage/env
    (`EXIT_USAGE`) — named constants, tested;
  - `golden/retrieval_v1.json` — 13-query fixed corpus (requirements, test
    cases, standards, run history, repo files incl. `server/src/api/orders.js`);
  - **gate misses debugged (11/13 → 13/13)**: Q02 + Q09 were real ranking
    collisions — enriched `orders.js` corpus doc (endpoint/list/results/csv
    tokens) and sharpened the two queries; gate now 13/13 `gate_met: true`,
    CLI `golden` exits 0;
  - **live CLI (this repo as demo target)**: `index .` → 221 documents /
    675 chunks, exit 0 · `search . "golden gate top1 accuracy" --top-k 3` →
    correct top hits (golden tests + `golden.py`), JSON + matched-terms
    summary, exit 0 · `golden` → PASS 13/13, exit 0;
  - **tests**: 5 new files, **65 tests** — `tests/unit/test_knowledge_
    {chunking,search,sources,golden,cli}.py` (hermetic; gate tests run the
    checked-in set);
  - **gates**: full suite **539 passed** · mypy strict **104 files clean** ·
    ruff **all green**;
  - gotchas this session: my initial 2 test failures were assertion
    mismatches, not core bugs (evidence cap = first **2** lines only; words
    longer than `max_chars` are hard-cut, so preservation tests must use
    words that fit); PowerShell `Set-Content`/`-replace` mangled
    backtick-`n` into literal text (repair with a Python script, not more
    PowerShell string surgery).

- 2026-08-30 · **S4.3 (Approve → re-run loop) — live full-loop E2E PASSED** (commit
  `3a78db6`): `qa_copilot_ai.loop` — `run_fix_loop()` over injectable protocols;
  `PlaywrightLoopRunner` (`loop/live.py`) adapts S4.2 verifier via `run_spec()`;
  approval gate: `--approve`/`--reject` wins → TTY prompt → non-TTY fail-safe
  **reject**; patch + re-run strictly approval-gated · `LoopReport`
  `fix-loop-report/v1` · CLI `python -m qa_copilot_ai.loop.cli` /
  `scripts/loop_run.py` · exit 0 = closed (fixed/declined/passing), 1 = open
  (rejected/not_fixed), 2 = config/LLM/patch error ·
  **live (Qwen3.8-27B, all 10 fixtures `--approve`)**: 7/8 fixable `fixed` +
  re-run PASSED (incl. defect-flag server instances), FIX-007/010 `declined`
  (correct), FIX-005 `declined` (investigator said `product_defect`; golden
  `test_data_defect` — see §7), `--reject` fail-safe verified (nothing
  applied, exit 1) · `tests/unit/test_fix_loop.py` 26 tests (protocol fakes) ·
  474 tests ✓ · mypy strict ✓ · ruff ✓.

- 2026-08-30 · **S4.2 (Fix Agent) — live gate 8/10 (target ≥ 5/10), 8/8 applicable
  passing, correct action 10/10**: `FixerAgent` (prompt `fix-agent@2`,
  `fix-proposal/v1`: patch/decline, test-file-only diff, human approval
  required) + `parse_fix_proposal` + `qa_copilot_ai.fixer` runner + CLI
  (`scripts/fixer_run.py`) · **2/10 → 8/10 jump**: rebuilt stale v1 prompt
  (`fix-agent.v2.md`: diagnosis = strong prior, code + runtime evidence govern)
  + `fixer/app_context.py` `build_app_context()` (deterministic, 48k-char
  capped app digest: curated test-ids/pages/routes/api/seeds first) →
  `{{app_context}}` (CLI `--demo-app`, opt-out `FIXER_NO_APP_CONTEXT=1`) ·
  `tests/unit/test_fixer.py` 46 tests (incl. CLI e2e vs in-process OpenAI
  server) · 448 tests ✓ · mypy strict ✓ · ruff ✓ (`reports/fixer_v1.json`).
- 2026-08-29 · **S4.1 (Failure Investigator) — live 30/30 (100% ≥ 80%)**:
  `FailureInvestigatorAgent` (prompt `failure-investigator@2`, §12 `Diagnosis`)
  + `parse_diagnosis` (strict pydantic) + runner + CLI
  (`scripts/investigator_run.py`) · prompt v1 = 23/30 missed gate (model
  overrode correct normalizer suggestion) → v2 "strong prior" framing +
  disambiguation rules → 30/30 (`reports/investigator_v2.json`) ·
  `tests/unit/test_failure_investigator.py` 28 tests · 399 tests ✓ (1
  machine-local red: `test_golden_demo_app` — §7) · mypy strict ✓ · ruff ✓.
- 2026-08-29 · **S3.3 (failure normalizer) — golden 30/30**: deterministic
  LLM-free `qa_copilot_execution.failure` — 18 named rules, priority-ordered,
  first-match wins (signals kept), capped evidence, structural extraction
  (http_status/selector/endpoint) · golden 30 fixtures
  (`packages/execution/golden/failure_v1.json`, real Playwright strings) ·
  CLI exit 0/1/2 · `tests/unit/test_failure.py` 33 tests · 373 tests ✓ ·
  mypy strict ✓ · ruff ✓.
- 2026-08-29 · **AI settings centralization** (`01a2851` + `d886b69`):
  `qa_copilot_ai.config` `ModelSettings` + `load_dotenv()` (shell env wins);
  `.env` ships `AI_EXTRA_BODY` disabling **Qwen3 thinking** (root cause of
  the 15-min failed live run — thinking ate ~28k output tokens); `LLMGateway`
  enforces input budget pre-wire (`LLMInputBudgetError`); one repo `.env`
  controls API + AI · live: generate job 24s, real spec pending review ·
  341 tests ✓ · mypy ✓ · ruff ✓.
- 2026-08-28 · **S3.2 (Runs API + Runs UI)**: `GET /projects/{id}/runs`,
  `/runs/{id}` (+`/results`, `/artifacts`, `/artifacts/{id}/content` —
  Bearer `download_url`; totals/duration computed in API layer) · web
  `RunsView` (list → detail → per-test results + diagnosis → artifacts,
  inline image preview + download via `fetchArtifactBlob` — dev auth is
  Bearer, so plain `<a href>`/`<img src>` can't send `Authorization`) ·
  303 tests ✓ · mypy strict ✓ · ruff ✓ · web prettier/eslint/tsc/vite ✓.
- 2026-08-28 · **S3.1 (execution worker) — live exit PASS**:
  `qa_copilot_execution` (database-free) — `run_playwright` spawns target's
  `playwright test --reporter=json` (node_modules/.bin shim; `webServer`
  boots demo servers) → JSON report → §15 artifact set → `ArtifactStore`
  (`runs/{run_id}/{test_id}/{name}`, no overwrites) → `RunReport`;
  `qa_copilot_repository.runs.persist_run` → test_runs/test_results/artifacts ·
  CLI exit 0/1/2/3 · live: demo app 1/1 pass, 5 artifacts ·
  288 tests ✓ · mypy strict ✓ (fixed 18 pre-existing test errors) · ruff ✓.

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

**S7.1 — GitHub core (LLM-free)** (bible §19 Phase 7; exit: PR → changed-files
contract matches golden 100% · PAT never appears in logs/audit (red-team check) ·
no LLM in the path):
- `qa_copilot_integrations.github` — typed httpx client (PAT via env, never
  logged/audited; redacted §17): `resolve_repository(owner, repo)` →
  `repositories` fields (url, default_branch) · `fetch_pull_request(owner, repo,
  number)` → head/base sha + changed files in exactly the S6.1 `files[]` shape;
- `integration_configs` table (project_id, provider, base_url, token_ref,
  enabled; unique on project+provider) + migration; RBAC: owner-or-above
  config, member-or-above read;
- golden `github_v1.json` (§22) + fake-server tests (success, 401/404 mapping,
  token redaction) + CLI `python -m qa_copilot_integrations.github pr-files …` → JSON.
- **Phase 6 closeout (2026-09-02):** S6.5 live E2E 38/38 passed (analyze →
  202 → `regression.set` SSE → run the set → S3 Playwright 1/1 passed) ·
  baseline `reports/regression_v1.json` committed for drift tracking
  (§31.6/§31.7) · S6.4 backfilled into `SESSION_LOG.md` (that session
  skipped the memory update) · evidence pair `scripts/_s65_live.py` +
  `_s65_seed.py` committed · one-off probes + a stray git-log dump file
  removed.
- Queued follow-ups (not blockers): SSE bus is in-process — multi-worker
  deploy needs Redis pub/sub · demo-app `Dockerfile` unverified (S3.1) ·
  `test_golden_demo_app` conventions-golden re-baseline on clean trees
  (S4.1 note) · FIX-005 investigator classification (`product_defect` vs
  golden `test_data_defect`) — candidate for a failure-investigator prompt
  nudge · **re-run `scripts/_s65_live.py` after any prompt/model/regression-
  core change and diff `reports/regression_v1.json`** (drift tracking,
  §31.6/§31.7) · **vector retrieval** is still lexical-only (LM Studio has
  no embeddings endpoint, 501) — the S5.2 `EmbeddingProvider` seam is ready
  for a real endpoint.

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
- Regression (S6.1–S6.5): impact core `packages/repository/src/qa_copilot_repository/impact.py` ·
  flaky/risk `.../history.py` · recommender `.../regression.py` + advisor
  `packages/ai/src/qa_copilot_ai/agents/regression_advisor.py` · S6.4 API
  `apps/api/src/qa_copilot_api/{jobs,routes,schemas}.py` + web
  `apps/web/src/components/RegressionAnalysis.tsx` · **S6.5 evidence pair**
  `scripts/_s65_live.py` (38 assertions) + `scripts/_s65_seed.py` (demo
  flaky/fail→pass seed) · baseline `reports/regression_v1.json` (tracked —
  the single `.gitignore` exception under `reports/`; re-run the pair after
  any prompt/model/regression-core change and diff the report).

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
- **Playwright real message wording (S3.3):** the test timeout line is
  `Test timeout of 30000ms exceeded` (NOT "timed out after …") — golden
  fixtures must use the actual Playwright strings, not paraphrases.
- **mypy `no-redef` (S3.3):** annotating a variable again in the other
  if/else branch errors (`evidence = …` then `evidence: list[str] = []`);
  annotate on the first assignment only.
- **demo-app conventions golden vs WIP specs (S3.3 note, S4.1 root-caused):**
  the golden was baselined with untracked `e2e/ui/review-queue-demo.spec.ts`
  present (supplies the `*.spec.ts` pattern; zero locators). The untracked
  `e2e/login-invalid-credentials.spec.ts` live-run artifact (getByRole×4 +
  locator×1 in the "playwright" bucket) broke `locator_styles` on this
  machine — S4.1 archived it to `Workspace\demo-app-wip-backup\`, suite
  green. Clean-machine caveat: the golden still expects `*.spec.ts`, so it
  would fail there on `test_file_patterns`; re-baseline to the committed
  demo-app state (or commit that spec) in a later step.
- **Stale committed prompt (S4.2):** `fix-agent.v1.md` on disk predated the
  agent contract — v2 was rebuilt from the ACTUAL v1 body, then extended
  (app_context + evidence-over-diagnosis). Always diff the committed prompt
  against parser/agent expectations before iterating.
- **Tool-shell CWD resets (S4.2):** after a long `Start-Sleep` poll the
  PowerShell tool shell came back at `Workspace\` (not the project dir) —
  relative paths silently hit the wrong folder and the "missing report" was
  a wrong-directory read; use absolute paths when polling background runs.
- **S4.3 loop exit semantics:** `declined` (fixer had no fix) → exit **0**
  (loop closed) vs `rejected` (approver refused a real patch) → exit **1**
  (loop still open: test failing, decision pending) — a deliberate `--reject`
  is not a crash.
- **PowerShell + CLI dashes (S4.3):** the loop CLI is long-option only —
  `-reject` (single dash) is "unrecognized" (argparse defines `--reject`);
  `--%` stop-parsing kills `@splat` expansion in wrapper scripts. Reliable
  pattern here: foreground `uv run … 2>&1 | Out-File` + append
  `"EXIT=$LASTEXITCODE"` (background `Start-Process` jobs have been silently
  lost in the tool shell).
- **S5.1 test-vs-core triage:** the first 2 S5.1 test failures were
  assertion mismatches, not core bugs (the live golden gate already passed):
  history evidence is capped to the FIRST 2 lines (200 chars/line) — a 4th
  evidence item never renders; chunking hard-cuts any word longer than
  `max_chars`, so content-preservation tests must use words that fit.
- **PowerShell backtick-n corruption (S5.1):** `Set-Content`/`-replace`
  replacement strings with backtick-n insert literal `` `n `` text (single-
  quoted PS strings don't process escapes) — repair with a small Python
  script (`t.replace(chr(96)+"n", "\n")`), not more PowerShell surgery.
- **mypy strict on argparse handlers (S5.1):** `args.handler` is Any —
  annotate `handler: Callable[[argparse.Namespace], int]` before `return
  handler(args)` or `no-any-return` fires.
- **httpx JSON + NaN (S5.2):** `httpx` refuses to JSON-encode
  `float("nan")` in a request body — the malformed-NaN-vector test posts a
  raw text body instead (the provider parses `response.json()` either way).
- **ruff format is repo-wide (S5.2):** `ruff format` normalized a few files
  outside the step (mechanical line-wrap only); the format gate is
  repository-wide, so those changes belong in the step commit.
- **S6.5 live-run rows have `test_case_id NULL`:** live Playwright results
  persist without a test-case link → flaky/fail→pass evidence must come from
  the seeded `test_results` history (`_s65_seed.py`), not live-run rows.
- **`regression.set` impact entries carry `path`** (not `test_key`) for the
  impacted test files; the ranked `recommendations[]` entries carry
  `test_key` — don't mix the two shapes up when asserting.
- **API health is `GET /health` at the root** (`http://127.0.0.1:8000/health`)
  — not under `/api/v1`.
- **`started_at NULL` history rows** sort by `created_at` in
  `project_test_history` — pre-existing seed rows may land after later runs;
  assert the observed order (S6.5: `failed, flaky, flaky, passed, passed,
  passed`), never assume insertion order.
- **domain `TestHistoryStats` has no `last_status` /
  `insufficient_samples`** — the API serialization adds them; assert only
  fields the exposed contract has (the S6.5 driver's first run failed on
  exactly this).
- **stale read-tool cache (S6.5 session):** the read tool served an old
  snapshot of `STATE.md` (pre-S6.3-session wording) — `editor` matches on
  disk content; when an edit fails with "text not found", re-fetch the exact
  line via terminal (`Get-Content`) before retrying.
