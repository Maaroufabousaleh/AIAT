import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const FLOW_FIXTURE = [
  {
    id: "flows-e2e-001",
    name: "flows-e2e-retained",
    description: "Retained after a failed flow refresh",
    definition_json: { nodes: [], edges: [] },
    version: 1,
    created_by: "e2e",
    is_active: true,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-02T00:00:00Z",
  },
];

test("flows list retains the last known state when a refresh fails", async ({
  page,
}) => {
  let failNextRead = false;

  await page.route("**/api/flows**", async (route) => {
    if (failNextRead) {
      failNextRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "flows fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FLOW_FIXTURE),
    });
  });

  await authenticate(page, "/flows");
  await expect(
    page.getByRole("heading", { name: "Orchestration Flows" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "flows-e2e-retained", exact: true }),
  ).toBeVisible();

  const flowsTable = page.getByRole("table", { name: "Flows list" });
  await expect(flowsTable.locator("caption")).toHaveText(
    /Use the search and status filters/i,
  );
  for (const heading of ["Name", "Version", "Status", "Nodes", "Updated"]) {
    await expect(
      flowsTable.getByRole("columnheader", { name: heading }),
    ).toHaveAttribute("scope", "col");
  }

  await expect(page.getByRole("button", { name: "Refresh flows" })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(page.getByRole("link", { name: "New Flow" })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(
    page.getByRole("searchbox", {
      name: "Filter flows by name or description",
    }),
  ).toHaveCSS("min-height", "44px");
  await expect(page.getByRole("button", { name: /^All/ })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(
    page.getByRole("checkbox", { name: "Select all flows" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("checkbox", { name: "Select flows-e2e-retained" }),
  ).toHaveCSS("min-height", "44px");
  await expect(page.getByRole("link", { name: "Edit", exact: true })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(
    page.getByRole("button", { name: "Delete flow flows-e2e-retained v1" }),
  ).toHaveCSS("min-height", "44px");

  failNextRead = true;
  await page.getByRole("button", { name: "Refresh flows" }).click();
  await expect(page.getByText("Showing last known flows")).toBeVisible();
  await expect(page.getByText(/latest flows refresh failed/i)).toBeVisible();
  await expect(
    page.getByRole("link", { name: "flows-e2e-retained", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known flows")).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "flows-e2e-retained", exact: true }),
  ).toBeVisible();
});
