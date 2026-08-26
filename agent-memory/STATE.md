# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.1 complete (python half; web tooling pending Node)
- **In-progress note:** none

## 2. Just completed

- 2026-08-26 · **S0.1 — Monorepo skeleton (python half)** — uv workspace (virtual root
  + 7 members: `apps/api`, `packages/{domain,ai,repository,execution,knowledge,integrations}`),
  ruff + mypy (strict) + pytest config, pre-commit (ruff/mypy/gitleaks), `.env.example`,
  `uv.lock` + `.venv`, scaffold smoke test. Web half (pnpm/ESLint/Prettier) pending Node LTS.
  Verified: ruff check+format ✓ · mypy strict ✓ (7 files) · pytest ✓ (2 passed). Commit `ed6dcaf`.
- 2026-08-26 · Bootstrap: build bible **v1.1** (canonical) + `agent-memory/` + `README.md`
  (details in SESSION_LOG).

## 3. NEXT STEP (start here)

**S0.2 — docker-compose: PostgreSQL + pgvector, Redis** (build bible §19, Phase 0)
- Work: `docker-compose.yml` (Postgres 16 with pgvector, Redis 7) + healthchecks;
  confirm `.env.example` URLs match (DATABASE_URL, REDIS_URL).
- **Exit criterion:** `psql` query works + `redis-cli ping` → PONG.
- **Blocker check (needs user decision first):** Docker is NOT installed.
  Option A: user installs Docker Desktop → standard path.
  Option B (fallback): local PostgreSQL + Redis on Windows (no Docker).
  Record the chosen option in §4 before starting S0.2.

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- **Docker: NOT installed** → S0.2 needs Docker Desktop (or fallback: local PostgreSQL + Redis)
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

- **S0.1 web half pending:** pnpm workspace + ESLint + Prettier + `pnpm lint` — blocked on
  Node LTS (also needed for S0.7 React shell).
- **S0.2 infra choice:** Docker Desktop vs local PostgreSQL/Redis on Windows — decide before S0.2.
- Exact llama-server URL + model(s) (small vs coder class) — needed at S0.6.
