# STATE — AI QA Copilot

> **Single source of truth for any new AI/human session. Read this file FIRST.** Keep ≤ ~150 lines.
> Protocol: build bible §32 · Step system: build bible §19

## 1. Current position

- **Phase:** 0 — Foundation
- **Step:** S0.1 ✓ · S0.2 ✓ · S0.3 ✓ · **S0.4 ✓ — next: S0.5** (SQLAlchemy + Alembic: §10 core
  tables + `jobs` + `requirement_test_cases` + `prompt_versions`)

## 2. Just completed

- 2026-08-26 · **S0.4 — domain package** (commit `f2fccea`): `qa_copilot_domain` now holds the
  §10 core model as pydantic v2 — `enums.py` (TestType, Priority, RiskLevel, JobType, JobStatus,
  FailureCategory (§16), ArtifactType; all `StrEnum`, snake_case wire values matching §12) +
  `entities.py` (Project, Requirement, TestCase, Failure, Artifact, Job) + `base.DomainModel`
  (`extra="forbid"`, `from_attributes`); `NonBlankStr` = `StringConstraints(min_length=1,
  strip_whitespace=True)`; `confidence`/`progress` bounded 0.0–1.0; ids `str | None` (server-assigned);
  `TestCase.requirement_refs` mirrors the §10 M:N join. `pydantic>=2.9` added to domain deps;
  `TestCase`/`TestType` carry `__test__ = False` (pytest). **31 tests green · ruff ✓ · mypy strict ✓.**
- 2026-08-26 · **S0.3 — FastAPI skeleton** (commit `cbd623d`): `create_app(settings)` factory +
  module `app`; `Settings` (pydantic-settings, `.env` + fallbacks); stdlib JSON structured logging
  with uvicorn routed through it; `GET /health` → **200 verified live**; `py.typed`. 14 tests green.
- 2026-08-26 · **S0.2 — compose infra up** (commit `4446f1a`): native PG16 kept on 5432 → compose db
  on **5433** (`.env`); redis :6379; both healthy; pgvector **0.8.6** available (extension at S0.5).
- 2026-08-26 · **S0.1 — Monorepo skeleton (python half)** (commit `ed6dcaf`): uv workspace
  (7 members), ruff + mypy strict + pytest, pre-commit, `.env.example`.
- 2026-08-26 · Bootstrap: build bible **v1.1** (canonical) + `agent-memory/` + `README.md`.

## 3. NEXT STEP (start here)

**S0.5 — SQLAlchemy + Alembic** (build bible §19): §10 core tables + `jobs` +
`requirement_test_cases` + `prompt_versions`
- **Exit criterion:** `alembic upgrade head` on the compose DB; seed script runs.
- Notes:
  1. DB is on **5433** (native PG16 stays on 5432 — user decision); `DATABASE_URL` in `.env` points there.
  2. Enable in a migration: `CREATE EXTENSION IF NOT EXISTS vector` (pgvector 0.8.6 in the image).
  3. Shape ORM columns from the S0.4 domain entities (`qa_copilot_domain`); enum wire values =
     stored strings (keep parity; no double-sourcing of vocabularies).
  4. Home for models/migrations: `packages/repository` (data access) + `infra/migrations` (bible §7) —
     confirm at S0.5 start.
  5. §10 also lists `knowledge_documents` + `embeddings` (vector col) — include so S0.5 is one-shot.

## 4. Environment facts (verified 2026-08-26)

- OS: Windows (PowerShell) · project: `c:\Users\manve\Workspace\ai-qa-copilot`
- Python **3.11.9 ✓** · uv **0.11.32 ✓** · git **2.55.0 ✓** · pypdf ✓
- Docker **running** (Desktop, per-user; CLI v29.7.2, Compose v5.4.0; on the **USER** PATH — refresh
  `$env:Path` in long-lived shells)
- **Infra up:** `qa-copilot-db` pgvector/pg16 **0.0.0.0:5433→5432** (qa/qa @ qa_copilot) ·
  `qa-copilot-redis` :6379 · named volumes
- Native PG16 service `postgresql-x64-16` running on 5432 (user decision; no pgvector)
- **Node/npm/pnpm: NOT installed** → S0.7 (React shell) needs Node 20+
- Toolchain in `.venv`: ruff 0.16.4 · mypy 2.3.1 · pytest 9.1.1 · pre-commit 4.6.2
- LLM: **local model via llama server** (user-run). Confirm exact `LLM_BASE_URL` + model
  name/size before S0.6 (needed to tune §31.1 budgets). Assume OpenAI-compatible.
- python-docx ✗ — Markdown is the doc source of truth

## 5. Key decisions (full list: build bible §29)

- Local LLM only, no cloud · hard context budgets · text-first failure analysis
- Async jobs mandatory (202 + SSE) — local inference is slow
- One step per session · verify exit criterion · commit `step S#.x` · update this file
- Markdown is canonical; do not re-derive decisions already in §29
- Keep native PG16 on 5432; compose db on 5433 (S0.2, user decision)
- S0.4 domain: pydantic v2 · `StrEnum` wire strings · `extra="forbid"` · ids `str | None`
  (server-assigned) · `Project.repository_id` (bible §10 says `repo_id` — clearer name kept)

## 6. Pointers (paths only — no code here)

- Build bible: `docs/AI_QA_Copilot_Build_Bible_v1.1.md`
- Session history: `agent-memory/SESSION_LOG.md`
- v1.0 original (PDF, historical): `c:\Users\manve\Desktop\AI_QA_Copilot_Build_Bible.pdf`
- Demo app (later, S0.10): separate repo `ai-qa-copilot-demo-app`
- Domain entities: `packages/domain/src/qa_copilot_domain/{base,enums,entities}.py`

## 7. Open questions / gotchas

- **PATH gotcha:** docker CLI lives on the USER PATH (per-user install) — terminals opened before
  install don't see it; refresh `$env:Path` from Machine+User (or open a new shell).
- **S0.1 web half pending:** pnpm workspace + ESLint + Prettier + `pnpm lint` — blocked on
  Node LTS (also needed for S0.7 React shell).
- Exact llama-server URL + model(s) (small vs coder class) — needed at S0.6.
- **pydantic v2:** `Field(..., strip_whitespace=True)` is a deprecated v1 kwarg (mypy strict
  rejects it) — use `Annotated[str, StringConstraints(...)]` (learned in S0.4).
- **ruff isort:** `qa_copilot_*` packages are NOT detected as first-party (src-layout workspace) —
  they sort in the third-party block, no blank line between `pydantic`/`pytest` and them.
- **mypy strict + pydantic:** wire-string/negative cases must go through `model_validate` — typed
  constructors are arg-checked (`status="completed"` → arg-type error).
