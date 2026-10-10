import { defineConfig } from "@playwright/test";

/** One live, safe CEO prepare-only flow without suite-wide cleanup hooks. */
export default defineConfig({
  testDir: "./e2e",
  testMatch: "ceo-prepare-project-handoff.integration.spec.ts",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4000",
    headless: true,
  },
});
