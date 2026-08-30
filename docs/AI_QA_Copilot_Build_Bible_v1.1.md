# AI QA Copilot — Build Bible

Product vision, architecture, implementation roadmap, and engineering checklist

**Version 1.1** | Build reference | 26 Aug 2026 (supersedes v1.0 of 22 Aug 2026)

> **North Star:** Requirement → Test Design → Automation → Execution → Failure Analysis → Fix → Regression Intelligence

## Changelog

- **v1.1 (2026-08-26)** — Local LLM strategy (llama server, hard context budgets); mandatory async job pattern (202 + SSE); V1 auth baseline; repository connection model; prompt registry; numeric evaluation targets; demo-app spec with defect injection; monorepo tooling; a11y engine; execution details; data-model fixes (requirement↔test many-to-many, `jobs`, `prompt_versions`); **agent-memory & session-continuity protocol**; step-based (token-efficient) execution plan.
- **v1.0 (2026-08-22)** — Initial build bible.

## How to use this document

- Treat this as the master build reference; do not add major features before the MVP gates are satisfied.
- Build vertically: one complete user journey at a time, rather than many disconnected services.
- Keep the first product narrow: Playwright + requirements + test generation + execution + failure analysis.
- Use the synthetic demo application during early development; no proprietary code, data, or credentials.
- **Token-efficient development (v1.1):** work in small steps (§19); target one step per chat session; verify with the step's exit criterion; commit after each step.
- **Session continuity (v1.1):** every new session starts by reading `agent-memory/STATE.md` (§32) and ends by updating it.
- This Markdown file is the **source of truth** (the v1.0 PDF on the Desktop is historical).

---

## 1. Product definition

| Item | Decision |
|---|---|
| Product | AI QA Copilot for Playwright-based QA teams |
| Primary users | QA engineers, SDETs, automation leads, developers |
| Core problem | QA work is fragmented across requirements, test cases, automation, execution logs, defects, and CI failures |
| Core value | Reduce time from requirement to trustworthy automated regression; shorten failure-to-fix cycles |
| Initial wedge | Requirement → test scenarios → Playwright automation → execution → AI failure analysis |
| Long-term moat | Application-specific QA knowledge from requirements, code, tests, execution history, defects, business rules |

## 2. Product principles

- **Evidence over confidence:** every important AI conclusion must cite the evidence it used.
- **Human approval for code changes:** V1 never silently modifies repositories.
- **Structured AI outputs:** store machine-readable objects, not only prose.
- **Repository-aware generation:** generated automation follows the target project's conventions.
- **Model-agnostic design:** provider-specific code is isolated behind the AI gateway.
- **Security by default:** secrets, credentials, PII, and project boundaries are first-class.
- **Observable AI:** log prompts, tool calls, outputs, latency, cost, decisions — with redaction.
- **Context frugality (v1.1):** local LLMs have limited context windows — every prompt fits a hard budget; retrieve, don't dump.

## 3. V1 feature scope

| Feature | V1 requirement | Priority |
|---|---|---|
| Requirement analysis | Parse requirement/acceptance criteria into structured QA context | P0 |
| Test design | Generate functional, negative, boundary, risk, accessibility, and basic security scenarios | P0 |
| Playwright generation | Generate maintainable tests using existing project patterns | P0 |
| Repository understanding | Detect project language/framework/folder conventions and relevant files | P0 |
| Execution | Run selected Playwright tests and collect artifacts | P0 |
| Failure analysis | Analyze error + screenshot + trace + DOM/logs and classify likely cause | P0 |
| Fix suggestion | Produce a reviewable patch/diff, not a silent change | P0 |
| Project memory | Persist requirements, standards, known issues, prior outcomes | P1 |
| Regression impact | Suggest impacted tests based on changes and history | P1 |
| Jira/GitHub/CI integrations | Connect external workflow systems | P1 |

## 4. End-to-end product flow

```
User requirement → Requirement Agent → QA Test Designer → Structured Test Cases
→ Repository Context Builder → Playwright Code Generator → Human Review / Diff
→ Execution Service → Artifacts (trace + screenshot + video + console + network + DOM)
→ Failure Investigator → Root Cause + Evidence + Confidence → Suggested Patch
→ Human Approval → Re-run → Test History / Project Knowledge
```

## 5. Target architecture

