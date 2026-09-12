# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19 · per-session detail: `SESSION_LOG.md`

## 1. Current position

- **Phase:** 0–7 **complete** (S0.1–S7.5 ✓ — one line each in §2) ·
  **Phase 8 — Commercialization: IN PROGRESS** — S8.0 ✓ step table (bible §19;
  user-approved 2026-09-08: **pilot scope §24**, **SSO/OAuth deferred to
  Enterprise §24**, **billing = admin-assigned plans + real quota metering, no
  payment processor**) · **S8.1 ✓ auth hardening** · **S8.2 ✓ teams** · **S8.3 ✓ RBAC+audit** ·
  **S8.4 ✓ billing** · **S8.5 ✓ deployment hardening** · S8.6 planned
  (pilot E2E + baseline)
- **next:** **S8.6 — Pilot E2E + baseline report** (two-user pilot
  scenario, full pipeline, quota denial + plan bump, audit-trail export,
  live driver + `reports/commercialization_v1.json` — details in §3)

## 2. Just completed (one line per step — full detail: SESSION_LOG.md)

- **2026-09-12 · S8.5 deployment hardening — complete + gated.** Prod
  deployment posture + the full S8.5 security surface (details:
  SESSION_LOG 2026-09-12) · `docker-compose.prod.yml` — `db`/`redis`/`api`
  publish **no** ports, `caddy` is the only 80/443 entrypoint (TLS
  reverse proxy, `infra/caddy/Caddyfile`) · `AUTH_TOKEN_SECRET` required —
  compose fails loudly · `migrate` (alembic head) must succeed **before**
  `api` starts · `api`: non-root, healthcheck on `/health`, restart
  policy, resource limits · `apps/api/Dockerfile` + `.dockerignore` ·
  `apps/api/src/qa_copilot_api/security.py` — middleware outer→inner:
  `RequestIDMiddleware` (echoes provided `X-Request-ID`, generates one
  when absent, sets `request_id_var` for the request) → `CORSMiddleware`
  (registered **only when** `cors_origins` is non-empty — fail-closed) →
  `RateLimitMiddleware` → `SecurityHeadersMiddleware` (CSP/HSTS/
  X-Content-Type-Options/Referrer-Policy on success, error, and 429
  responses) · rate limiting: Redis fixed window (100 req/60 s default),
  IP bucket fallback, user bucket **only from verified JWT `sub`**,
  `/health`/`/docs`/`/redoc`/`/openapi.json` exempt, **fail-open** when
  Redis is down, blocked → **429 + `Retry-After`** + structured
  `rate_limited` body · JSON log correlation: `RequestIDFilter` stamps
  `request_id_var` onto every in-request record → top-level `request_id`
  in the JSON line (`logging_config.py`) · `scripts/backup.sh` (pg_dump
  custom + artifacts → `qa-copilot-backup-<UTC>.tar.gz` + MANIFEST;
  CRLF-safe .env loader) + `scripts/restore.sh` (extract → `pg_restore
  --clean --if-exists --single-transaction` → artifacts) · `.gitleaks.toml`
  + `.github/workflows/ci.yml` gitleaks job (fail-closed §31.4) ·
  **root-cause fix found during validation:** `infra/migrations/env.py`
  `fileConfig(...)` defaulted to `disable_existing_loggers=True` → any
  in-process alembic run (all DB tests) silently set `disabled=True` on
  every existing app logger for the rest of the process; now
  `disable_existing_loggers=False` · tests **31**: `test_s85_security.py`
  **24** (headers on success/404/429 · request-id echo+generate · JSON
  line correlation · CORS allow/deny/preflight + fail-closed default ·
  rate-limit buckets/IP-fallback/verified-sub/exemptions/fail-open/
  429+Retry-After — live-Redis tests skip honestly) · `test_s85_infra.py`
  **6** (structural: compose posture, Dockerfile, Caddyfile,
  .dockerignore, gitleaks+CI, backup/restore scripts) · `test_s85_backup.py`
  **1** — **live round-trip**: seed → `backup.sh` (real script inside the
  pgvector image) → TRUNCATE + artifact deleted → `restore.sh` → rows +
  artifact byte-identical · gates green (ruff check+format 220 files ·
  mypy strict 123 files · **full unit suite 1021 passed, 0 failed** ·
  `bash -n` both scripts) · `3acc90b`.
