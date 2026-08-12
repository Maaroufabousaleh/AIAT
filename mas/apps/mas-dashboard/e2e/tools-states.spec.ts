import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const TOOLS_FIXTURE = {
  tools: [
    {
      name: "tools-e2e-read",
      description: "Retained after a failed catalogue refresh",
      group: "General",
      circuit_breaker: { state: "CLOSED", failure_count: 0 },
    },
  ],
  health: { tools_registered: 1 },
};

test("tools catalogue retains the last known state when a refresh fails", async ({
  page,
}) => {
  await page.route("**/api/tools", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TOOLS_FIXTURE),
    });
  });

  await authenticate(page, "/tools");
  await expect(page.getByRole("heading", { name: "Tools" })).toBeVisible();
  await expect(
    page.getByRole("main", { name: "Tools catalogue" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Tool catalogue summary" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Tool search" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh tools" })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(page.getByRole("button", { name: /all groups/ })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(page.getByRole("searchbox", { name: "Search tools" })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();

  const toolsRegion = page.getByRole("region", { name: "General tools" });
  await expect(toolsRegion).toBeVisible();
  const toolsTable = page.getByRole("table", { name: "General tools" });
  await expect(toolsTable.locator("caption")).toHaveText(
    /registered in the General group/i,
  );
  for (const heading of [
    "Expand tool details",
    "Tool Name",
    "Description",
    "Circuit Breaker",
    "Failures",
  ]) {
    await expect(
      toolsTable.getByRole("columnheader", { name: heading }),
    ).toHaveAttribute("scope", "col");
  }
  await expect(
    page.getByRole("button", { name: "Expand tools-e2e-read details" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("button", { name: "Copy tools-e2e-read to clipboard" }),
  ).toHaveCSS("min-height", "44px");
  await page
    .getByRole("button", { name: "Expand tools-e2e-read details" })
    .click();
  await expect(
    page.getByRole("button", { name: "Collapse tools-e2e-read details" }),
  ).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#tool-row-tools-e2e-read-details")).toBeVisible();

  await page.unroute("**/api/tools");
  let failNextRead = true;
  await page.route("**/api/tools", async (route) => {
    if (failNextRead) {
      failNextRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "tools fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TOOLS_FIXTURE),
    });
  });

  await page.getByRole("button", { name: "Refresh tools" }).click();
  await expect(
    page.getByText("Showing last known tool catalogue"),
  ).toBeVisible();
  await expect(page.getByText(/latest tools refresh failed/i)).toBeVisible();
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known tool catalogue")).toHaveCount(
    0,
  );
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();
});

test("tools catalogue access denial on first load hides catalogue controls", async ({
  page,
}) => {
  await page.route("**/api/tools", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "tools access denied" }),
    });
  });

  await authenticate(page, "/tools");
  const tools = page.getByRole("main", { name: "Tools catalogue" });
  await expect(
    tools.getByRole("region", { name: "Tools access status" }),
  ).toBeVisible();
  await expect(
    tools.getByRole("heading", { name: "Tools access denied" }),
  ).toBeVisible();
  await expect(
    tools.getByText("No live tool catalogue is available", { exact: true }),
  ).toBeVisible();
  await expect(tools.getByRole("button", { name: "Refresh tools" })).toHaveCount(0);
  await expect(tools.getByRole("button", { name: /all groups/i })).toHaveCount(0);
  await expect(tools.getByRole("searchbox", { name: "Search tools" })).toHaveCount(0);
});

test("tools catalogue access denial after a successful read retains metadata without controls", async ({
  page,
}) => {
  let reads = 0;
  await page.route("**/api/tools", async (route) => {
    reads += 1;
    if (reads > 1) {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "tools access expired" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TOOLS_FIXTURE),
    });
  });

  await authenticate(page, "/tools");
  const tools = page.getByRole("main", { name: "Tools catalogue" });
  await expect(tools.getByText("tools-e2e-read", { exact: true })).toBeVisible();
  await tools.getByRole("button", { name: "Refresh tools" }).click();

  await expect(
    tools.getByRole("region", { name: "Tools access status" }),
  ).toBeVisible();
  await expect(tools.getByText("tools-e2e-read", { exact: true })).toBeVisible();
  await expect(tools.getByRole("button", { name: "Refresh tools" })).toHaveCount(0);
  await expect(tools.getByRole("button", { name: /all groups/i })).toHaveCount(0);
  await expect(tools.getByRole("searchbox", { name: "Search tools" })).toHaveCount(0);
  await expect(tools.getByRole("button", { name: /Expand|Collapse|Copy/ })).toHaveCount(0);
  await expect(tools.getByRole("table", { name: "General tools" })).toBeVisible();
});