## 6. Recommended technology stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + TypeScript | Strong ecosystem, modern web tooling |
| Backend | Python + FastAPI | Fast development, excellent AI/automation ecosystem |
| Automation | Playwright | Primary initial automation engine |
| Database | PostgreSQL | Reliable relational core, strong JSON support |
| Vector search | pgvector | Keeps V1 architecture simple |
| Cache / jobs | Redis | Caching and async execution support |
| AI gateway | Provider abstraction in Python | No vendor lock-in |
| **AI runtime (v1.1)** | **Local model via llama server (OpenAI-compatible endpoint)** | Offline, zero cost, no cloud dependency; budgets protect context window + latency |
| **Observability (v1.1)** | Structured JSON logs + `ai_actions` first; self-hosted Langfuse optional | Prompt debugging without managed vendors |
| **Secret scanning (v1.1)** | gitleaks | Redaction before indexing and AI context assembly |
| **Tooling (v1.1)** | uv + pnpm workspaces; ruff + mypy; ESLint + Prettier | One-command setup |
| Source control | Git + GitHub first | Repository intelligence and PR workflow |
| Containers | Docker | Repeatable development and deployment |
| CI | GitHub Actions first | Simple initial CI/CD integration |

## 7. Repository structure

```
ai-qa-copilot/
├── agent-memory/            # v1.1: session continuity — STATE.md, SESSION_LOG.md (read FIRST in any new session)
├── apps/
│   ├── web/                       # React UI
│   └── api/                       # FastAPI application
├── packages/
│   ├── domain/                    # Core domain models / rules
│   ├── ai/                        # LLM gateway, agents, prompts/, schemas
│   ├── repository/                # Git, AST, file indexing
│   ├── execution/                 # Playwright execution + artifacts
│   ├── knowledge/                 # Retrieval, embeddings, project memory
│   └── integrations/              # GitHub, Jira, CI, etc.
├── infra/
│   ├── docker/
│   └── migrations/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
├── docs/                        # This build bible + ADRs
├── scripts/
├── .env.example
├── docker-compose.yml
└── README.md
```

## 8. AI agent architecture

| Agent | Responsibility | Tools / context |
|---|---|---|
| Requirement | Extract actors, flows, rules, assumptions, acceptance criteria, risks | Requirement text, project knowledge |
| Test Design | Create scenarios and structured test cases | Requirements, risk taxonomy, existing tests |
| Automation | Generate/update Playwright code | Repository index, coding standards, selected test |
| Execution | Select and run tests; collect artifacts | Playwright, environment config |
| Failure Investigator | Classify failures and propose root cause | Stack trace, screenshot, trace, DOM, logs, git diff |
| Fix | Create reviewable patch/diff | Source files, failure evidence, project conventions |
| Regression | Identify impacted regression scope | Git diff, dependency graph, test history, failures |

> **v1.1:** each agent declares a `model_class` (small / coder / vision-optional) and its token budgets in its prompt front-matter (§31.6).

## 9. AI orchestration rules

- Use deterministic application logic for routing, permissions, validation, persistence, execution control.
- Use the LLM for interpretation, generation, classification, ranking, explanation.
- Every agent has explicit input/output schemas.
- Tool access is allow-listed; agents have no unrestricted shell or filesystem access.
- Set budgets: max tokens, tool calls, execution time, retries per task.
- Store an AI action record: model, task, tools, output, latency, cost, approval status.
- **Context budgets (v1.1):** every agent has a hard input/output token budget (§31.1); the context assembler truncates retrieved chunks to fit; no unbounded context.
- **Local-model reality (v1.1):** AI calls are slow → always executed as async jobs (§11); stream responses; per-call timeout + one retry.

## 10. Core data model

| Entity | Key fields |
|---|---|
| users | id, email, role, created_at |
| organizations | id, name, plan |
| projects | id, organization_id, name, repo_id, settings |
| repositories | id, provider, url, default_branch, scan_status |
| files | id, repository_id, path, hash, language, indexed_at |
| requirements | id, project_id, title, content, acceptance_criteria, risk |
| test_cases | id, title, type, priority, steps, expected_result *(v1.1: requirement link moved to join table)* |
| **requirement_test_cases (v1.1)** | requirement_id, test_case_id *(many-to-many)* |
| test_runs | id, project_id, commit_sha, status, started_at, completed_at |
| test_results | id, run_id, test_case_id, status, duration, failure_id |
| failures | id, test_result_id, category, root_cause, confidence |
| artifacts | id, test_result_id, type, uri, metadata |
| knowledge_documents | id, project_id, source_type, source_ref, content, metadata |
| embeddings | id, knowledge_document_id, vector |
| ai_sessions | id, project_id, user_id, task_type, status |
| ai_actions | id, session_id, agent, tool, **model, tokens_in, tokens_out, latency_ms** (v1.1), input_hash, output_ref, approval_status |
| **jobs (v1.1)** | id, project_id, type, status, progress, input_ref, output_ref, error, created_at, started_at, completed_at |
## 11. API contract — V1

