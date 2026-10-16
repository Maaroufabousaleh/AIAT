import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "project-flow-actions-001";
const FLOW_ID = "flow-retry-001";
const FLOW_INSTANCE_ID = "flow-instance-retry-001";

test("project flow retry shows a recoverable error then clears it on success", async ({
  page,
}) => {
  let currentStatus = "FAILED";
  let retryCalls = 0;
  const flowInstance = () => ({
    id: FLOW_INSTANCE_ID,
    project_id: PROJECT_ID,
    flow_id: FLOW_ID,
    flow_version: 1,
    status: currentStatus,
    active_node_ids: [],
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    if (pathname === `/api/projects/${PROJECT_ID}`) {
      await route.fulfill({
        json: {
          id: PROJECT_ID,
          name: "Flow retry recovery",
          description: "A failed flow can be retried from the project view",
          state: "ACTIVE",
          created_at: "2026-10-07T00:00:00Z",
          updated_at: "2026-10-07T00:00:00Z",
        },
      });
    } else if (
      pathname === `/api/projects/${PROJECT_ID}/state-history` ||
      pathname === `/api/projects/${PROJECT_ID}/decisions` ||
      pathname === `/api/projects/${PROJECT_ID}/transition` ||
      pathname === `/api/flows/instances/${FLOW_INSTANCE_ID}/executions`
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
    } else if (pathname === `/api/projects/${PROJECT_ID}/flow-instance`) {
      await route.fulfill({ json: flowInstance() });
    } else if (pathname === `/api/flows/${FLOW_ID}`) {
      await route.fulfill({
        json: {
          id: FLOW_ID,
          name: "Retry fixture",
          definition_json: { nodes: [], edges: [] },
        },
      });
    } else if (
      pathname === `/api/flows/instances/${FLOW_INSTANCE_ID}/retry` &&
      route.request().method() === "POST"
    ) {
      retryCalls += 1;
      if (retryCalls === 1) {
        await route.fulfill({
          status: 503,
          json: { detail: "retry temporarily unavailable" },
        });
      } else {
        currentStatus = "RUNNING";
        await route.fulfill({ json: { status: "RUNNING" } });
      }
    } else {
      await route.fulfill({ json: [] });
    }
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await expect(page.getByRole("heading", { name: "Flow retry recovery" })).toBeVisible();
  await page.getByRole("tab", { name: "Flow", exact: true }).click();
  await expect(
    page.getByTestId("project-tab-flow").getByText("FAILED", { exact: true }),
  ).toBeVisible();

  await page.getByTestId("flow-retry-button").click();
  await expect(page.getByText("retry temporarily unavailable")).toBeVisible();

  await page.getByTestId("flow-retry-button").click();
  await expect(
    page.getByTestId("project-tab-flow").getByText("RUNNING", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("retry temporarily unavailable")).toHaveCount(0);
  expect(retryCalls).toBe(2);
});