- **2026-09-12 · S8.4 billing core — complete + gated.** Code-defined plan
  catalog (`free`/`pro`/`enterprise`: concurrent_jobs, runs/month,
  tokens/month) over the existing `organizations.plan` column — unknown/
  `dev`/NULL resolve to `free` (default) · live metering from real tables
  (in-flight jobs from `jobs`, runs from `test_runs`, tokens from
  `ai_actions`; per-month window via `month_start`/`month_label`) ·
  `check_quota(session, org_id, *, job_type, exclude_job_id)` →
  `QuotaCheck` (limit/used/allowed), evaluation order: concurrent_jobs →
  runs_per_month (run_execution only) → tokens_per_month · `PlanLimitExceeded`
  domain exception · enforcement in `JobRunner._run()` **before**
  `create_ai_session` (denied dispatch rolls the job row back → zero
  `ai_sessions` rows, never a mid-run crash) · `POST /requirements/analyze`
  maps the rejection to **409** + structured `plan_limit` body (plan/limit/
  used/allowed/org/project/job id) + `ORG_QUOTA_DENIED` audit row (actor +
  client IP, outcome `denied`) · `GET /organizations/{id}/plan` +
  `GET /organizations/{id}/usage` (member+; cross-org/outsider → 403 never
  404) · `PATCH /organizations/{id}` owner-only plan assignment
  (`ORG_PLAN_UPDATED` audited; non-owner → 403 + gate-denied row) · new
  core `qa_copilot_repository.billing` (no new migration — reuses
  `organizations.plan` + usage tables) · tests:
  `test_s84_billing.py` **16** (S8.3-style fixture: scratch DB
  `qa_copilot_s84_test` + Alembic head per test + deterministic UUIDs +
  StubAgent matching the real `JobAgent` contract): plan catalog + free
  fallback · usage endpoint exact numbers · RBAC matrix (member reads ·
  viewer 403 · outsider 403) · owner plan update + audit · non-owner 403 +
  gate row · allowed dispatch → job completes + exactly one `ai_sessions`
  row · denied dispatch ×3 (concurrent_jobs / runs_per_month /
  tokens_per_month) → 409 + `plan_limit` body + no new sessions + single
  quota-denied audit row · gates green (ruff check+format 216 files, mypy
  strict 122 files, **990 passed**). Details: SESSION_LOG 2026-09-12.
- **2026-09-11 · S8.3 RBAC hardening + audit trail — complete + gated.**
  Owner-gated org deletion: `DELETE /organizations/{id}` 204 — cascades the
  org's projects/memberships/invites and revokes **every former member's**
  refresh tokens; member/outsider/probed-unknown → **403 never 404** ·
  **re-auth** (`current_password` in the body; wrong password → 401 +
  `org.delete.reauth_failure` row) — the RBAC check runs **before** body
  validation (member + no body → 403, owner + no body → 422) · account
  self-delete `DELETE /auth/account` 204 (purges user + PII, cascades
  memberships/tokens, nulls `ai_sessions.user_id`; `auth.account.delete` row) ·
  **append-only `audit_log`** (`actor_id` nullable — a login failure has no
  actor; `action` = closed `AuditAction` vocabulary; `target`; `outcome`
  success/failure/denied; `ip`; `at`; `actor_id` ON DELETE SET NULL → rows
  outlive BOTH org and actor deletion; no update/delete path anywhere) · core
  `qa_copilot_repository.security_audit` (`record` flush-only,
  `list_for_target` newest-first, `DEFAULT_LIMIT=200`; core never commits —
  API owns the transaction, S7.3 pattern) · events: login/register/refresh/
  change-password success+failure, org gate-denied, membership add/update/
  remove/leave, invite create/accept/denied, org delete (+reauth failure),
  project delete · owner-only export `GET /organizations/{id}/audit`
  (newest-first, capped 200) · domain `ORG_ROLE_RANK` + `org_role_at_least` ·
  migration `b7e4d9c2a815` (**new head**) + dev DB (5433) migrated · tests:
  `test_s83_rbac_audit.py` **7** (delete role matrix · re-auth 401 + success ·
  export owner-only/newest-first/capped · invite create+accept rows · rows
  outlive org + actor deletion) + `test_s82_teams.py` repaired to the re-auth
  contract (`current_password` + `client.request("DELETE", …)` — httpx's
  `.delete()` takes no `json`) + `test_auth.py` self-delete +
  `EXPECTED_TABLES` += `audit_log` · gates green (ruff check+format, mypy
  strict 165 files, **974 passed**) · `2ff56bf`.
