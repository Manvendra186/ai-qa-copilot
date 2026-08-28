// ESLint flat config for the S2.3 generated-test gate (build bible §19 S2.3 / §21).
//
// Lints AI-generated Playwright specs with the SAME ruleset the web app
// itself uses (apps/web/eslint.config.js → typescript-eslint "recommended"),
// so the gate holds generated code to the repository's own bar.
//
// Generated specs only import "@playwright/test" (stubbed under
// tests/unit/support/playwright-test for tsc), so browser/node globals are
// the only environment they need.
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: ["node_modules/**"],
  },
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      globals: {
        console: "readonly",
        process: "readonly",
        Buffer: "readonly",
        URL: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
      },
      ecmaVersion: 2022,
      sourceType: "module",
    },
  },
);