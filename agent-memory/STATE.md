# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.2 in progress — `docker-compose.yml` committed; Docker Desktop installed; **BLOCKED on BIOS**
- **In-progress note:** Docker engine can't start — **Intel VT-x disabled in firmware** (ASUS ROG Strix
  SCAR 18, Core Ultra 9 275HX). User must enable VT-x in UEFI (F2 at boot → Advanced → CPU
  Configuration → Intel Virtualization Technology = Enabled), reboot, then start Docker Desktop.

## 2. Just completed

- 2026-08-26 · **S0.2 (cont.) — Docker Desktop installed + engine blocker diagnosed**:
  Docker 29.7.2 (per-user install, `%LocalAppData%\Programs\DockerDesktop`; CLI+Compose v5.4.0
  on the **USER** PATH — pre-existing terminals don't see it). Engine will not start:
  `docker info` → 500 on pipe `dockerDesktopLinuxEngine`. **Root cause:
  `VirtualizationFirmwareEnabled = False`** (Intel VT-x off in UEFI) → WSL2 and Hyper-V
  backends both impossible until enabled in BIOS. WSL2 kernel also not installed yet.
- 2026-08-26 · **S0.2 (partial) — `docker-compose.yml` committed**: `pgvector/pgvector:pg16`
  (db) + `redis:7`, healthchecks, named volumes, env-overridable ports
  (`${POSTGRES_PORT:-5432}`, `${REDIS_PORT:-6379}`); credentials qa/qa @ qa_copilot
  match `.env.example` (DATABASE_URL, REDIS_URL verified). **Exit criterion (psql +
  redis ping) PENDING** — Docker Desktop not yet installed.
- 2026-08-26 · **S0.2 infra decision = Option A (Docker Desktop)** — standard build-bible
  path (pgvector prebuilt in image, portable compose artifact). New env facts discovered
  this session: **Windows-native PostgreSQL 16 is installed + RUNNING** (service
  `postgresql-x64-16`, port 5432, scram auth, **no pgvector**) → port-conflict risk at
  bring-up; **WSL not installed**; Redis not installed.
- 2026-08-26 · **S0.1 — Monorepo skeleton (python half)** — uv workspace (virtual root
  + 7 members: `apps/api`, `packages/{domain,ai,repository,execution,knowledge,integrations}`),
  ruff + mypy (strict) + pytest config, pre-commit (ruff/mypy/gitleaks), `.env.example`,
  `uv.lock` + `.venv`, scaffold smoke test. Web half (pnpm/ESLint/Prettier) pending Node LTS.
  Verified: ruff check+format ✓ · mypy strict ✓ (7 files) · pytest ✓ (2 passed). Commit `ed6dcaf`.
- 2026-08-26 · Bootstrap: build bible **v1.1** (canonical) + `agent-memory/` + `README.md`
  (details in SESSION_LOG).

## 3. NEXT STEP (start here)

**S0.2 (finish) — bring up compose infra + verify exit criterion** (build bible §19, Phase 0)
1. **BLOCKER (user action, requires reboot):** enable **Intel VT-x** in UEFI — shut down →
   power on → tap **F2** (ASUS ROG) → Advanced → CPU Configuration →
   *Intel Virtualization Technology* = Enabled (VT-d optional) → F10 save → boot Windows.
2. Start **Docker Desktop** (may prompt for WSL2/Hyper-V backend setup — let it, or run
   `wsl --install` as admin + reboot if it asks). Confirm engine: `docker info` OK (no 500).
3. PATH note: docker is on the USER PATH — refresh long-lived shells:
   `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`
4. Port 5432 conflict: native PG16 service `postgresql-x64-16` is listening — confirm with
   user: **stop the service** (keep standard 5432 URLs) vs `POSTGRES_PORT=5433` in `.env`
   + DATABASE_URL on 5433.
5. `docker compose up -d`, wait for healthy, then verify **exit criterion**:
   `docker compose exec db psql -U qa -d qa_copilot -c 'SELECT 1'` → `1`
   `docker compose exec redis redis-cli ping` → `PONG`
6. Commit `step S0.2: compose infra up (pgvector/pg16 + redis7), exit criterion verified`,
   then start **S0.3** — FastAPI skeleton: app factory, pydantic-settings, JSON logging,
   `GET /health` → 200.

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- **Docker: installed 2026-08-26** (Desktop, per-user; CLI v29.7.2, Compose v5.4.0, on USER PATH)
  — engine DOWN until **Intel VT-x enabled in UEFI** (`VirtualizationFirmwareEnabled=False`)
- **Hardware:** Intel Core Ultra 9 275HX · ASUS ROG Strix SCAR 18 (G835LX) — BIOS entry: F2
- **PostgreSQL 16 (Windows-native): installed + RUNNING** (service `postgresql-x64-16`,
  `C:\Program Files\PostgreSQL\16`, data dir `...\16\data`, scram-sha-256 auth,
  **no pgvector**, port 5432) → potential conflict with compose db port
- **WSL: NOT installed** (Docker Desktop may set it up at first start; else `wsl --install`)
- **Node/npm/pnpm: NOT installed** → S0.7 (React shell) needs Node 20+
- Toolchain in `.venv` (uv 0.11.32): ruff 0.16.4 · mypy 2.3.1 · pytest 9.1.1 · pre-commit 4.6.2
- LLM: **local model via llama server** (user-run). Confirm exact `LLM_BASE_URL` + model
  name/size before S0.6 (needed to tune §31.1 budgets). Assume OpenAI-compatible.
- python-docx ✗ — Markdown is the doc source of truth (no .docx regeneration)

## 5. Key decisions (full list: build bible §29)

- Local LLM only, no cloud · hard context budgets · text-first failure analysis
- Async jobs mandatory (202 + SSE) — local inference is slow
- One step per session · verify exit criterion · commit `step S#.x` · update this file
- Markdown is canonical; do not re-derive decisions already in §29

## 6. Pointers (paths only — no code here)

- Build bible: `docs/AI_QA_Copilot_Build_Bible_v1.1.md`
- Session history: `agent-memory/SESSION_LOG.md`
- v1.0 original (PDF, historical): `c:\Users\manve\Desktop\AI_QA_Copilot_Build_Bible.pdf`
- Demo app (later, S0.10): separate repo `ai-qa-copilot-demo-app`

## 7. Open questions / gotchas

- **PATH gotcha:** docker CLI lives on the USER PATH (per-user install) — terminals opened
  before install don't see it; refresh `$env:Path` from Machine+User (or open a new shell).
- **S0.2 bring-up:** port 5432 held by Windows-native PG16 service — confirm with user
  (stop service vs `POSTGRES_PORT=5433` in `.env` + update DATABASE_URL) before `docker compose up -d`.
- **S0.1 web half pending:** pnpm workspace + ESLint + Prettier + `pnpm lint` — blocked on
  Node LTS (also needed for S0.7 React shell).
- Exact llama-server URL + model(s) (small vs coder class) — needed at S0.6.