- **2026-09-09 · S8.2 Teams (organization membership) — complete + gated.**
  Org membership surface: `GET /organizations` (mine + role + member count) ·
  `GET/POST/PATCH/DELETE /organizations/{id}/members` (owner-only ops;
  outsider → 403 never 404; a member may leave via self-DELETE; one owner per
  org — partial unique index `uq_organization_members_single_owner`; transfer
  = demote-then-promote in one transaction) · one-time code invites (owner-only
  `POST /organizations/{id}/invites` → 128-bit URL-safe code returned exactly
  once, 7-day TTL, only SHA-256 hash stored §17 · `POST /invites/{code}/accept`:
  email mismatch 403 · already member 409 · unknown/expired/reused all 404, no
  reason leak) · org-baseline project access: `require_role` resolves an
  explicit `project_members` row first, else the org role (owner→owner,
  member→member) · core `qa_copilot_repository.invites` + `membership`
  extensions (LLM-free, core never commits) · migration `f3a9c2d81b57`
  (**new head**: `organization_invites` + single-owner index) · tests:
  `test_s82_teams.py` **22** (API) + 7 new in `test_repository.py` (core:
  issue/accept, reuse/expiry/mismatch/already-member, single-owner index,
  baseline + explicit override) · exit green (two users see shared projects ·
  outsider 403 · invite accept/expiry/reuse · explicit row wins) · gates green
  (ruff check+format — also cleared pre-existing repo-wide mechanical
  lint/format debt · mypy strict 163 files · **962 passed**) · `7a0e154`.
- **2026-09-08 · S8.1 Auth hardening — complete + gated.**
  `POST /api/v1/auth/register` (201; password policy 10+/letter/digit → 422;
  duplicate → 409; signup = user **and** their organization, user = `owner` in
  new `organization_members` table) · `POST /api/v1/auth/refresh` (opaque
  rotating refresh token; only SHA-256 **hash** stored in new
  `user_refresh_tokens` table; rotation revokes predecessor; reuse of a
  revoked token revokes the whole `family_id` chain) · `POST
  /api/v1/auth/change-password` (204; current-password re-auth 401; revokes
  all refresh tokens) · `GET /api/v1/auth/me` (now includes
  `organizations`) · login throttle: `throttle.py` fixed-window Redis failure
  counters per email + per IP (fail-open; 429 + `Retry-After`; success
  resets) · `redis>=5.0` dep · migration `c7e2a4f81b63` (**new head**) ·
  `tests/unit/test_auth.py` **40 tests** (deterministic stub throttlers for
  the 429 paths + live-Redis tests, skipped when Redis absent) · fixed stale
  `test_repository.py` table set (+`organization_members`,
  +`user_refresh_tokens`) · gates green (ruff check+format, mypy strict 161,
  **933 passed**) · `fa4e6c0`.
- **2026-09-08 · S8.0 Phase 8 defined — complete + approved.**
  Bible §19 Phase 8 step table (S8.1–S8.6) drafted + user-approved: S8.1 auth
  hardening · S8.2 teams/org-membership + invites · S8.3 RBAC matrix + `audit_log`
  + deletion workflows · S8.4 billing core (plans/quotas/metering over
  `organizations.plan` + `ai_actions` usage; admin-assigned, no payment
  processor) · S8.5 deployment hardening (prod compose, TLS, headers, rate
  limits, backup/restore) · S8.6 pilot E2E + baseline
  `reports/commercialization_v1.json` · stance: pilot scope §24, SSO/OAuth
  deferred to Enterprise, local-first stays §29 · `457f10f`.
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

