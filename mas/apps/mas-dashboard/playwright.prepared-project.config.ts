import { defineConfig } from "@playwright/test";

/** Runs one live-Compose test without the broad E2E cleanup hooks. */
export default defineConfig({
  testDir: "./e2e",
  testMatch: ["prepared-project-flow.integration.spec.ts"],
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:18040",
    headless: true,
  },
});
