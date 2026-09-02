import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "project-workspace-stale-001";

const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  name: "Project workspace recovery",
  description: "Retains workspace evidence through a failed refresh",
  state: "ACTIVE",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-02T00:00:00Z",
};

const INITIAL_WORKSPACE = {
  next_actions: [
    { kind: "review", label: "Review initial workspace", severity: "medium" },
  ],
  pending_approvals: [],
  recent_activity: [
    {
      event_type: "workspace_loaded",
      occurred_at: "2026-08-02T00:00:00Z",
      summary: "Initial workspace activity",
      actor: "fixture",
    },
  ],
  worker_activity: [],
  artifacts: [{ id: 1, path: "initial-report.md", agent_id: "fixture-agent" }],
  logs: [],
  cost_usage: {
    available: true,
    total_cost_usd: 1.25,
    tool_calls: 2,
    llm_calls: 1,
    failed_calls: 0,
    total_tokens: 120,
  },
  repository: null,
};

const RECOVERED_WORKSPACE = {
  ...INITIAL_WORKSPACE,
  next_actions: [
    {
      kind: "ship",
      label: "Ship recovered workspace",
      severity: "high",
    },
  ],
  recent_activity: [
    {
      event_type: "workspace_recovered",
      occurred_at: "2026-08-03T00:00:00Z",
      summary: "Recovered workspace activity",
      actor: "fixture",
    },
  ],
  artifacts: [
    { id: 2, path: "recovered-report.md", agent_id: "fixture-agent" },
  ],
};

test("project workspace retains data through a failed refresh and recovers", async ({
  page,
}) => {
  let workspaceReads = 0;

  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
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
    workspaceReads += 1;
    if (workspaceReads === 2) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "workspace fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        workspaceReads >= 3 ? RECOVERED_WORKSPACE : INITIAL_WORKSPACE,
      ),
    });
  });
  await page.route(
    `**/api/projects/${PROJECT_ID}/repository`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ configured: false, workspace: null }),
      });
    },
  );

  await authenticate(page, `/projects/${PROJECT_ID}`);

  await expect(
    page.getByRole("heading", { name: PROJECT_FIXTURE.name }),
  ).toBeVisible();
  await expect(page.getByText("Review initial workspace")).toBeVisible();
  await expect(page.getByText("Initial workspace activity")).toBeVisible();

  await page.getByRole("button", { name: "Refresh project data" }).click();

  await expect(page.getByTestId("project-workspace-stale")).toBeVisible();
  await expect(page.getByText(/workspace fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("Review initial workspace")).toBeVisible();
  await expect(page.getByText("Initial workspace activity")).toBeVisible();

  await page
    .getByTestId("project-workspace-stale")
    .getByRole("button", { name: "Retry" })
    .click();

  await expect(page.getByTestId("project-workspace-stale")).toHaveCount(0);
  await expect(page.getByText("Ship recovered workspace")).toBeVisible();
  await expect(page.getByText("Recovered workspace activity")).toBeVisible();
  const activityTab = page.getByRole("tab", { name: "Activity", exact: true });
  const resourcesTab = page.getByRole("tab", {
    name: "Resources",
    exact: true,
  });
  const costTab = page.getByRole("tab", { name: "Cost", exact: true });
  await expect(activityTab).toHaveAttribute(
    "aria-controls",
    "workspace-panel-activity",
  );
  await expect(resourcesTab).toHaveAttribute(
    "aria-controls",
    "workspace-panel-resources",
  );
  await expect(costTab).toHaveAttribute(
    "aria-controls",
    "workspace-panel-cost",
  );

  await resourcesTab.click();
  await expect(page.getByText("recovered-report.md")).toBeVisible();

  await resourcesTab.focus();
  await resourcesTab.press("ArrowRight");
  await expect(costTab).toBeFocused();
  await expect(costTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#workspace-panel-cost")).toBeVisible();
  await expect(page.locator("#workspace-panel-cost")).toHaveAttribute(
    "aria-labelledby",
    "workspace-tab-cost",
  );

  await costTab.press("Home");
  await expect(activityTab).toBeFocused();
  await expect(activityTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#workspace-panel-activity")).toBeVisible();

  await activityTab.press("ArrowLeft");
  await expect(costTab).toBeFocused();
  await expect(costTab).toHaveAttribute("aria-selected", "true");
});
