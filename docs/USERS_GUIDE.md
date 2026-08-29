# AI QA Copilot — User Guide (for humans, not engineers)

> **Who this is for:** manual testers, QA leads, and managers who want to understand
> and use the tool without reading source code.
> **Companion docs:** `README.md` (developer quickstart) ·
> `docs/AI_QA_Copilot_Build_Bible_v1.1.md` (the full engineering blueprint).

---

## 1. What is this tool, in one sentence?

**It is an AI assistant for QA teams that takes a written requirement and turns it
into test cases — and (as it matures) into automated browser tests, runs them, and
explains what failed.**

Think of it as a very fast junior QA engineer who never sleeps, never forgets a
test case, and always shows its work.

### What problem does it solve?

Today, QA knowledge lives in people's heads and spreadsheets: requirements in Jira,
test cases in Excel, automation in code, failures in logs. The AI QA Copilot keeps
all of that in **one connected flow**:

```
requirement ──> test design ──> automation ──> execution ──> failure analysis ──> fix
   (what          (how to        (browser       (run the       (why did it     (proposed
   should it      test it)       test code)      tests)        break?)         correction)
   do?)
```

That row is called the **six-stage pipeline** and it is the heart of the product.
The web UI you see is just a window into this pipeline.

### Two important promises (the "guardrails")

1. **Nothing is changed without a human saying "yes".** The tool can *propose*
   test code and fixes, but it never writes to a codebase on its own — every
   generated test lands in a review queue where someone must approve and apply it.
2. **Everything is local.** The AI runs on a model on this machine (LM Studio).
   Your requirements and code never leave the building. There is no cloud account.

---

## 2. The system design, step by step (plain language)

The tool is built in layers, like a restaurant kitchen. You only ever talk to the
waiter (the web UI); behind you there is a clear division of labour.

### Layer 1 — The face: the web app (React, port 5173)

The dark-themed page at `http://127.0.0.1:5173`. It has two tabs:

| Tab | What it shows |
|---|---|
| **Test design** | A form to describe a requirement; a live six-stage pipeline; the test cases the AI designed; a log of everything that happened |
| **Runs** | History of test executions: pass/fail per test, timing, and downloadable evidence (screenshots, traces, logs) |

### Layer 2 — The office: the API server (FastAPI, port 8000)

Every click in the UI becomes an HTTP call to this server. It is the **only place
that is allowed to touch the database**, and it enforces:

- **Login & roles** — you must be signed in; different actions need different
  permission levels (viewer / member / owner).
- **The "job" pattern** — AI work takes minutes, so it is *never* done while your
  browser waits. Instead the server answers instantly with a **job number**
  (like a restaurant ticket), works on it in the background, and pushes live
  progress updates over a stream (SSE). That is why you see the pipeline stages
  light up one by one instead of staring at a spinner.
### Layer 3 — The brain: AI agents + a single "AI gateway"

The clever part is **not** one big AI, but a small team of specialist "agents",
each with one job, each with strict rules:

| Agent | Job (what it does for you) | Status |
|---|---|---|
| Requirement Analyst | Reads your requirement, extracts actors, rules, risks, acceptance criteria | ✅ working |
| Test Designer | Turns the requirement into structured test cases (functional, negative, boundary, risk, a11y) | ✅ working |
| Automator | Writes Playwright test code that follows the project's existing style | ✅ working (API + review queue) |
| Executor | Runs the Playwright tests, captures screenshots/video/traces/logs | ✅ working |
| Failure Investigator | Explains *why* a test failed and with what evidence | 🚧 next phase |
| Fix Proposer | Drafts a patch a human can approve | 🚧 later phase |

Safety rules around the brain (these are non-negotiable product principles):

- **One door in, one door out.** All model calls go through a single *AI
  gateway* — so timeouts, retries, token budgets and secret-redaction are applied
  everywhere, consistently.
- **Budgets.** Every agent has a hard limit on how much context it may read and
  how much it may write (local models have small memory — this prevents garbage
  output and runaway cost).
- **Structured answers.** Agents must return machine-readable JSON (e.g. a test
  case with title/steps/expected result), not free prose. That is what lets the
  UI render clean test cases and lets us measure quality automatically.
- **Audit trail.** Every AI call is logged: which model, how long, how many
  tokens, what it produced. Managers can answer "what did the AI actually do?"

