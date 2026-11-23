import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const VIZ_ENDPOINTS = [
  "**/api/system/hierarchy",
  "**/api/system/permissions",
  "**/api/system/orchestration",
  "**/api/system/org-graph",
];

test("system viz loads views and switches tabs", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/system-viz");
  const tabs = page.getByRole("tablist", { name: "Visualization views" });
  await expect(tabs).toBeVisible();
  await expect(page.getByRole("tab", { name: "Team Hierarchy" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Permissions" }).click();
  await expect(page.getByRole("tab", { name: "Permissions" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Orchestration" }).click();
  await expect(page.getByRole("tab", { name: "Orchestration" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "Refresh visualization data" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("system viz total failure shows retry and recovers", async ({ page }) => {
  for (const pattern of VIZ_ENDPOINTS) {
    await page.route(pattern, async (route) => {
      await route.abort("connectionrefused");
    });
  }
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/system-viz");
  await expect(page.getByText("Failed to load system hierarchy")).toBeVisible();
  await expect(page.getByText("Visualization unavailable")).toBeVisible();
  await page.unrouteAll({ behavior: "wait" });
  await page.getByRole("button", { name: "Retry" }).first().click();
  await expect(page.getByRole("tablist", { name: "Visualization views" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("system viz partial failure keeps content with warning", async ({ page }) => {
  await page.route("**/api/system/permissions", async (route) => {
    await route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/system-viz");
  await expect(page.getByText("Some visualization data is stale or unavailable")).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Visualization views" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("system viz hierarchy denial shows access status without graph", async ({ page }) => {
  await page.route("**/api/system/hierarchy", async (route) => {
    await route.fulfill({ status: 403, contentType: "application/json", body: "{}" });
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/system-viz");
  await expect(page.getByText("Visualization access denied")).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Visualization views" })).toHaveCount(0);
  expect(errors).toEqual([]);
});