> **v1.1 rule:** every AI-backed, long-running endpoint returns **`202 Accepted` + `{job_id}`** and reports progress via `GET /jobs/{id}` / `GET /events` (SSE). Local inference is slow — synchronous AI calls in HTTP handlers are prohibited.

| Method | Endpoint | Purpose |
|---|---|---|
| POST | /projects | Create project |
| GET | /projects/{id} | Get project |
| POST | /requirements/analyze | Analyze requirement → **202 + job_id** |
| POST | /test-cases/generate | Generate test cases → **202 + job_id** |
| POST | /automation/generate | Generate Playwright code/diff → **202 + job_id** |
| POST | /runs | Start test run → **202 + job_id** |
| GET | /runs/{id} | Get run status |
| GET | /runs/{id}/artifacts | List artifacts |
| POST | /failures/analyze | Analyze a failure → **202 + job_id** |
| POST | /fixes/propose | Create suggested patch → **202 + job_id** |
| POST | /repositories/index | Index repository → **202 + job_id** |
| GET | /projects/{id}/knowledge | Search project knowledge |
| GET | /jobs/{id} | **v1.1** — job status, progress, result/error ref |
| GET | /events?project_id= | **v1.1** — SSE stream of job/run progress events |

## 12. Structured AI schemas

**Test case output**
```json
{
  "id": "TC-001",
  "title": "Reset password with registered email",
  "type": "functional",
  "priority": "high",
  "preconditions": [],
  "steps": [],
  "expected_results": [],
  "risk": "medium",
  "requirement_refs": ["REQ-001"]
}
```

**Failure analysis output**
```json
{
  "category": "automation_defect",
  "root_cause": "obsolete_locator",
  "confidence": 0.92,
  "evidence": [
    "DOM snapshot contains data-testid=submit-order",
    "Git diff changed button markup"
  ],
  "suggested_fix": "Update locator in checkout_page.py",
  "needs_human_approval": true
}
```

## 13. Repository intelligence

- Scan files and detect language/framework.
- Parse imports/classes/functions using AST where possible.
- Identify tests, page objects, fixtures, helpers, config, test data.
- Extract locator patterns and coding conventions.
- Build file/function/test relationships.
- Index git history and recent diffs.
- Create retrievable chunks with metadata: project, file, symbol, language, test area, commit.
- **v1.1:** chunks have a hard size cap (default ≤ 600 tokens) to fit local-model context budgets (§31.1).

## 14. RAG / project memory design

```
User request → Intent / task classifier
→ Retrieve relevant context from: requirements, repo files/symbols, existing tests,
  standards, defects, prior failures, execution history
→ Context ranking / deduplication (top-k ≤ 5 chunks, hard-truncated to agent budget)
→ Agent prompt (within hard token budget)
→ Structured answer + evidence
```

## 15. Playwright execution service

- Run tests in isolated workers/containers.
- Capture trace.zip, screenshot, video, console, network, stdout/stderr, metadata.
- Record commit SHA, browser, OS/container image, environment, duration, retries, test identifier.
- Normalize failures so the AI sees consistent structures, not raw logs only.
- Keep artifact storage separate from relational metadata.
- Never expose secrets in logs or AI context.
- **v1.1:** app under test reached via compose service URL (`APP_UNDER_TEST` env); tracing on with retain-on-failure; pinned Playwright browser image; artifact path convention `runs/{run_id}/{test_id}/{type}`; retention default 30 days, configurable.

## 16. Failure taxonomy

