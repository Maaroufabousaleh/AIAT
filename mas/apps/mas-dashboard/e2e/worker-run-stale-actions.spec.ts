import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

const RUN_ID = "70000000-0000-4000-8000-000000000001";

async function mockRun(page: Page, initialState: "RUNNING" | "PAUSED" | "FAILED") {
  let failEvidenceReads = false;
  const actionRequests: string[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === `/api/governance/runs/${RUN_ID}` && request.method() === "GET") {
      await route.fulfill({
        json: {
          id: RUN_ID,
          worker_id: "stale-state-worker",
          task_type: "code.review",
          state: initialState,
          adapter_id: "adapter-fixture",
          project_id: "70000000-0000-4000-8000-000000000002",
        },
      });
      return;
    }

    if (path.startsWith(`/api/governance/runs/${RUN_ID}/`)) {
      if (["POST", "PUT", "PATCH", "DELETE"].includes(request.method())) {
        actionRequests.push(request.method() + " " + path);
      }
      if (request.method() === "GET" && path.endsWith("/events") && failEvidenceReads) {
        await route.fulfill({ status: 503, json: { detail: "Event storage is temporarily unavailable." } });
        return;
      }
      if (request.method() === "GET") {
        await route.fulfill({ json: [] });
        return;
      }
    }

    await route.fulfill({ json: [] });
  });

  await authenticate(page, `/workers/runs/${RUN_ID}`);
  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText(initialState);

  return {
    runPage,
    failNextRefresh() {
      failEvidenceReads = true;
    },
    actionRequests,
  };
}

test("stale running run data cannot be used to request execution actions", async ({ page }) => {
  const fixture = await mockRun(page, "RUNNING");
  fixture.failNextRefresh();

  await fixture.runPage.getByRole("button", { name: "Refresh" }).click();
  await expect(fixture.runPage.getByRole("status").filter({ hasText: "Showing last known Worker Run data" }))
    .toContainText("Event storage is temporarily unavailable");
  await expect(fixture.runPage.getByRole("status").filter({ hasText: "Operator actions are disabled" }))
    .toContainText("until this Worker Run refreshes successfully");

  await expect(fixture.runPage.getByRole("button", { name: "Cancel Worker Run" })).toBeDisabled();
  await expect(fixture.runPage.getByRole("button", { name: "Pause Worker Run" })).toBeDisabled();
  expect(fixture.actionRequests).toEqual([]);
});

test("stale failed run data cannot be used to create a retry", async ({ page }) => {
  const fixture = await mockRun(page, "FAILED");
  fixture.failNextRefresh();

  await fixture.runPage.getByRole("button", { name: "Refresh" }).click();
  await expect(fixture.runPage.getByRole("status").filter({ hasText: "Showing last known Worker Run data" }))
    .toContainText("Event storage is temporarily unavailable");
  await expect(fixture.runPage.getByRole("status").filter({ hasText: "Operator actions are disabled" }))
    .toContainText("until this Worker Run refreshes successfully");

  await expect(fixture.runPage.getByRole("button", { name: "Retry Worker Run" })).toBeDisabled();
  expect(fixture.actionRequests).toEqual([]);
});

test("stale paused run data cannot be used to resume execution", async ({ page }) => {
  const fixture = await mockRun(page, "PAUSED");
  fixture.failNextRefresh();

  await fixture.runPage.getByRole("button", { name: "Refresh" }).click();
  await expect(fixture.runPage.getByRole("status").filter({ hasText: "Showing last known Worker Run data" }))
    .toContainText("Event storage is temporarily unavailable");

  await expect(fixture.runPage.getByRole("combobox", { name: "Resume checkpoint (optional)" })).toBeDisabled();
  await expect(fixture.runPage.getByRole("button", { name: "Resume Worker Run" })).toBeDisabled();
  expect(fixture.actionRequests).toEqual([]);
});
