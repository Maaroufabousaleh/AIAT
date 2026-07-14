import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("renders the requested first-run state from the live orchestrator", async ({ page }) => {
  const expected = process.env.EXPECTED_FIRST_RUN;
  expect(["not_seeded", "seeded", "needs_migration_config"]).toContain(expected);

  await authenticate(page);
  await expect(page.getByRole("heading", { name: "System Overview" })).toBeVisible();

  const response = await page.request.get("/api/system/status");
  expect(response.ok()).toBeTruthy();
  expect((await response.json()).first_run).toBe(expected);

  if (expected === "seeded") {
    await expect(page.getByText("Default company not yet seeded")).toHaveCount(0);
    await expect(page.getByText("Configuration required")).toHaveCount(0);
  } else if (expected === "not_seeded") {
    await expect(page.getByText("Default company not yet seeded")).toBeVisible();
    await expect(page.getByRole("button", { name: /seed default aiat/i })).toBeVisible();
  } else {
    await expect(page.getByText("Configuration required")).toBeVisible();
  }
});
