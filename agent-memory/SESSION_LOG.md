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
