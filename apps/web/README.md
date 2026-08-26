# apps/web — React UI (placeholder)

Placeholder for the React + TypeScript frontend (build bible §6, §29).

**Status: pending** — Node 20+ / pnpm are not installed in this environment
(see `agent-memory/STATE.md` §4). The web half of step S0.1 (pnpm workspace,
ESLint, Prettier, `pnpm lint`) is deferred until Node LTS is available; the
React shell itself lands at S0.7.

Planned layout (when Node is available):

- `package.json` (pnpm workspace member) + `pnpm-workspace.yaml`
- `vite.config.ts`, `tsconfig.json`
- `eslint.config.js` + `.prettierrc.json`
- React app under `src/`
