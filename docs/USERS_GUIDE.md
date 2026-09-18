# AI QA Copilot — User Guide (for humans, not engineers)

> **Who this is for:** manual testers, QA leads, and managers who want to understand
> and use the tool without reading source code. If you have never written code,
> that is exactly who this guide is written for — every step is explained.
> **Companion docs:** `README.md` (developer quickstart) ·
> `docs/AI_QA_Copilot_Build_Bible_v1.1.md` (the full engineering blueprint).
>
> **Freshness:** current as of 2026-09-17 (matches `agent-memory/STATE.md`).

---

## 1. What is this tool, in one sentence?

**It is an AI assistant for QA teams that takes a written requirement and turns it
into test cases — then writes the automated browser tests for them, runs them,
explains what failed, and drafts a fix that a human must approve.**

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
   generated test lands in a review queue where someone must approve and apply it,
   and every proposed fix is shown for review before it is applied (with no
   terminal to ask in, it fail-safes to *not* applying).
2. **Everything is local.** The AI runs on a model on this machine (LM Studio).
   Your requirements and code never leave the building. There is no cloud account.

---

## 2. Jargon decoder (skim this first if you're not technical)

| Word you'll keep seeing | What it actually means |
|---|---|
| **Terminal** (PowerShell) | A plain text window where you type commands. Open it from the Windows start menu (search "PowerShell"). Right-click inside it to paste. |
| **Command** | One line of instructions typed into the terminal, e.g. `pnpm dev`. |
| **Port** | A "door number" on your computer that a program listens on, e.g. `5173`. Your browser reaches the web app by visiting `http://127.0.0.1:5173` — `127.0.0.1` means "this computer". |
| **API** | A set of fixed addresses the web app calls so the server does things for it. You'll mostly never touch it directly. |
| **HTTP call** | A request from one program to another, like a form submission. `GET` = read something, `POST` = do something. |
| **Database** | The permanent filing cabinet (PostgreSQL) holding requirements, test cases, runs, audit rows, and more. |
| **Docker** | A way to run programs in sealed boxes (containers) so they can't mess up your machine. We use it for the database and a helper called Redis. |
| **Local AI model** | The AI "brain" (Qwen3.8-27B) running on this laptop via LM Studio — not in a cloud. Slower, private, free. |
| **Token** | A chunk of text (about three-quarters of a word). Models read and write in tokens; we cap how many each AI call may use so it can't ramble. |
| **JSON** | A rigid text format for data — like a form with fixed fields. The AI must fill in "forms" instead of writing essays, which is what lets the UI display clean results. |
| **Job** | A piece of long-running AI work. You get a job number instantly and watch it progress live instead of staring at a spinner. |
| **Live stream (SSE)** | The pipe that pushes job progress to your browser as it happens (the pipeline stages lighting up one by one). |
| **Playwright** | The browser-automation tool the generated tests use to click, type, and check things in the demo shop. |
| **Repository (repo)** | A folder of source code (usually under Git). Approved generated tests are written into the repo you point at. |
| **Artifact / evidence** | The proof files a test run produces: screenshots, videos, traces, console/network logs. |
| **`.env` file** | A plain-text settings file with the machine-specific answers (which model, which ports, the dev login password). Already configured for this machine. |

---

## 3. How the system is built (plain language)

The tool is built in layers, like a restaurant kitchen. You only ever talk to the
waiter (the web UI); behind the scenes there is a clear division of labour.

### Layer 1 — The face: the web app (React, port 5173)

The dark-themed page at `http://127.0.0.1:5173`. After you sign in, it has **five tabs**:

| Tab | What it shows |
|---|---|
| **Test design** | A form to describe a requirement; the live six-stage pipeline; the test cases the AI designed; a **past requirements** list of everything you've designed in this project; and a log of every event with timestamps |
| **Generated tests** | The review queue of AI-written Playwright test code: read the proposal, then **✓ Approve & write test** (writes the file into the target repo) or **✗ Reject** |
| **Runs** | History of test executions: pass/fail per test, timing, the AI failure diagnosis, and downloadable evidence (screenshots, traces, videos, logs) |
| **Knowledge** | The project's memory: index the code + requirements + history, search it by keyword, and ask it questions that are answered *from* that corpus — with citations, and an honest "I don't know" when the corpus is silent |
| **Regression** | "I changed these files — which tests should I run?": a risk-ranked impact analysis, a recommended test set, and a one-click button to run exactly that set |

