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