**S8.2 is complete + gated** (`7a0e154`, 2026-09-09) — org membership CRUD,
one-time code invites (hash-only, 7-day TTL, non-leaking errors), org-baseline
project access with explicit-row override (details in §2 + SESSION_LOG 2026-09-09).

**S8.3 is complete + gated** (`2ff56bf`, 2026-09-11) — owner-gated
org deletion (`DELETE /organizations/{id}` 204; cascades the org's
projects/memberships/invites; revokes every former member's refresh
tokens; member/outsider/probed-unknown → 403 never 404) with
`current_password` re-auth (RBAC checked before body validation: member+
no body → 403, owner + no body → 422, wrong password →
401 + `org.delete.reauth_failure` row) · account self-delete
(`DELETE /auth/account` 204; purges PII, cascades memberships/tokens,
nulls `ai_sessions.user_id`; `auth.account.delete` row) · **append-only
`audit_log`** (`actor_id` nullable → login failures have no actor;
`action` = closed `AuditAction` vocabulary; `target`; `outcome` success/
failure/denied; `ip`; `at`; `actor_id` ON DELETE SET NULL → rows outlive
BOTH org and actor deletion; no update/delete path anywhere) · core
`qa_copilot_repository.security_audit` (`record` flush-only;
`list_for_target` newest-first; `DEFAULT_LIMIT=200`; core never commits →
API owns the transaction, S7.3 pattern) · events: login/register/
refresh/change-password success+failure, org gate-denied, membership
add/update/remove/leave, invite create/accept/denied, org delete
(+reauth failure), project delete · owner-only export
`GET /organizations/{id}/audit` (newest-first, capped 200) · 7 S8.3
tests + `test_s82_teams.py` repaired to the re-auth contract
(`client.request("DELETE", ...)` → httpx's `.delete()` takes no
`json`) + `EXPECTED_TABLES` += `audit_log` · migration `b7e4d9c2a815`
(new head; dev DB 5433 migrated) · gates green (ruff check+format,
mypy strict 165 files, **974 passed**). Details: SESSION_LOG 2026-09-11.

**S8.4 is complete + gated** (2026-09-12) — billing core: code-defined
plan catalog over `organizations.plan` (`free`/`pro`/`enterprise`;
unknown/`dev`/NULL → `free`), live metering from real tables
(`jobs`/`test_runs`/`ai_actions`), `check_quota` + `QuotaCheck`,
`PlanLimitExceeded` enforced in `JobRunner` **before** `create_ai_session`
(denied dispatch = job rolled back + **409** `plan_limit` body +
`ORG_QUOTA_DENIED` audit row, never a mid-run crash), member+ plan/usage
reads, owner-only `PATCH /organizations/{id}` plan assignment
(`ORG_PLAN_UPDATED`), 16 S8.4 tests, **990 passed** · details: SESSION_LOG
2026-09-12.

**S8.5 is complete + gated** (`3acc90b`, 2026-09-12) — prod compose (db/
redis/api publish no ports, Caddy-only 80/443, `AUTH_TOKEN_SECRET` fail
loud, migrate-before-api, non-root + healthcheck + resource limits) ·
security middleware (request-id echo/generate + JSON log correlation,
fail-closed CORS, security headers on success/error/429, Redis fixed-
window rate limiting: IP fallback + verified-JWT `sub` user buckets,
exempt paths, fail-open, 429 + `Retry-After`) · `backup.sh`/`restore.sh`
(pg_dump custom + tar bundle + single-transaction `pg_restore` +
artifacts) · Dockerfile + `.dockerignore` + gitleaks CI job · alembic
`env.py` no longer disables host loggers (root cause of a logging test
that passed alone but failed after any DB test) · 31 S8.5 tests (24
security incl. live-Redis, 6 infra-structural, 1 **live backup/restore
round-trip** running the real scripts in the pgvector image) · gates
green (ruff check+format, mypy strict 123, **1021 passed**) · details:
SESSION_LOG 2026-09-12.

