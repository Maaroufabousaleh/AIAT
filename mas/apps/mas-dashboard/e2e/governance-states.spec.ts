import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

test("governance retains the last known state when a refresh fails", async ({
  page,
}) => {
  let catalogueRequestCount = 0;

  await page.route(
    (url) => url.pathname === "/api/governance/model-profiles",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            profile_id: "governance-e2e-profile",
            purpose: "E2E governance profile",
            status: "APPROVED",
            versions: [
              {
                version: "v1",
                provider_id: "e2e-provider",
                exact_model_id: "e2e-model",
                status: "APPROVED",
              },
            ],
          },
        ]),
      });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/governance/model-profiles/catalogue",
    async (route) => {
      catalogueRequestCount += 1;
      if (catalogueRequestCount === 2) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "catalogue fixture unavailable" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "e2e-governance-v1",
          registry_model_count: 1,
          profile_count: 1,
          profile_version_count: 1,
          covered_profile_version_count: 1,
          profile_pending_model_count: 0,
          entries: [
            {
              model_id: "e2e-model",
              provider_id: "e2e-provider",
              profile_state: "approved_profile_present",
            },
          ],
        }),
      });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/governance/runs",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "governance-e2e-run",
            worker_id: "governance-e2e-worker",
            task_type: "governance.check",
            state: "SUCCEEDED",
            model_resolution_snapshot_id: "snapshot-e2e",
          },
        ]),
      });
    },
  );

  await page.route(
    (url) => url.pathname === "/api/governance/stewards",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "governance-e2e-steward",
            worker_id: "governance-e2e-worker",
            status: "READY",
            monitoring_cadence: "daily",
            candidate_count: 0,
          },
        ]),
      });
    },
  );

  await authenticate(page, "/governance");
  await expect(page.getByRole("heading", { name: "Governance" })).toBeVisible();
  const governance = page.getByRole("main", { name: "Governance" });
  await expect(governance).toBeVisible();
  for (const region of [
    governance.getByRole("region", { name: "Governance read surfaces" }),
    governance.getByRole("region", { name: "Executive actions" }),
    governance.getByRole("region", { name: "Model Profiles" }),
    governance.getByRole("region", { name: "Recent WorkerRuns" }),
    governance.getByRole("region", { name: "External Worker Stewards" }),
    governance.getByRole("region", { name: "Runtime Model Catalogue" }),
  ]) {
    await expect(region).toBeVisible();
  }
  for (const control of [
    governance.getByRole("button", { name: "Refresh governance" }),
    governance.getByLabel("Project ID"),
    governance.getByLabel("Requested profile ID"),
    governance.getByLabel("Reason"),
    governance.getByLabel("Dispatch JSON"),
    governance.getByLabel("Action", { exact: true }),
    governance.getByLabel("Payload JSON"),
    governance.getByRole("checkbox"),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }
  const executiveActions = governance.getByRole("region", { name: "Executive actions" });
  for (const action of [
    executiveActions.getByRole("button", { name: "Request override" }),
    executiveActions.getByRole("button", { name: "Dispatch governed run" }),
    executiveActions.getByRole("button", { name: "Request privileged action" }),
  ]) {
    await expect(action).toHaveCSS("min-height", "44px");
  }
  const workerRunsTable = governance.getByRole("table", { name: "Recent governed worker runs" });
  await expect(workerRunsTable).toBeVisible();
  for (const header of await workerRunsTable.getByRole("columnheader").all()) {
    await expect(header).toHaveAttribute("scope", "col");
  }
  await expect(page.getByText("governance-e2e-profile")).toBeVisible();
  await expect(page.getByText("e2e-governance-v1")).toBeVisible();
  await expect(page.getByText("governance-e2e-worker").first()).toBeVisible();

  await governance.getByRole("button", { name: "Refresh governance" }).click();
  await expect(page.getByText("Showing last known governance state")).toBeVisible();
  await expect(page.getByText(/latest refresh failed/i)).toBeVisible();
  await expect(page.getByText("governance-e2e-profile")).toBeVisible();
  await expect(page.getByText("e2e-governance-v1")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known governance state")).toHaveCount(0);
  await expect(page.getByText("governance-e2e-profile")).toBeVisible();
});