### Layer 2 — The office: the API server (FastAPI, port 8000)

Every click in the UI becomes an HTTP call to this server. It is the **only place
allowed to touch the database**, and it enforces:

- **Login & roles** — you must be signed in, and different actions need different
  permission levels (viewer / member / owner — the owner can also manage members,
  plan, and integrations).
- **The "job" pattern** — AI work takes minutes, so it is *never* done while your
  browser waits. Instead the server answers instantly with a **job number**
  (like a restaurant ticket), works on it in the background, and pushes live
  progress updates over a stream. That is why you see pipeline stages light up
  one by one — and why the header shows a green **"SSE live"** dot while a job
  runs. If your browser falls asleep, the job keeps going on the server.

### Layer 3 — The brain: AI agents behind a single "AI gateway"

The clever part is **not** one big AI, but a small team of specialist "agents",
each with one job and strict rules:

| Agent | Job (what it does for you) | Status |
|---|---|---|
| Requirement Analyst | Reads your requirement, extracts actors, rules, risks, and acceptance criteria | ✅ working |
| Test Designer | Turns the requirement into structured test cases (functional, negative, boundary, risk, a11y) | ✅ working |
| Automator | Writes Playwright test code that follows the project's existing style | ✅ working (review queue) |
| Executor | Runs the Playwright tests and captures screenshots, video, traces, logs | ✅ working |
| Failure Investigator | Explains *why* a test failed, with the evidence that proves it | ✅ working |
| Fix Proposer | Drafts a patch — **applied only after a human approves it** | ✅ working (approval-gated) |
| Knowledge Q&A | Answers questions grounded in the project's indexed corpus, with citations | ✅ working |
| Regression Advisor | Deterministic change-impact + risk ranking (a human-written summary is the only AI part) | ✅ working |

Safety rules around the brain (non-negotiable product principles):

- **One door in, one door out.** Every model call goes through a single *AI
  gateway* — so timeouts, retries, token budgets, and secret-redaction are
  applied everywhere, consistently.
- **Budgets.** Every agent has a hard limit on how much context it may read and
  how much it may write (local models have small memory — this prevents garbage
  output and runaway token use).
- **Structured answers.** Agents must return machine-readable JSON (e.g. a test
  case with title / steps / expected result), not free prose. That is what lets
  the UI render clean test cases and lets the team measure quality automatically.
- **Audit trail.** Every AI call is logged (which model, how long, how many
  tokens, what it produced) and every sensitive action — logins, approvals,
  quota denials — is written to an append-only audit log. A manager can answer
  "what did the AI — or the user — actually do?"

### Layer 4 — The filing cabinet: storage

| What | Where | Why |
|---|---|---|
| Requirements, test cases, runs, results, review queue, audit log | **PostgreSQL** (in Docker, port **5433** on this machine) | Durable, relational, searchable |
| Vector search for project knowledge | pgvector extension in the same database | "Search by meaning", not just keywords |
| Job queues / event streams / rate limiting | **Redis** (in Docker, port 6379) | Fast, temporary coordination |
| Test evidence (screenshots, videos, traces, logs) | **Files on disk** under `ai-qa-copilot\data\artifacts\runs\…` | Big binary files don't belong in a database |

### Layer 5 — The lab rat: the demo app (separate folder `ai-qa-copilot-demo-app`)

A small, fake e-commerce site (login → products → cart → checkout) with a demo
user **qa / qa1234**. It exists **only** so the copilot has a safe, synthetic app
to test against — real customer code is never involved. Its special trick: you
can switch on **known defects** (a renamed button, a failing checkout API, random
slowness, missing order data) so the failure-analysis stage can be evaluated
against *known* answers. See Section 7.7.

### The port map (which "door" is which)

