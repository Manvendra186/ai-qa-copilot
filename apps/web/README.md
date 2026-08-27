# apps/web — React shell

React 18 + Vite + TypeScript (strict) + Tailwind CSS v4, managed by pnpm
(build bible §19 S0.7, §29, §31.9).

## Scripts (run from this dir or the repo root)

| Script              | What it does                                                                           |
| ------------------- | -------------------------------------------------------------------------------------- |
| `pnpm dev`          | Vite dev server on :5173 (mock SSE at `/mock/events`; `/api` proxied to FastAPI :8000) |
| `pnpm build`        | Type-check (`tsconfig.json` + `tsconfig.node.json`) + production build to `dist/`      |
| `pnpm preview`      | Serve the production build                                                             |
| `pnpm lint`         | ESLint flat config (TS recommended + react-hooks + react-refresh, Prettier-clean)      |
| `pnpm format`       | Prettier write                                                                         |
| `pnpm format:check` | Prettier check                                                                         |

## Layout

- `vite.config.ts` — plugins (react, tailwind) + dev-only **mock SSE** plugin
  (streams `job.started` → per stage `stage.started`/`progress`/`stage.completed`
  → `job.completed`; the same shape S0.9 will serve from `GET /events`)
- `src/lib/pipeline.ts` — the six-stage pipeline contract (shared by shell + mock)
- `src/hooks/useJobEvents.ts` — `EventSource` client; reduces SSE into stage state + event log
- `src/components/` — `Header`, `PipelineView` (per-stage progress bars), `EventLog`

S0.7 is a shell: no real API calls yet (S0.8 auth and the S0.9 jobs API plug in
through the `/api` dev proxy).
