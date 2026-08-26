# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.1 not started (project created, no code yet)
- **In-progress note:** none

## 2. Just completed

- 2026-08-26 · Reviewed build bible v1.0 (PDF on Desktop) → produced **v1.1** at
  `docs/AI_QA_Copilot_Build_Bible_v1.1.md` (canonical; v1.0 PDF is historical):
  local-LLM strategy + hard context budgets (§31.1), mandatory async jobs 202+SSE (§31.2),
  auth baseline (§31.3), repo-connection model (§31.4), prompt registry (§31.6),
  numeric eval targets (§31.7), demo-app defect-injection spec (§23/§31.8),
  step plan S0.1–S4.3 (§19), agent-memory protocol (§32), data-model fixes (§10/§11).
- 2026-08-26 · Created `agent-memory/` (this file + SESSION_LOG.md) and `README.md`.

## 3. NEXT STEP (start here)

**S0.1 — Monorepo skeleton** (build bible §19, Phase 0)
- Work: `git init` (done as part of this bootstrap), uv workspace (`pyproject.toml` +
  `[tool.uv.workspace]`), `apps/api` + `apps/web` placeholders,
  `packages/{domain,ai,repository,execution,knowledge,integrations}` with minimal
  `py.typed`/`__init__`, ruff + mypy + pytest + ESLint + Prettier configs, pre-commit,
  `.env.example` (LLM_BASE_URL, LLM_MODEL, DATABASE_URL, REDIS_URL, APP_UNDER_TEST).
- **Exit criterion:** `uv run ruff check .` and `pnpm lint` pass on the empty scaffold;
  `git status` clean after commit `step S0.1: monorepo skeleton`.
- **Blocker check:** pnpm NOT installed → needs Node 20+ first (see §4). If Node is still
  absent when starting S0.1, ask user to install Node LTS, or scope S0.1 to the Python
  half only and mark web tooling as pending.

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- **Docker: NOT installed** → S0.2 needs Docker Desktop (or fallback: local PostgreSQL + Redis)
- **Node/npm/pnpm: NOT installed** → S0.7 (React shell) needs Node 20+
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

- Exact llama-server URL + model(s) (small vs coder class) — needed at S0.6
- Docker Desktop: will user install it? (affects S0.2 plan)
- Node LTS: will user install it? (affects S0.1 web half + S0.7)