### Layer 4 — The filing cabinet: storage

| What | Where | Why |
|---|---|---|
| Requirements, test cases, runs, results, review queue | **PostgreSQL** (in Docker) | Durable, relational, searchable |
| Vector search for project knowledge (later phase) | pgvector extension in the same DB | "Search by meaning", not just keywords |
| Job queues / event streams (later phases) | **Redis** (in Docker) | Fast, temporary coordination |
| Test evidence (screenshots, videos, traces, logs) | **Files on disk** under `data/artifacts/runs/…` | Big binary files don't belong in a database |

### Layer 5 — The lab rat: the demo app (separate folder `ai-qa-copilot-demo-app`)

A small, fake e-commerce site (login → products → cart → checkout) with a demo
user `qa / qa1234`. It exists **only** so the copilot has a safe, synthetic app
to test against. Its special trick: you can switch on **known defects**
(a broken button id, a failing checkout API, random slowness, missing order
data) so the failure-analysis stage can be evaluated against *known* answers.
Real customer code is never involved.

### How a single request travels (end to end)

```
You type a requirement in the UI
        │
        ▼
Web app ──POST /api/v1/requirements/test-cases──▶ API server
        ◀────────── 202 + job_id (instant) ──────┘
API server starts a background job:
   1. Requirement Agent reads your text (via the AI gateway → local LLM)
   2. Test Design Agent drafts the suite (strict JSON schema enforced)
   3. Results are stored in PostgreSQL
   4. Progress events stream back to your browser (the live pipeline)
        │
        ▼
Job done → UI fetches the stored requirement + test cases → you see them
```

Nothing about this step requires you to know any of the above — but if something
ever breaks, knowing *which layer* is failing tells you which team member to ask.


---

## 3. How to run the app (this Windows machine)

**Prerequisites (all already installed here):** Docker Desktop, Node 22 + pnpm,
Python via `uv`, and **LM Studio running** with the Qwen3.8-27B model loaded.