**S8.6 — Pilot E2E + baseline report** (bible §19):
- live pilot scenario — two users (org owner + member) on one org, shared
  project · full pipeline as the member (requirement → test cases →
  automation → run → failure → diagnosis → Jira link, the S7.5 loop) ·
  quota denial + plan bump (S8.4) · audit-trail export (S8.3)
- live driver committed (evidence pair, S6.5/S7.5 pattern) + baseline
  `reports/commercialization_v1.json`
- **Exit:** live E2E green (all legs, both roles) · baseline report
  committed (single tracked report, `.gitignore` exception per S6.5) ·
  all red-team checks pass · gates green (ruff + mypy strict + pytest).

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
  `packages/repository/src/qa_copilot_repository/models.py` (26 core tables — new tables
  must be added to `tests/unit/test_repository.py` `EXPECTED_TABLES`) · migrations
  `infra/migrations/` (initial `60fa1027d8d2`, current head `b7e4d9c2a815`) · DB URL
  `.../repository/db.py` (env → `.env` → default)
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
  `history.py` (S6.2) · `regression.py` (S6.3) · `membership.py` (S0.8/S8.2) ·
  `invites.py` (S8.2) · sample repos
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
  adding a model without the table entry fails the full suite (caught in S7.1,
  and again in S8.1 with `organization_members`/`user_refresh_tokens`).
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
- S8.1: password policy = `PASSWORD_MIN_LENGTH` (10) + ≥1 letter + ≥1 digit —
  fixture passwords without a digit (e.g. `correct-horse-battery-staple`) 422 on
  register/change-password · 429 throttle tests must use a **stub throttler**,
  never live Redis counters (shared-state pollution).

- S8.3: **RBAC before body validation** → `DELETE /organizations/{id}`
  checks the role first, then the `current_password` body (member + no
  body → 403, not 422; owner + no body → 422).
- S8.3: **httpx.TestClient** → `client.delete(url)` takes no `json=`
  kwarg; use `client.request("DELETE", url, json=...)` for DELETE bodies.
- S8.3: **dev DB (Docker Postgres 5433) must be migrated** → the
  `test_db_smoke_seed_rows_present` smoke test inspects the *dev* database
  directly (`EXPECTED_TABLES` must exist there); after adding a table
  migration + `EXPECTED_TABLES` entry (e.g. `b7e4d9c2a815` / `audit_log`)
  run `alembic upgrade head` on the dev DB too, or the smoke test fails
  (`audit_log` missing from the live schema).
- S8.3: **audit rows outlive actors** → `audit_log.actor_id` is ON
  DELETE SET NULL; never add a CASCADE from `users`/`organizations` to
  `audit_log`.
- S8.4: **quota denial is 409, not 403** — the build bible (§19 S8.4)
  specifies quota exceeded → **409** `plan_limit`; the old STATE.md §3
  draft said 403 — the bible is canonical. RBAC denials stay 403.
- S8.4: **denial happens before any AI session** — `JobRunner._run()`
  calls `check_quota` before `create_ai_session`; on
  `PlanLimitExceeded` it `db.rollback()`s (which also removes the
  route-created job row). A rejected dispatch leaves **zero**
  `ai_sessions` rows and the job count is back to baseline — tests must
  assert both.
- S8.4: **metering seeds ARE usage** — a test that saturates
  `tokens_per_month` by seeding `ai_sessions`/`ai_actions` legitimately
  creates session rows; assert "no *new* sessions" (baseline before
  dispatch), never "zero sessions".
- S8.4: **raw-SQL reads of Postgres UUID columns return `UUID` objects** —
  comparing them to `str` fixture ids fails (`UUID('...') == '...'` is
  False); normalize (`str()`) UUID-valued columns in test helpers
  (e.g. `_audit_rows` for `actor_id`/`target`).