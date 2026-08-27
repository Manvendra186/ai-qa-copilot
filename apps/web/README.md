# apps/web — React shell

React 18 + Vite + TypeScript (strict) + Tailwind CSS v4, managed by pnpm
(build bible §19 S0.7, §29, §31.9).

## Scripts (run from this dir or the repo root)

| Script              | What it does                                                                           |
| ------------------- | -------------------------------------------------------------------------------------- |
| `pnpm dev`          | Vite dev server on :5173 (`/api` proxied to FastAPI :8000)                          |
| `pnpm build`        | Type-check (`tsconfig.json` + `tsconfig.node.json`) + production build to `dist/`      |
| `pnpm preview`      | Serve the production build                                                             |
| `pnpm lint`         | ESLint flat config (TS recommended + react-hooks + react-refresh, Prettier-clean)      |
| `pnpm format`       | Prettier write                                                                         |
| `pnpm format:check` | Prettier check                                                                         |

## Layout

- `vite.config.ts` — plugins (react, tailwind) + dev proxy for `/api` → FastAPI :8000
- `src/lib/api.ts` — fetch API client: `login`, `me`, `createTestCaseJob`,
  `getRequirement`, and `streamJobEvents` (fetch + streaming reader SSE client
  with Bearer auth — `EventSource` cannot set headers)
- `src/lib/pipeline.ts` — the six-stage pipeline contract (shared by shell + hook)
- `src/hooks/useAuth.ts` — session state (token in localStorage, `/me` bootstrap,
  project membership, `login`/`logout`)
- `src/hooks/useJobEvents.ts` — `start(jobId)` streams `GET /api/v1/events?job_id=…`
  and reduces it into stage state, an event log, and the terminal `output_ref`
- `src/components/` — `Header` (user/project/stream status), `LoginForm`,
  `RequirementForm`, `PipelineView` (per-stage progress bars), `TestCaseList`,
  `EventLog`

The shell is wired to the real backend (S1.3): login → requirement submit
(202 + `job_id`) → live SSE pipeline → on `job.completed` the `output_ref`
(persisted requirement id) is fetched and the stored test cases are rendered.
