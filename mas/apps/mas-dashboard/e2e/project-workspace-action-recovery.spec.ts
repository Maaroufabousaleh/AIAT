import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "project-workspace-action-recovery-001";

test("failed project retry shows the service error and can be retried", async ({
  page,
}) => {
  let projectState = "FAILED";
  let retryCalls = 0;

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;

    if (pathname === `/api/projects/${PROJECT_ID}`) {
      await route.fulfill({
        json: {
          id: PROJECT_ID,
          name: "Project retry recovery",
          description: "A failed project retry can be recovered",
          state: projectState,
          created_at: "2026-10-08T00:00:00Z",
          updated_at: "2026-10-08T00:00:00Z",
        },
      });
    } else if (
      pathname === `/api/projects/${PROJECT_ID}/state-history` ||
      pathname === `/api/projects/${PROJECT_ID}/decisions` ||
      pathname === `/api/projects/${PROJECT_ID}/transition`
    ) {
      await route.fulfill({ json: [] });
    } else if (pathname === `/api/projects/${PROJECT_ID}/workspace`) {
      await route.fulfill({
        json: {
          next_actions: [],
          pending_approvals: [],
          recent_activity: [],
          worker_activity: [],
          artifacts: [],
          logs: [],
          cost_usage: { available: false, reason: "fixture has no usage data" },
          repository: null,
        },
      });
    } else if (pathname === `/api/projects/${PROJECT_ID}/repository`) {
      await route.fulfill({ json: { workspace: null } });
    } else if (
      pathname === `/api/projects/${PROJECT_ID}/retry` &&
      route.request().method() === "POST"
    ) {
      retryCalls += 1;
      if (retryCalls === 1) {
        await route.fulfill({
          status: 503,
          json: { detail: "retry temporarily unavailable" },
        });
      } else {
        projectState = "ACTIVE";
        await route.fulfill({ json: { status: "accepted" } });
      }
    } else {
      await route.fulfill({ json: [] });
    }
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await expect(
    page.getByRole("heading", { name: "Project retry recovery" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Workflow", exact: true }).click();
  await expect(
    page.getByRole("status", { name: "Project state: FAILED" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByTestId("project-action-error")).toContainText(
    "retry temporarily unavailable",
  );
  await expect(
    page.getByRole("status", { name: "Project state: FAILED" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Retry action", exact: true }).click();
  await expect(
    page.getByRole("status", { name: "Project state: ACTIVE" }),
  ).toBeVisible();
  await expect(page.getByTestId("project-action-error")).toHaveCount(0);
  expect(retryCalls).toBe(2);
});
