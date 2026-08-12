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
  await expect(page.getByRole("heading", { name: "Projects", exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: "projects-e2e-retained", exact: true }),
  ).toBeVisible();

  const projectsTable = page.getByRole("table", { name: "Projects list" });
  await expect(projectsTable.locator("caption")).toHaveText(
    /Use the filters and sort controls/i,
  );
  for (const heading of ["Name", "State", "Created", "Updated"]) {
    await expect(
      projectsTable.getByRole("columnheader", { name: heading }),
    ).toHaveAttribute("scope", "col");
  }

  const descriptionToggle = page.locator(
    'button[aria-controls="project-description-projects-e2e-001"]',
  );
  await expect(descriptionToggle).toHaveAttribute(
    "aria-controls",
    "project-description-projects-e2e-001",
  );
  await expect(descriptionToggle).toHaveCSS("min-height", "44px");
  await descriptionToggle.press("Enter");
  await expect(descriptionToggle).toHaveAttribute("aria-expanded", "true");
  await expect(
    page.locator("#project-description-projects-e2e-001"),
  ).toBeVisible();

  await expect(
    page.getByRole("checkbox", { name: "Select projects-e2e-retained" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("checkbox", { name: "Select all projects" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("link", { name: "Open projects-e2e-retained" }),
  ).toHaveCSS("min-height", "44px");
  await expect(page.getByRole("button", { name: /Non archived/ })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(
    page.getByRole("button", { name: "Sort by Updated (descending)" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("button", { name: "Archive project projects-e2e-retained" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("button", { name: "Delete project projects-e2e-retained" }),
  ).toHaveCSS("min-height", "44px");

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
  await expect(
    page.getByRole("link", { name: "projects-e2e-retained", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known project list")).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("link", { name: "projects-e2e-retained", exact: true }),
  ).toBeVisible();
});

test("projects list fails closed when the initial project read is denied", async ({
  page,
}) => {
  await page.route("**/api/projects**", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "projects access denied" }),
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
  await expect(page.getByRole("heading", { name: "Projects", exact: true })).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Projects access status" }),
  ).toContainText("No live project definitions are available");
  await expect(
    page.getByText("No live project definitions are available", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh projects" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New Project" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Non archived/ })).toHaveCount(0);
});

test("projects list preserves retained rows but hides controls after read denial", async ({
  page,
}) => {
  let denyNextProjectRead = false;
  await page.route("**/api/projects**", async (route) => {
    if (denyNextProjectRead && route.request().method() === "GET") {
      denyNextProjectRead = false;
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "projects authorization expired" }),
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
  await expect(page.getByRole("link", { name: "projects-e2e-retained", exact: true })).toBeVisible();

  denyNextProjectRead = true;
  await page.getByRole("button", { name: "Refresh projects" }).click();
  await expect(
    page.getByRole("region", { name: "Projects access status" }),
  ).toContainText("Previously loaded project and active-flow definitions remain visible");
  await expect(page.getByText("projects-e2e-retained", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "projects-e2e-retained", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh projects" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New Project" })).toHaveCount(0);
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Archive project/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Delete project/ })).toHaveCount(0);
});

test("projects list enters the same denied state when deletion loses authorization", async ({
  page,
}) => {
  await page.route("**/api/projects**", async (route) => {
    if (route.request().method() === "DELETE") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ error: "project deletion denied" }),
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
  await expect(page.getByRole("button", { name: "Delete project projects-e2e-retained" })).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete project projects-e2e-retained" }).click();

  await expect(
    page.getByRole("region", { name: "Projects access status" }),
  ).toContainText("Previously loaded project and active-flow definitions remain visible");
  await expect(page.getByText("projects-e2e-retained", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "New Project" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh projects" })).toHaveCount(0);
});