| Port | Who lives there | How to check it's alive |
|---|---|---|
| 8080 | LM Studio (the AI brain) | `curl.exe http://localhost:8080/v1/models` → JSON listing the model |
| 8000 | Copilot API server ("the office") | `curl.exe http://127.0.0.1:8000/health` → `{"status":"ok",…}` |
| 5173 | Copilot web app (what you click) | browser: `http://127.0.0.1:5173` |
| 5433 | PostgreSQL (the database) | `docker compose exec db psql -U qa -d qa_copilot -c 'SELECT 1'` → prints `1` |
| 6379 | Redis (the coordinator) | `docker compose exec redis redis-cli ping` → `PONG` |
| 5174 | Demo shop (the app under test) | browser: `http://localhost:5174` |
| 4000 | Demo shop's backend | `curl.exe http://localhost:4000/health` |

> Note: on most machines the database would sit on 5432; on **this** machine a
> Windows-native PostgreSQL already holds 5432, so the copilot's database was
> moved to **5433** (already set in `.env`).

### How a single request travels (end to end)

```
you (browser, :5173)
   │  "Design test cases for: <requirement>"
   ▼
API server (:8000)
   │  1. checks you're signed in and have permission
   │  2. saves the requirement to the database
   │  3. starts a background AI job → replies with a job number (202)
   │  4. pushes live progress updates to your browser (SSE stream)
   ▼
AI gateway → one of the agents (local model via LM Studio, :8080)
   │  produces structured JSON (requirement analysis → test cases)
   ▼
API server stores the result in PostgreSQL (port 5433)
   │
   ▼
your browser shows the pipeline complete + the test-case list
```

Nothing about any of this requires you to know the details above — but if
something ever breaks, knowing *which layer* is failing tells you which part of
the stack to check (see the troubleshooting sheet in Section 8).

---

## 4. Before you start: the checklist

Everything below is **already installed on this machine** — this section exists so
you can *verify* each piece, and so the guide still works if you move to another PC.

| # | What | Why you need it | How to check (in a terminal) | Expected result |
|---|---|---|---|---|
| 1 | Docker Desktop | Runs the database + Redis in sealed boxes | `docker version` | client + server versions print (whale icon in the taskbar) |
| 2 | Node.js 22 + pnpm | Runs the web apps (copilot UI + demo shop) | `node -v` and `pnpm -v` | `v22.x` and a pnpm version number |
| 3 | uv (the Python manager) | Runs the API server and the AI/execution packages | `uv --version` | a version number |
| 4 | LM Studio with the model loaded | The local AI brain | `curl.exe http://localhost:8080/v1/models` | JSON listing the Qwen3.8-27B model |
| 5 | The two project folders | The code itself | — | `c:\Users\manve\Workspace\ai-qa-copilot` and `c:\Users\manve\Workspace\ai-qa-copilot-demo-app` |

> ⚠️ **Check LM Studio first** — it is the number-one cause of "the app runs but
> the AI does nothing real".

### LM Studio, step by step (if you've never used it)

1. **Open LM Studio** (start menu).
2. **Load the model:** on the *Models* tab, find **Qwen3.8-27B (Q4_K_M GGUF)**
   (folder `lmstudio-community`) and click it. It is "loaded" when the
   *Loaded* indicator appears next to it.
3. **Start the local server:** on the *Developer* tab → **Local Server** →
   make sure the port is **8080** (that's where the copilot's `.env` points)
   → **Start Server**.
4. **Prove it works** from a terminal:

   ```powershell
   curl.exe http://localhost:8080/v1/models
   ```

   You should see a JSON list that includes the Qwen3.8-27B model.

> **Why is it slow?** A 27-billion-parameter model running on a laptop is
> deliberately slow — that is the price of privacy (nothing leaves the machine).
> A single AI job typically takes **several minutes**. That is normal, not a hang.

### What `.env` already sets on this machine (no action needed)

The file `ai-qa-copilot\.env` already contains the machine-specific settings:

| Setting | Value | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `http://localhost:8080/v1` | Where the AI brain listens (LM Studio) |
| `LLM_MODEL` | `.\Models\lmstudio-community\Qwen3.8-27B-GGUF\Qwen3.8-27B-Q4_K_M.gguf` | Which model to ask |
| `POSTGRES_PORT` / `DATABASE_URL` | `5433` | The database door (5432 is taken on this PC) |
| `APP_UNDER_TEST` | `http://localhost:5174` | Where the demo shop lives |
| `AUTH_DEV_PASSWORD` | `dev-password` | The password for the dev login `dev@local.dev` |

