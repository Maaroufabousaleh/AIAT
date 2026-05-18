import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:3000",
    headless: true,
  },
});