| Category | Examples |
|---|---|
| Product defect | Incorrect UI behavior, API response, business logic |
| Automation defect | Bad locator, timing issue, wrong assertion, bad fixture |
| Environment defect | Service unavailable, network, credentials, infrastructure |
| Test data defect | Missing/invalid data, stale fixture |
| Flaky behavior | Non-deterministic timing or external dependency |
| Unknown | Insufficient evidence; require more diagnostics |

> **v1.1:** failure analysis is **text-first** (stack trace, DOM snapshot, network log, console, git diff) because local models are assumed non-vision; screenshot/trace analysis is used only when a vision model is configured.

## 17. Security requirements

- Tenant/project isolation at database and service layers.
- Secrets scanning and redaction before AI calls or logs (gitleaks, §31.4).
- Do not send entire repositories when only a subset is needed.
- Encrypt data in transit and at rest in production.
- RBAC for project, repository, execution, and code-change actions.
- Audit all AI-generated code changes and approvals.
- Provide data-retention controls and deletion workflows.
## 18. Build roadmap

| Phase | Target | Build | Exit criterion |
|---|---|---|---|
| Phase 0 — Product foundation | 3–5 days | Repo, architecture decisions, environments, coding standards, sample demo app | Foundation green; local stack starts with one command |
| Phase 1 — Requirement/Test Designer | Week 1 | Requirement parsing, test generation, schemas, UI | Requirement produces repeatable structured tests |
| Phase 2 — Playwright Copilot | Week 2 | Repo scanner, framework detection, code generation, diffs | Generate a test consistent with an existing project |
| Phase 3 — Execution | Week 3 | Playwright runner, artifacts, run history | One-click execution with trace/screenshot/video |
| Phase 4 — Failure Intelligence | Week 4 | Failure parser, investigator, confidence, fix proposal | Broken test → evidence-backed diagnosis |
| Phase 5 — Project Knowledge | Month 2 | RAG, embeddings, history, standards | Answers reflect project-specific context |
| Phase 6 — Regression Intelligence | Month 2–3 | Change impact, test prioritization, flaky detection | Recommend a focused regression set |
| Phase 7 — Integrations | Month 3 | GitHub, Jira, CI/CD | Fits engineering workflow |
| Phase 8 — Commercialization | After MVP validation | Auth, billing, teams, RBAC, deployment hardening | Pilot-ready product |

> **v1.1:** Phases 0–4 are decomposed into single-session steps in §19. Phases 5+ are decomposed only when entered (detail-on-demand — do not pre-write their detail).

## 19. Step execution plan (token-efficient, v1.1)

**Rules of the step system:**
1. One step = one small, verifiable unit of work — target: completable in a single chat session.
2. Finish the step's **exit criterion**, then commit with `step S#.x: <summary>`.
3. Update `agent-memory/STATE.md` after every step/session (§32).
4. Never start a step while the previous one is red; fix-forward before feature-forward.

### Phase 0 — Foundation

| Step | Work | Exit criterion |
|---|---|---|
| S0.1 | Monorepo skeleton: git init, uv + pnpm workspaces, `apps/api` + `apps/web` placeholders, `packages/{domain,ai,repository,execution,knowledge,integrations}`, ruff + mypy + ESLint + Prettier configs, pre-commit, `.env.example` | `ruff check` + `pnpm lint` pass on the empty scaffold; committed |
| S0.2 | docker-compose: PostgreSQL+pgvector, Redis | `psql` query + `redis-cli ping` OK *(requires Docker Desktop — see STATE.md)* |
| S0.3 | FastAPI skeleton: app factory, pydantic-settings, JSON structured logging, `GET /health` | `GET /health` → 200 |
| S0.4 | Domain package: pydantic entities + enums (project, requirement, test_case, failure, artifact, job) | Schema unit tests green |
| S0.5 | SQLAlchemy + Alembic: §10 core tables + `jobs` + `requirement_test_cases` + `prompt_versions` | `alembic upgrade head` on compose DB; seed script runs |
| S0.6 | AI gateway (local llama server, OpenAI-compatible): streaming, token accounting → `ai_actions`, redaction hook, prompt-registry loader | Unit tests green with a fake server; one live call logs `tokens_in/out` |
| S0.7 | React shell (Vite): layout + pipeline view + SSE client (mocked) | Shell renders; mocked SSE updates a progress bar |
| S0.8 | Auth baseline: dev user, JWT middleware, project roles owner/member/viewer | 401/200/403 matrix tested |
| S0.9 | Jobs service: `202` pattern, job runner, `GET /jobs/{id}`, `GET /events` SSE | POST stub agent → 202 → SSE progress → completed |
| S0.10 | Demo app v0 (separate repo `ai-qa-copilot-demo-app`): Express + SQLite + React; `/login /products /cart /checkout`; defect-injection env flags | Manual smoke passes; one defect flag changes behavior |