---

## 5. Starting the app (follow these steps in this exact order)

You need **three or four terminal windows** (one per moving part). To open one:
press **Win + X → Terminal** (or search "PowerShell" in the start menu).
A few words you'll see in the commands: `cd <folder>` = "walk into that folder";
`Ctrl+C` = "stop this program"; right-click in a terminal = paste.

### Step 1 — start the database + queue (Docker)

First make sure **Docker Desktop is running** (whale icon in the taskbar; if not,
start it from the start menu and give it a minute to settle). Then:

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
docker compose up -d        # "-d" = quietly in the background

# sanity checks — you should see a table containing "1", then the word "PONG"
docker compose exec db psql -U qa -d qa_copilot -c 'SELECT 1'
docker compose exec redis redis-cli ping
```

If `docker compose up` ever complains that **port 5432** is in use: on this
machine the native PostgreSQL Windows service may hold it. The copilot's
`.env` already routes its database to **5433**, so there is no collision — if you
still get an error, check that both `POSTGRES_PORT` and `DATABASE_URL` in `.env`
say `5433` (they do by default here).

### Step 2 — create the tables + demo data (safe to re-run any time)

```powershell
uv run alembic upgrade head        # creates/updates all database tables
uv run python scripts/seed.py      # adds the demo login + demo project (never duplicates)
```

`seed.py` creates the login **dev@local.dev / dev-password**, the organization
"Acme Dev", the project "Demo App", and a couple of sample requirements. It
*never* overwrites things that already exist, so you can re-run it freely.

### Step 3 — start the API server ("the office")

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
uv run uvicorn qa_copilot_api.main:app --port 8000
```

In **another** window, verify it's alive:

```powershell
curl.exe http://127.0.0.1:8000/health
# → {"status":"ok","service":"qa-copilot-api", ...}
```

**Leave this window open — it is the server.** Stop it later with `Ctrl+C`.

### Step 4 — start the web app ("the face")

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
pnpm install        # first time only — downloads the web app's building blocks
pnpm dev
# → open http://127.0.0.1:5173 in your browser
```

### Step 5 (optional) — start the demo shop (needed for the Execution stage)

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot-demo-app
pnpm install        # first time only
pnpm dev
```

This starts two things together: the fake shop you can browse at
`http://localhost:5174` (sign in with **qa / qa1234**) and its backend on
`http://localhost:4000` (check with `curl.exe http://localhost:4000/health`).

### Stopping everything (reverse order)

```powershell
# 1. in each pnpm window:            Ctrl+C      (web app + demo shop)
# 2. in the uvicorn window:          Ctrl+C      (API server)
cd c:\Users\manve\Workspace\ai-qa-copilot
docker compose down                 # 3. database + redis — your data is KEPT
```

> `docker compose down -v` would **wipe the database** as well — only do that
> when you deliberately want a clean slate.

---

## 6. Your first run, step by step

### 6.1 Sign in

1. Open `http://127.0.0.1:5173` in your browser.
2. If it shows "Loading…" forever, the API server is down — go back to Section 5.
3. Enter **Email:** `dev@local.dev` · **Password:** `dev-password` → **Sign in**.
4. The header now shows your project name ("Demo App") and your email.

### 6.2 Design test cases (the main workflow)

On the **Test design** tab:

1. **Title** — e.g. `Checkout totals`.
2. **Description** — plain English: what the product should do.
3. **Acceptance criteria** — one bullet per line (optional, but improves results).
4. Click **Design test cases**.

What happens next:

- The button turns to **Running…** and a `job <number>` appears next to it.
- The **six-stage pipeline** animates below (Requirement → Test design → …).
  The green **SSE live** dot in the header means your browser is receiving
  live updates.
- **This takes a while** — several minutes, by design (local model). Don't close
  the tab; the job keeps running on the server even if the stream drops.
- When it completes, the full requirement and its **structured test cases**
  (title, type, priority, steps, expected result) appear below the pipeline.
- The **event log** at the bottom is your audit trail: every stage transition,
  with timestamps.
- The requirement also joins the **past requirements** list under the output
  (newest first, with its test-case count). Click any row to re-open it — no
  need to re-run the AI.

