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

test("tools catalogue ignores an older refresh response after a newer refresh", async ({
  page,
}) => {
  let refreshRequestCount = 0;
  let readyForRefresh = false;
  let releaseFirstResponse: (() => void) | undefined;
  let firstResponseFulfilled: (() => void) | undefined;
  const firstResponse = new Promise<void>((resolve) => {
    releaseFirstResponse = resolve;
  });
  const firstFulfilled = new Promise<void>((resolve) => {
    firstResponseFulfilled = resolve;
  });

  await page.route("**/api/tools", async (route) => {
    if (!readyForRefresh) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(TOOLS_FIXTURE),
      });
      return;
    }
    refreshRequestCount += 1;
    if (refreshRequestCount === 1) {
      await firstResponse;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...TOOLS_FIXTURE,
          tools: [{ ...TOOLS_FIXTURE.tools[0], name: "tools-stale-refresh" }],
        }),
      });
      firstResponseFulfilled?.();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...TOOLS_FIXTURE,
        tools: [{ ...TOOLS_FIXTURE.tools[0], name: "tools-fresh-manual" }],
      }),
    });
  });

  await authenticate(page, "/tools");
  await expect(page.getByText("tools-e2e-read", { exact: true })).toBeVisible();

  readyForRefresh = true;
  await page.getByRole("button", { name: "Refresh tools" }).click();
  await expect.poll(() => refreshRequestCount).toBe(1);
  await page.getByRole("button", { name: "Refresh tools" }).click();
  await expect.poll(() => refreshRequestCount).toBe(2);
  await expect(
    page.getByText("tools-fresh-manual", { exact: true }),
  ).toBeVisible();

  releaseFirstResponse?.();
  await firstFulfilled;
  await page.evaluate(
    () =>
      new Promise<void>((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
      ),
  );
  await expect(
    page.getByText("tools-stale-refresh", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("tools-fresh-manual", { exact: true }),
  ).toBeVisible();
});

test("tools catalogue preserves a deliberate collapse-all choice across refresh", async ({
  page,
}) => {
  let reads = 0;
  let refreshRequested = false;
  await page.route("**/api/tools", async (route) => {
    reads += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...TOOLS_FIXTURE,
        tools: [
          ...TOOLS_FIXTURE.tools,
          {
            name: "web-e2e-search",
            description: "Search the approved local fixture index",
            group: "Web / HTTP",
          },
        ],
        health: { tools_registered: refreshRequested ? 3 : 2 },
      }),
    });
  });

  await authenticate(page, "/tools");
  const generalGroup = page.locator("#group-header-General");
  await expect(generalGroup).toHaveAttribute("aria-expanded", "true");
  const webGroup = page.getByRole("button", { name: /Web \/ HTTP/ });

  await page.getByRole("button", { name: "Collapse all groups" }).click();
  await expect(generalGroup).toHaveAttribute("aria-expanded", "false");
  await expect(webGroup).toHaveAttribute("aria-expanded", "false");

  const readsBeforeRefresh = reads;
  refreshRequested = true;
  await page.getByRole("button", { name: "Refresh tools" }).click();
  await expect.poll(() => reads).toBe(readsBeforeRefresh + 1);
  await expect(page.getByText(/3 registered tools/)).toBeVisible();
  await expect(generalGroup).toHaveAttribute("aria-expanded", "false");
});

test("tools catalogue recognizes newly added groups when showing expand or collapse all", async ({
  page,
}) => {
  let showReplacementGroups = false;
  const initialGroups = {
    tools: [
      { name: "general-read", description: "Read general metadata", group: "General" },
      { name: "file-read", description: "Read a file", group: "File System" },
    ],
    health: { tools_registered: 2 },
  };
  const replacementGroups = {
    tools: [
      { name: "general-read", description: "Read general metadata", group: "General" },
      { name: "code-check", description: "Run a local check", group: "Code Execution" },
    ],
    health: { tools_registered: 2 },
  };

  await page.route("**/api/tools", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(showReplacementGroups ? replacementGroups : initialGroups),
    });
  });

  await authenticate(page, "/tools");
  await expect(page.locator("#group-header-General")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(page.locator("#group-header-File-System")).toHaveAttribute(
    "aria-expanded",
    "true",
  );

  showReplacementGroups = true;
  await page.getByRole("button", { name: "Refresh tools" }).click();
  const expandAll = page.getByRole("button", { name: "Expand all groups" });
  await expect(expandAll).toBeVisible();
  await expandAll.click();
  await expect(page.locator("#group-header-Code-Execution")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
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