### Phase 1 — Requirement → Test Design

| Step | Work | Exit criterion |
|---|---|---|
| S1.1 | Requirement Agent (prompt v1, schema-validated) | 10 fixture requirements → 10/10 schema-valid |
| S1.2 | Test Design Agent (functional/negative/boundary/risk/a11y/security) | Step coverage ≥ 85% vs oracle on 10 requirements |
| S1.3 | UI flow: requirement → structured test cases (persisted) | Manual E2E through the UI |
| S1.4 | Eval runner CLI + golden set v1 (§22) | `eval run` emits JSON report vs §31.7 targets |

### Phase 2 — Playwright Copilot

| Step | Work | Exit criterion |
|---|---|---|
| S2.1 | Repository scanner: language/framework detection, test-structure detection | Correct on 3 sample repos |
| S2.2 | Convention extractor: locators, page objects, fixtures, helpers | Golden outputs match on 2 repos |
| S2.3 | Automation Agent: generate tests using extracted conventions | Generated code passes lint + type ≥ 95% |
| S2.4 | Diff review UI + human approval (apply to workspace) | Apply + reject flows tested |

### Phase 3 — Execution

| Step | Work | Exit criterion |
|---|---|---|
| S3.1 | Execution worker: Playwright run, trace/screenshot/video/console/network capture | 1 test on demo app → all artifacts stored |
| S3.2 | Runs API + run history + artifacts UI | A run is visible with its artifacts |
| S3.3 | Failure normalizer: raw failure → structured taxonomy fields | 30 broken tests normalize 100% |

### Phase 4 — Failure Intelligence

| Step | Work | Exit criterion |
|---|---|---|
| S4.1 | Failure Investigator: classification + evidence + confidence | top-1 ≥ 80% on the 30-broken-test set |
| S4.2 | Fix Agent: reviewable patch/diff | ≥ 5/10 fixes applicable and passing |
| S4.3 | Approve → re-run loop | Full loop E2E (S3 → S4 → re-run) |

### Phase 5 — Project Knowledge

> Note (S5.0): the local LLM (Qwen3.8-27B, OpenAI-compatible) is **completion-only**
> (`POST /v1/embeddings` → 501). Retrieval therefore starts as deterministic
> lexical search (BM25) — no LLM in the retrieval path — with a pluggable
> `EmbeddingProvider` seam (S5.2) that upgrades to vector retrieval once an
> embedding endpoint exists. The `knowledge_documents`/`embeddings` (pgvector)
> tables from S0.5 already exist and are the persistence target.

| Step | Work | Exit criterion |
|---|---|---|
| S5.1 | Knowledge core (LLM-free): `qa_copilot_knowledge` — document/chunk models, hard size-capped chunking (§13), deterministic BM25 retrieval (top-k ≤ 5, §14), sources (requirements, test cases, standards/conventions, run+failure history, repository files), golden retrieval gate, CLI | Golden retrieval gate passes (≥ 90% top-1, tamper detected); "a repository can be indexed" (MVP item) |
| S5.2 | Embeddings seam: `EmbeddingProvider` protocol + OpenAI-compat provider (fake-server tests) → `embeddings` table; graceful lexical fallback when endpoint unavailable (501) | Fake-embedding unit tests green; lexical path unchanged |
| S5.3 | Knowledge API + web: `POST /projects/{id}/knowledge/index` (202+job), `GET /projects/{id}/knowledge?q=&top_k=5`, `GET .../documents`; "Project Knowledge" tab | API search returns project-specific chunks with source metadata; visible in UI |
| S5.4 | RAG Q&A agent: `knowledge-qa@1` strict grounded-answer contract (answer + citations + refusal), parser, runner + CLI over the golden Q&A set, **live gate** | Live: ≥ 80% in-scope questions grounded with project-specific facts; 100% out-of-scope refused |
| S5.5 | Ask API + web Q&A view: `POST /projects/{id}/knowledge/ask` (202+job) + chat view | Ask → 202 → job → grounded answer with citations in the UI |