**Manager's tip:** ask the AI to design cases for *one* requirement at a time and
read them as a QA peer would — the value is in the negative/boundary cases it adds
that a rushed human pass usually misses.

---

## 7. Day-to-day use of the five tabs

### 7.1 Accounts, teams and limits (the basics)

- **Dev login:** `dev@local.dev` / `dev-password` (created by `scripts/seed.py`;
  the password comes from `AUTH_DEV_PASSWORD` in `.env`).
- **Teams exist now:** new people can register
  (`POST /api/v1/auth/register`), and an owner can add members, send invites, and
  change roles. Passwords must be at least 10 characters with a letter and a digit.
- **Roles:** owner (everything, including members/plan/integrations) > member
  (approve tests, run jobs, …) > viewer (read-only).
- **Quotas:** each organization has a plan (`free` / `pro` / `enterprise`) with
  caps on projects, runs per month, AI tokens per month, and concurrent jobs.
  When a cap is hit the job is rejected with a clear "plan limit" message (409) —
  an owner can check `GET /api/v1/organizations/{id}/usage` and raise the plan
  via `PATCH /api/v1/organizations/{id}`. Repeatedly *failed* logins are
  throttled (5 failures a minute per email/IP → a 429 with a "try again in
  N s" hint; a correct login resets the counter). Full request rate limiting
  (120 requests per 60 s) is **off by default** in this local setup — it only
  turns on in the production compose file — so you won't hit it day to day.

### 7.2 Generated tests tab — reviewing the AI's code

The Automator agent produces Playwright test code, but it **never writes to disk
on its own**. Each proposal becomes a row in the **review queue**, and this tab is
where you make the call:

1. Click a row to read the proposed code (target file path, framework, notes) and
   its status badge: `pending` (amber) → `approved` (blue) → `applied` (green),
   or `rejected` (red).
2. **✓ Approve & write test** — approves the row *and* writes the file into the
   target repository (`<repository_path>/<file_path>`). The API refuses to
   silently overwrite an existing file (a 409 error instead) — regenerate the
   test to create a new proposal.
3. **✗ Reject** — the row is discarded (a second click confirms, so you can't do
   it by accident).

Every one of these actions is audited (who, when, note).

For API users (member role or above):

- `GET  /api/v1/projects/{id}/generated-tests` — the queue
- `POST /api/v1/automation/generate` — create a new proposal (202 + job)
- `POST /api/v1/generated-tests/{id}/approve` — you say "yes"
- `POST /api/v1/generated-tests/{id}/reject` — you say "no"
- `POST /api/v1/generated-tests/{id}/apply` — `pending|approved → applied`;
  writes the file into the repository

### 7.3 Runs tab — executions and evidence

The **Runs tab** lists every execution of the Playwright suite, newest first:

- run status (green = passed / red = failed / amber = running or flaky), commit,
  start/end, duration, pass/fail totals;
- **per-test results**, including the **AI failure diagnosis** when a test
  failed (the Failure Investigator's explanation plus the evidence);
- **evidence**: an inline screenshot preview, plus downloads of trace, video,
  console, network, DOM and log files for any test (they live on disk under
  `ai-qa-copilot\data\artifacts\runs\…`).

**How do runs get started?** Three ways: the **Regression** tab's
"Run this set" button (the recommended one), the API
(`POST /api/v1/projects/{id}/runs`), or the manual worker command in 7.7.

A manager's read: **green = the product behaved as specified · red = something
is wrong, and the evidence links tell you where to look.**

### 7.4 Knowledge tab — the project's memory

This is where the copilot *remembers* the project. Three actions:

1. **Index knowledge** — the copilot assembles the project corpus: the stored
   requirements, test cases and run history, plus your repository files if you
   fill in the optional *Repository path* field. It's a job, so it takes a
   little while.
2. **Search** — keyword search over that corpus (a *Top-k* picker — 1 to 5,
   default 5), plus a browser for every stored document.
3. **Ask** — ask a question in plain English ("what do we know about checkout
   discounts?") and the AI answers **only from the indexed corpus, with
   citations** — if the corpus doesn't cover it, it says so instead of guessing.

Useful API calls (member role or above):

- `POST /api/v1/projects/{id}/knowledge/index` — build the index (202 + job)
- `GET  /api/v1/projects/{id}/knowledge/status` — is it indexed? when?
- `GET  /api/v1/projects/{id}/knowledge?q=checkout` — keyword search
- `GET  /api/v1/projects/{id}/knowledge/documents` — browse stored documents
- `POST /api/v1/projects/{id}/knowledge/ask` — grounded question (202 + job)

### 7.5 Regression tab — "what should I test after this change?"

Give it what changed; it tells you what to run:

1. Fill in the **Repository checkout path (server-local)** — the folder on this
   machine that holds the code and the Playwright tests — then pick a source:
   a **list of changed file paths** (one per line), two git refs (base → head),
   or a pull request (owner/repo/number).
2. Click **Analyze** (a job). You get three things, in order (plus an optional
   **Advisor summary** — a short AI brief, when the LLM is available):
   - **Impact set** — the tests that touch the changed code (direct / generated /
     referenced — all from real provenance links, no guessing);
   - **Risk ranking** — each affected test scored low → medium → high → critical
     (flakiness and history count);
   - **Recommended top-N** — the focused regression set worth running right now
     (a *Top-N* picker, default 10).
3. Click **Run this set (N)** — N is how many tests you've selected. It
   starts a real test run through the normal execution path; the result lands
   in the **Runs** tab.
4. (If a GitHub integration is configured and you used the pull-request source)
   **Post to PR** drops the recommendation straight onto the pull request —
   first post creates the comment, re-posts update it, identical re-posts are
   a no-op.

API: `POST /api/v1/projects/{id}/regression/analyze` (202 + job) ·
`POST /api/v1/projects/{id}/regression/pr-comment` (202 + job).

### 7.6 Integrations (Jira / GitHub) — brief tour

- **Integration config:** `GET/PUT/DELETE /api/v1/projects/{id}/integrations[/{provider}]`
  (providers: `github`, `jira`).
- **Jira:** link a diagnosed failure to a Jira issue —
  `POST /api/v1/projects/{id}/failures/{failure_id}/jira` (owner).
- **GitHub webhook:** `POST /api/v1/webhooks/github` — lets GitHub push events
  (for example pull requests) into the copilot.

### 7.7 (For the curious) commands that live outside the UI

**Run the demo app's test suite directly** (plain Playwright):

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot-demo-app
pnpm exec playwright test
```

**Run it through the copilot's execution worker** (same tests, plus the artifact
store layout):

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
uv run python -m qa_copilot_execution c:\Users\manve\Workspace\ai-qa-copilot-demo-app --json
```

Exit codes: `0` all passed · `1` run completed but some tests failed ·
`2` bad command line · `3` the worker itself failed.

**Inject a known defect and watch it get caught.** Start the demo server with a
flag (one at a time), run the suite, then restart clean:

| Flag | What breaks | What it teaches the AI |
|---|---|---|
| `DEFECT_LOCATOR_DRIFT=1` | UI button ids renamed/removed | "automation broke" vs "product broke" |
| `DEFECT_API_500=1` | checkout API returns 500 | a real product defect |
| `DEFECT_FLAKY=1` | random 300 ms–3 s delays | flakiness, not failure |
| `DEFECT_BAD_DATA=1` | orders come back without line items | bad test data |

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot-demo-app
$env:DEFECT_API_500=1; pnpm --filter demo-server start   # checkout will now 500
# see which defects are currently active:
curl.exe http://localhost:4000/api/config
# stop with Ctrl+C, then `pnpm dev` again for a clean shop
```

This is how the team *proves* the failure-analysis stage diagnoses real
failures: fail on purpose, against a known answer key.

**Run the full fix loop (diagnose → propose → approve → re-run):**

```powershell
cd c:\Users\manve\Workspace\ai-qa-copilot
uv run python scripts/loop_run.py            # shows the patch, asks y/n
uv run python scripts/loop_run.py --approve  # automation path: apply + re-run
uv run python scripts/loop_run.py --reject   # decline: nothing is applied
```

The approval gate is deliberate: with no explicit yes (and no terminal to ask
in), the loop **fail-safes to rejecting the patch** — the product never
auto-heals itself.

---

## 8. Troubleshooting cheat-sheet

| Symptom | First thing to check |
|---|---|
| Browser says "Loading…" forever, or login fails | The API server — is `http://127.0.0.1:8000/health` up? (restart step 3) |
| Job runs forever or fails with LLM errors | LM Studio: server running on **:8080**? Model loaded? `curl.exe http://localhost:8080/v1/models` should list it |
| Job fails with "no JSON" / schema errors | The model output was likely truncated — re-run the job; if it persists, it's a prompt/budget issue for an engineer to look at |
| API crashes on startup with database errors | Docker Desktop running? `docker compose ps` should show `db` as healthy |
| `Port 5432 already in use` during `docker compose up` | Expected on this machine if the native PostgreSQL service runs — `.env` already routes the database to **5433**; confirm `POSTGRES_PORT` and `DATABASE_URL` both say 5433 |
| Web UI shows "No project membership" | The dev user needs a project — re-run `uv run python scripts/seed.py` |
| Form is greyed out / button says "Running…" but nothing is happening | The UI is still attached to a stale job (e.g. the API restarted mid-run and the live stream died). Click **Start over** (next to the job id on the Test design tab) or refresh the page, then submit again |
| `curl.exe http://localhost:8080/v1/models` says "connection refused" | LM Studio's server isn't started (or is on a different port) — Developer tab → Start Server on 8080, or point `LLM_BASE_URL` in `.env` at the real port |
| A job is rejected with a "plan limit" (409) message | A quota was hit (runs/tokens/concurrent jobs). An owner can check `GET /api/v1/organizations/{id}/usage` and raise the plan via `PATCH /api/v1/organizations/{id}` |
| You get 429 responses in bursts | A *failed* login is being throttled (5 failures/minute per email/IP) — wait the shown "try again in N s" window and log in correctly (a good login resets it). Full request rate limiting (120/60 s) is off by default locally |
| Knowledge tab shows "never indexed" | Index it first (7.4) — search and Ask need the corpus to exist |
| Demo shop won't load on :5174 | Demo app not running (`pnpm dev` in its folder) or its backend on :4000 is down |

---

## 9. Where the product stands (honest status)

Per `agent-memory/STATE.md` (2026-09-17): **the entire six-stage pipeline is
built, tested, and running end to end**, plus the supporting infrastructure:

| Stage / area | Status |
|---|---|
| Requirement analysis | ✅ done |
| Test design + quality evaluation | ✅ done (measured against a "golden set" of expected answers) |
| Automation code generation + human review | ✅ done (Generated tests tab: approve & write / reject) |
| Execution + run history + evidence artifacts | ✅ done |
| Failure analysis (AI explains failures) | ✅ done |
| Fix proposals (always human-approved) | ✅ done (approval-gated fix loop) |
| Project knowledge (index / search / grounded Ask) | ✅ done |
| Regression intelligence (impact, risk, recommended set) | ✅ done |
| Integrations (Jira link, GitHub webhook) | ✅ done |
| Teams, roles, audit trail, plans & quotas | ✅ done (pilot end-to-end verified, 85/85 checks green) |
| SSO/OAuth + real payment processor | 📅 deferred (enterprise scope) |

**In one line for a manager:** every stage — *understand the requirement, design
the tests, write the automation, run it, explain the failures, and propose a fix*
— is built, tested and running locally, with human approval on every code change
and a full audit trail. What remains is enterprise polish (SSO, billing
processors), not core QA capability.

---

## 10. A quick map of where things live

| What | Where |
|---|---|
| The copilot (all code) | `c:\Users\manve\Workspace\ai-qa-copilot` |
| The demo shop (the app under test) | `c:\Users\manve\Workspace\ai-qa-copilot-demo-app` |
| Settings (model, ports, dev password) | `ai-qa-copilot\.env` (template: `.env.example`) |
| Test evidence (screenshots/traces/videos/logs) | `ai-qa-copilot\data\artifacts\runs\…` |
| Server logs | `ai-qa-copilot\logs\` and `api.log` |
| "Where is the project?" (for AI work sessions) | `ai-qa-copilot\agent-memory\STATE.md` |
| Developer quickstart | `ai-qa-copilot\README.md` |
| The full engineering blueprint | `ai-qa-copilot\docs\AI_QA_Copilot_Build_Bible_v1.1.md` |