> ⚠️ Check LM Studio first: its server must be on at `http://localhost:8080`
> with the Qwen3.8-27B (Q4_K_M) model loaded. Without it the AI steps fall back
> to a stub (the app still runs, but it won't produce real test design).

Then open **three terminals**, one per part (order matters):

### Terminal 1 — infrastructure (database + queue)

```powershell
# 1a. Start Docker Desktop first (from the taskbar), then:
cd c:\Users\manve\Workspace\ai-qa-copilot
docker compose up -d

# 1b. Sanity checks (expect: "1" and "PONG")
docker compose exec db psql -U qa -d qa_copilot -c 'SELECT 1'
docker compose exec redis redis-cli ping

# 1c. Create the tables + demo data (safe to re-run any time)
uv run alembic upgrade head
uv run python scripts/seed.py
```

### Terminal 2 — the API server (the "office")

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
uv run uvicorn qa_copilot_api.main:app --port 8000

# in a spare window, verify:
curl.exe http://127.0.0.1:8000/health
# → {"status":"ok","service":"qa-copilot-api",...}
```

*Leave this terminal running — it is the server. Stop with `Ctrl+C`.*

### Terminal 3 — the web app (the "face")

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
pnpm install   # first time only
pnpm dev
# → open http://127.0.0.1:5173 in your browser
```

### Optional — the demo app (needed only for the Execution stage)

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot-demo-app
pnpm install   # first time only
pnpm dev       # fake shop: client :5174 + server :4000, login qa / qa1234
```

> **Note:** the copilot's `.env` currently says `APP_UNDER_TEST=http://localhost:3000`,
> but the demo app actually serves on **:5174** (client) / :4000 (API). The demo
> app's own Playwright config already defaults correctly, so running tests works;
> the `.env` value should be corrected to `http://localhost:5174` to avoid confusion.

### Shutting down (reverse order)

```powershell
pnpm dev → Ctrl+C        # web
uvicorn  → Ctrl+C        # api
docker compose down      # infra (data is kept; use `down -v` to wipe it)
```
---

## 4. How to use the app (day-to-day)

### 4.1 Log in

- **Email:** `dev@local.dev`
- **Password:** `dev-password`

(This is a single development user — multi-user teams come with later phases.)

### 4.2 Design test cases from a requirement (the main workflow)

1. On the **Test design** tab, fill in the form:
   - **Title** — e.g. `Checkout totals`
   - **Description** — plain English: what the product should do
   - **Acceptance criteria** — one bullet per line (optional but improves results)
2. Click **Design test cases**.
3. Watch the six-stage pipeline animate. **This takes a while** — a local model
   on a laptop is slow by design (privacy). Don't close the tab; the job keeps
   running server-side.
4. When it completes, the full requirement and its **structured test cases**
   (title, type, priority, steps, expected result) appear below the pipeline.
5. The **event log** at the bottom is your audit trail: every stage transition,
   with timestamps.

**Manager's tip:** ask the AI to design cases for *one* requirement at a time and
read them as a QA peer would — the value is in the negative/boundary cases it adds
that a rushed human pass usually misses.

### 4.3 Review AI-generated test code (via the API today)

The Automator agent produces Playwright test code, but it **never writes to disk**.
Each proposal becomes a row in a **review queue**:

- `GET /api/v1/projects/{id}/generated-tests` — the queue
- `POST /api/v1/generated-tests/{id}/approve` — you say "yes"
- `POST /api/v1/generated-tests/{id}/reject` — you say "no"
- `POST /api/v1/generated-tests/{id}/apply` — only after approval; writes the file
  (and refuses to silently overwrite an existing file)

Every one of these actions is audited (who, when, note). A web UI for this queue
is a near-term improvement — today it is API-level.

### 4.4 Look at test runs (the Runs tab)

The **Runs** tab lists every execution of the Playwright suite:

- run status, commit, start/end, duration, pass/fail totals
- **per-test results**, including the AI failure diagnosis when available
- **evidence**: inline screenshot preview, plus downloads of trace, video,
  console, network, DOM and log files for any test

A manager's read: **green = product behaved as specified · red = something is
wrong, and the evidence links tell you where to look.**

### 4.5 (For the curious) run the test suite manually

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot-demo-app
pnpm exec playwright test            # plain Playwright, same as the copilot worker

# or via the copilot's execution worker (stores artifacts + a run record):
cd c:\Users\manve\Workspace\ai-qa-copilot
uv run python -m qa_copilot_execution c:\Users\manve\Workspace\ai-qa-copilot-demo-app --json
```

### 4.6 (For the curious) inject a known defect and watch it get caught

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot-demo-app
$env:DEFECT_API_500=1; pnpm --filter demo-server start   # checkout will now 500
```

This is how the team *proves* the failure-analysis stage will diagnose real
failures: fail on purpose, against a known answer key.

---

## 5. Troubleshooting cheat-sheet

| Symptom | First thing to check |
|---|---|
| Browser says "Loading…" forever or login fails | API server (terminal 2) — is `http://127.0.0.1:8000/health` up? |
| Job runs forever / fails with LLM errors | LM Studio: server on :1234? Model loaded? (`curl http://localhost:1234/v1/models`) |
| API crashes on startup with DB errors | Docker Desktop running? `docker compose ps` shows `db` healthy? |
| `Port 5432 already in use` during `docker compose up` | `.env` already routes to **5433** on this machine — make sure `DATABASE_URL` matches `POSTGRES_PORT` |
| Test design job "failed" with schema errors | Usually the model output got truncated — re-run the job; if persistent, it's a prompt/budget issue (an engineer should look) |
| Web UI shows "No project membership" | The dev user needs a project — re-run `uv run python scripts/seed.py` |
| Design form is greyed out and the button says "Running…" but nothing is actually running | The UI is still attached to a stale job (e.g. the API restarted mid-run and the event stream died). Click **Start over** (next to the job id on the Test design tab) or refresh the page, then submit again |

---

## 6. Where the product stands (honest status)

Per `agent-memory/STATE.md` (2026-08-28):

| Stage | Status |
|---|---|
| Requirement analysis | ✅ done |
| Test design + quality evaluation | ✅ done (measured against a "golden set" of expected answers) |
| Automation code generation + human review | ✅ done (API-level) |
| Execution + run history + evidence artifacts | ✅ done |
| Failure analysis (AI explains failures) | 🚧 next |
| Fix proposals | 🚧 planned |
| Project knowledge (RAG), regression intelligence, integrations | 📅 later phases |

**In one line for a manager:** the left half of the pipeline — *understand the
requirement, design the tests, write the automation, run it and keep the
evidence* — is built, tested and running. The right half — *explain failures and
propose fixes* — is the next workstream.