### Phases 6–8

## 20. MVP definition of done

- A user can create a project.
- A user can enter a requirement.
- The system generates structured test cases.
- A repository can be indexed.
- The system generates Playwright code using repository conventions.
- The user can review a diff before changes are applied.
- A selected test can be executed.
- Artifacts are stored and visible.
- A failed test can be analyzed by the AI.
- The analysis shows evidence and confidence.
- A suggested code fix can be reviewed and approved.
- The fixed test can be re-run.
- All meaningful AI actions are auditable.
- The demo uses a safe synthetic/public application.

## 21. Quality gates for the product itself

| Area | Gate |
|---|---|
| AI quality | Schema-valid output ≥ 99% in automated contract tests |
| Test generation | Human review demonstrates useful coverage on representative scenarios |
| Code generation | Generated tests pass lint/type checks and respect project conventions |
| Failure analysis | Evaluation set tracks classification accuracy and false certainty |
| Security | No secrets/PII leakage in automated red-team checks |
| Reliability | Execution jobs are isolated, retryable, and observable |
| UX | Core flow can be completed without prompt-engineering knowledge |

**Numeric targets (v1.1, §31.7):** schema-valid ≥ 99% · failure top-1 ≥ 80% · generated code lint+type ≥ 95% · test-design coverage vs oracle ≥ 85% · secret/PII leaks = 0.

## 22. Evaluation dataset — build early

- 10–20 synthetic requirements from common SaaS workflows: auth, search, checkout, profile, upload, permissions, payments.
- Matching expected test scenarios written by you as the QA oracle.
- At least 30 intentionally broken Playwright tests with known root causes.
- A set of repository styles: page objects, fixtures, direct locators, API helpers.
- Golden outputs for test case structure and failure classification.
- Regression tests for every important prompt/schema/tool change (prompts pinned by `name@version`, §31.6).
- **v1.1:** every broken test is tagged with the defect-injection flag that produced it (§31.8), so evals are reproducible.

## 23. Demo application

Recommended synthetic app areas: `/login` · `/forgot-password` · `/products` · `/cart` · `/checkout` · `/orders` · `/profile` · `/admin`

Deliberately injected defects to evaluate: locator changes · timing failures · assertion failures · API 500s · bad test data · permission regressions · UI validation defects.

**v1.1 spec:** own repo `ai-qa-copilot-demo-app`; React + Vite frontend, Express + SQLite API; **defect injection via env flags** (each maps 1:1 to a failure-taxonomy category, §16):

| Flag | Injects | Taxonomy |
|---|---|---|
| `DEFECT_LOCATOR_DRIFT` | renamed/removed test-ids | Automation defect |
| `DEFECT_API_500` | checkout API returns 500 | Product defect |
| `DEFECT_FLAKY` | random 300ms–3s delays | Flaky behavior |
| `DEFECT_BAD_DATA` | orders missing line items | Test data defect |
## 24. Commercial roadmap

| Stage | Positioning | What customer buys |
|---|---|---|
| Prototype | AI QA helper | Individual productivity |
| MVP | AI QA Copilot for Playwright | Test design + automation + failure analysis |
| Pilot | Team QA intelligence | Repo memory + CI + shared history |
| Growth | Regression intelligence | Impact analysis + prioritization + analytics |
| Enterprise | Private AI QA platform | SSO/RBAC, isolation, private deployment, audit/compliance |

## 25. What NOT to build initially

- A foundation model of your own.
- A general-purpose Selenium/Cypress/Appium/Playwright generator simultaneously.
- A full performance-testing platform.
- A full security-testing platform.
- A mobile device farm.
- Dozens of integrations before the core QA loop works.
- Unrestricted autonomous code modification.
- **v1.1:** cloud LLM dependencies, managed observability vendors, or multi-provider orchestration complexity before the local loop works.

## 26. Product moat

LLM capability + QA reasoning + repository structure + business rules + test history + failure history + change-impact graph + execution evidence = **application-specific QA intelligence**.

## 27. Future vision — Autonomous QA Agent

```
"Test this feature" → Understand requirement → Build test strategy → Generate tests
→ Inspect repository → Generate Playwright → Execute → Investigate failures
→ Fix automation where safe → Re-run → Classify product defects → Generate QA report
→ Recommend GO / NO-GO
```

## 28. Build checklist

