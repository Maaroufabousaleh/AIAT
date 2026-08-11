import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "project-detail-e2e-001";

const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  name: "Project detail recovery",
  description: "Recovered after an unavailable first load",
  state: "ACTIVE",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-02T00:00:00Z",
};

const WORKSPACE_FIXTURE = {
  next_actions: [],
  pending_approvals: [],
  recent_activity: [],
  worker_activity: [],
  artifacts: [],
  logs: [],
  cost_usage: { available: false, reason: "fixture has no usage data" },
  repository: null,
};

test("project detail exposes first-load unavailability and recovers on retry", async ({
  page,
}) => {
  let projectReads = 0;

  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    projectReads += 1;
    if (projectReads === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "project fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECT_FIXTURE),
    });
  });

  await page.route(
    `**/api/projects/${PROJECT_ID}/state-history`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    },
  );
  await page.route(`**/api/projects/${PROJECT_ID}/decisions`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    });
  });
  await page.route(
    `**/api/projects/${PROJECT_ID}/transition`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    },
  );
  await page.route(`**/api/projects/${PROJECT_ID}/workspace`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(WORKSPACE_FIXTURE),
    });
  });
  await page.route(
    `**/api/projects/${PROJECT_ID}/repository`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ workspace: null }),
      });
    },
  );

  await authenticate(page, `/projects/${PROJECT_ID}`);

  await expect(page.getByText("Project unavailable")).toBeVisible();
  await expect(page.getByText(/project fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("Project not found")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();

  await expect(
    page.getByRole("heading", { name: PROJECT_FIXTURE.name }),
  ).toBeVisible();
  await expect(page.getByText("Project unavailable")).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "Workspace", exact: true }),
  ).toBeVisible();
});
