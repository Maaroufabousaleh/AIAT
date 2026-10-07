import { defineConfig } from "@playwright/test";

// This focused test is hermetic: it does not run the suite's API cleanup hooks,
// and it intercepts every dashboard API call before any worker/runtime action.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "project-issue-worker-dispatch.spec.ts",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:18040",
    headless: true,
  },
});