> **v1.1:** the step plan (§19) is the operative checklist; this v1.0 list is kept as the reference inventory.

- [ ] Git repository + branch strategy · Docker Compose environment
- [ ] FastAPI backend skeleton · React frontend shell
- [ ] PostgreSQL schema + migrations · domain models + API schemas
- [ ] AI provider gateway · requirement analyzer · test designer
- [ ] Repository scanner · Playwright generator · diff/approval workflow
- [ ] Execution worker · artifact storage
- [ ] Failure normalizer · failure investigator · fix proposal engine
- [ ] Project memory/RAG · evaluation dataset
- [ ] Unit/integration/e2e tests · secret redaction · audit logging
- [ ] GitHub integration · CI pipeline · MVP demo recording

## 29. Decision log

| Date | Decision | Reason | Revisit when |
|---|---|---|---|
| 2026-08-22 | Start Playwright-first | Tight wedge, aligns with automation focus | When multi-framework demand is proven |
| 2026-08-22 | PostgreSQL + pgvector | Simple operational model for V1 | When scale/search requirements change |
| 2026-08-22 | Human approval for code changes | Safety and trust | After strong evals justify higher autonomy |
| 2026-08-26 | **Local LLM via llama server (OpenAI-compatible); no cloud** | Offline, zero cost; limited context → hard budgets | If local quality proves insufficient for a task |
| 2026-08-26 | **Async jobs mandatory (202 + SSE)** | Local inference latency; UI stays responsive | — |
| 2026-08-26 | **Text-first failure analysis** | Local models assumed non-vision | When a vision model is configured |
| 2026-08-26 | **Agent-memory folder + STATE.md protocol** | Cross-session continuity under limited context | — |
| 2026-08-26 | **Step-based execution (S#.x, one step per session)** | Token-efficient, verifiable progress | — |
| 2026-08-26 | **Auth baseline at S0.8 (dev user + JWT + roles)** | Avoid retrofitting permissions | — |

## 30. One-page build mantra

**BUILD SMALL → MEASURE → ADD EVIDENCE → EARN TRUST → THEN ADD AUTONOMY**

---

## 31. Phase 0 decisions (v1.1 addendum)

### 31.1 LLM strategy — local model (llama server)

- **Runtime:** local llama server exposing an **OpenAI-compatible endpoint** (e.g. `http://localhost:8080/v1`, or Ollama at `:11434`). Config via env: `LLM_BASE_URL`, `LLM_MODEL`, optional per-agent overrides.
- **Gateway:** all model calls go through `packages/ai` — agents never call the model directly. Every call records `model`, `tokens_in`, `tokens_out`, `latency_ms` into `ai_actions`.
- **Model classes (per task):**
  | Class | Served by | Used for |
  |---|---|---|
  | `small` | 1–4B-class local model | Requirement parsing, failure classification, short structured outputs |
  | `coder` | 7B–70B-class local model | Test design, Playwright generation, fix proposals |
  | `vision` | optional; only if available | Screenshot/trace analysis (else text-first, §16) |
- **Context budgets (hard defaults — tune once the exact model is known at S0.6):**
  | Agent | Input budget | Output budget |
  |---|---|---|
  | Requirement analysis | 4,000 | 1,500 |
  | Test design | 8,000 | 4,000 |
  | Code generation | 12,000 | 6,000 |
  | Failure analysis | 16,000 | 3,000 |
  | Fix proposal | 12,000 | 4,000 |
- **S0.6 (2026-08-26):** first runtime fixed — LM Studio (llama.cpp) at `http://localhost:8080/v1`, model **Qwen3.8-27B Q4_K_M** (27.3B params, n_ctx 100,096, completion-only). Budgets above hold (far inside the context window; the constraint stays **latency**, not memory). No local embedding model is served yet, so `VECTOR_DIM` remains provisional.
- **Context assembly rules:** system prompt + output schema first; retrieved chunks ranked, deduplicated, hard-truncated to fit the budget; never include a whole file > 200 lines; top-k ≤ 5 chunks, chunk ≤ 600 tokens.
- **Reliability:** stream responses; per-call timeout (default 120 s); one retry; on failure the job fails with a clear error (no silent model-swap).
- **Why budgets:** with a local model the constraint is **context window + latency**, not money.

### 31.2 Async API pattern (mandatory)

- Long-running endpoints → `202 Accepted` + `{job_id}`; poll `GET /jobs/{id}`; live progress via `GET /events` (SSE).
- Job state machine: `queued → running → completed | failed`.
- No synchronous AI calls in HTTP handlers (local inference is slow by definition).

### 31.3 V1 auth baseline

- Dev-mode single user + JWT; project-scoped roles: `owner` / `member` / `viewer`.
- Code-apply/approve requires `member`+; project deletion requires `owner`.
- Every approval is written to `ai_actions` (auditable).
- SSO / RBAC / billing stay in Phase 8 as planned.

### 31.4 Repository connection model

- V1: **local workspace path or `git clone`** (GitHub via PAT/deploy key). No zip uploads.
- **gitleaks scan before indexing and before any AI context assembly** (fail closed).
- Only selected files/symbols enter AI context (§14).

### 31.5 Observability

- Phase 0: structured JSON logs + `ai_actions` audit (model, tokens, latency, approval status).
- Prompt debugging: redacted prompt/completion snapshots stored via `ai_actions.output_ref`.
- Optional: self-hosted Langfuse compose profile when deeper tracing is needed (Phase 5+).

### 31.6 Prompt registry

- Prompts are versioned files: `packages/ai/prompts/{agent}.{name}.v{n}.md` with front-matter (`model_class`, budgets, `schema_ref`, `temperature`).
- Agents reference prompts by `name@version`; runtime resolves via the `prompt_versions` table.
- Golden evals (§22) pin `name@version` → prompt changes are regression-tested, never silent.

### 31.7 Numeric evaluation targets

| Metric | Target |
|---|---|
| Schema-valid AI output | ≥ 99% |
| Failure classification top-1 (30-test broken set) | ≥ 80% |
| Generated code passes lint + type | ≥ 95% |
| Test-design step coverage vs oracle | ≥ 85% |
### 31.8 Demo application

Per §23: own repo, React + Vite + Express + SQLite, env-flag defect injection mapping 1:1 to the failure taxonomy; run it as a compose service so Playwright workers reach it via `APP_UNDER_TEST`.

### 31.9 Monorepo tooling

- **uv** workspace (Python packages/apps) + **pnpm** (web); ruff + mypy + pytest; ESLint + Prettier; pre-commit (lint + type + secret scan).
- One-command up: `docker compose up` (infra) · `uv run uvicorn` (api) · `pnpm dev` (web).

### 31.10 Accessibility

- `@axe-core/playwright` is the standard a11y assertion engine for generated tests (Playwright has no native a11y engine).

### 31.11 Execution details

Per §15: `APP_UNDER_TEST` URL via compose service; tracing on with retain-on-failure; pinned Playwright browser image; artifacts under `runs/{run_id}/{test_id}/{type}`; retention default 30 days, configurable.

---

## 32. Agent memory & session-continuity protocol (v1.1 — critical)

**Why:** development runs across many short AI chat sessions with limited context. The project must be resumable from *any* new session with zero prior conversation.

**The folder — `agent-memory/` (committed to git):**

| File | Purpose |
|---|---|
| `STATE.md` | **Single source of truth.** Current phase/step, in-progress status, *next step + its exit criterion*, environment facts, open issues, pointers (paths only, no code). Keep ≤ ~150 lines. |
| `SESSION_LOG.md` | Append-only history: date, goal, what changed, what was verified, decisions, where the next session starts. |

**Session protocol:**

1. **Start:** read `agent-memory/STATE.md` first (small by design). Then read *only* the files the next step touches. Do not re-read whole modules or the codebase.
2. **Work:** stay inside one step (§19). If context runs low mid-step: write an "in-progress" note into `STATE.md` and stop cleanly.
3. **Verify:** run the step's exit criterion — prefer small, filtered commands (e.g. `pytest tests/unit/test_x.py -q`) over broad ones.
4. **Commit:** `step S#.x: <summary>`. Git history is cheap long-term memory — `git log --oneline` is the session map.
5. **End:** update `STATE.md` (status → next step + decisions); append `SESSION_LOG.md`; commit.

**Rules:**

- Never re-decide: decisions live in §29 + `STATE.md`.
- `STATE.md` holds **pointers, not code** — no pasted snippets.
- Prune: when a step completes, move its detail from `STATE.md` into `SESSION_LOG.md`.
- `agent-memory/` is always committed — it *is* the handoff memory.