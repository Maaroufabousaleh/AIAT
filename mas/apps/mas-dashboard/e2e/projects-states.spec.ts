import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_FIXTURE = [
  {
    id: "projects-e2e-001",
    name: "projects-e2e-retained",
    description: "Retained after a failed list refresh",
    state: "ACTIVE",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-02T00:00:00Z",
  },
];

const FLOW_FIXTURE = [
  {
    id: "projects-e2e-flow",
    name: "Projects E2E flow",
    version: 1,
  },
];

test("projects list retains the last known state when a refresh fails", async ({
  page,
}) => {
  let failNextProjectRead = false;

  await page.route("**/api/projects**", async (route) => {
    if (failNextProjectRead) {
      failNextProjectRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "projects fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECT_FIXTURE),
    });
  });

  await page.route("**/api/flows**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FLOW_FIXTURE),
    });
  });

  await authenticate(page, "/projects");
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByRole("link", { name: "projects-e2e-retained", exact: true })).toBeVisible();

  await page.unroute("**/api/projects**");
  await page.route("**/api/projects**", async (route) => {
    if (failNextProjectRead) {
      failNextProjectRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "projects fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECT_FIXTURE),
    });
  });

  failNextProjectRead = true;
  await page.getByRole("button", { name: "Refresh projects" }).click();
  await expect(page.getByText("Showing last known project list")).toBeVisible();
  await expect(page.getByText(/latest project refresh failed/i)).toBeVisible();
  await expect(page.getByRole("link", { name: "projects-e2e-retained", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known project list")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "projects-e2e-retained", exact: true })).toBeVisible();
});
