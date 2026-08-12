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

  await expect(
    page.getByRole("main", { name: "Project detail" }),
  ).toBeVisible();
  await expect(page.getByText("Project unavailable")).toBeVisible();
  await expect(page.getByText(/project fixture unavailable/i)).toBeVisible();
  await expect(page.getByText("Project not found")).toHaveCount(0);
  const firstLoadRetry = page.getByRole("button", { name: "Retry" });
  await expect(firstLoadRetry).toBeVisible();
  await expect(firstLoadRetry).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("link", { name: "Back to projects" }),
  ).toHaveCSS("min-height", "44px");

  await page.getByRole("button", { name: "Retry" }).click();

  await expect(
    page.getByRole("heading", { name: PROJECT_FIXTURE.name }),
  ).toBeVisible();
  await expect(
    page.getByRole("main", { name: "Project detail" }),
  ).toBeVisible();
  await expect(
    page.getByRole("status", { name: "Project state: ACTIVE" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Refresh project data" }),
  ).toHaveCSS("min-height", "44px");
  const projectViews = page.getByRole("tablist", { name: "Project views" });
  await expect(projectViews).toHaveAttribute("aria-orientation", "horizontal");
  const projectTabs = ["Workspace", "Workflow", "Flow", "Context", "Evidence"];
  for (const tabName of projectTabs) {
    const tab = page.getByRole("tab", { name: tabName, exact: true });
    await expect(tab).toHaveCSS("min-height", "44px");
    await expect(tab).toHaveAttribute(
      "aria-controls",
      `project-panel-${tabName.toLowerCase()}`,
    );
  }
  await expect(page.locator("#project-panel-workspace")).toHaveAttribute(
    "role",
    "tabpanel",
  );
  const workspaceSections = page.getByRole("tablist", {
    name: "Workspace sections",
  });
  await expect(workspaceSections).toBeVisible();
  for (const sectionName of ["Activity", "Resources", "Cost"]) {
    await expect(
      workspaceSections.getByRole("tab", { name: sectionName, exact: true }),
    ).toHaveCSS("min-height", "44px");
  }
  await expect(page.getByText("Project unavailable")).toHaveCount(0);
  await expect(
    page.getByRole("tab", { name: "Workspace", exact: true }),
  ).toBeVisible();
});

test("project detail fails closed when the initial project read is denied", async ({
  page,
}) => {
  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ detail: "project access denied" }),
    });
  });
  await page.route(
    `**/api/projects/${PROJECT_ID}/state-history`,
    async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    },
  );
  await page.route(`**/api/projects/${PROJECT_ID}/decisions`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/transition`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/workspace`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(WORKSPACE_FIXTURE),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/repository`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workspace: null }),
    });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);

  await expect(
    page.getByRole("region", { name: "Project access status" }),
  ).toContainText("The project definition cannot be read");
  await expect(page.getByRole("heading", { name: "Project access denied" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh project data" })).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "Project views" })).toHaveCount(0);
});

test("project detail retains the last-known header but hides controls after read denial", async ({
  page,
}) => {
  let projectReads = 0;
  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    projectReads += 1;
    if (projectReads > 1) {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "project authorization expired" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECT_FIXTURE),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/state-history`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/decisions`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/transition`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/workspace`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(WORKSPACE_FIXTURE),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/repository`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workspace: null }),
    });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await expect(page.getByRole("heading", { name: PROJECT_FIXTURE.name })).toBeVisible();

  await page.getByRole("button", { name: "Refresh project data" }).click();
  await expect(
    page.getByRole("region", { name: "Project access status" }),
  ).toContainText("last-known project header");
  await expect(page.getByRole("heading", { name: PROJECT_FIXTURE.name })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh project data" })).toHaveCount(0);
  await expect(page.getByRole("tablist", { name: "Project views" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
});

test("project detail enters the same denied state when a workflow mutation loses authorization", async ({
  page,
}) => {
  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(PROJECT_FIXTURE),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/state-history`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/decisions`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/transition`, async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "transition authorization denied" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(["PAUSE"]),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/workspace`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(WORKSPACE_FIXTURE),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/repository`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ workspace: null }),
    });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("tab", { name: "Workflow", exact: true }).click();
  await page.getByRole("button", { name: "PAUSE", exact: true }).click();

  await expect(
    page.getByRole("region", { name: "Project access status" }),
  ).toContainText("last-known project header");
  await expect(page.getByRole("heading", { name: PROJECT_FIXTURE.name })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Project views" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh project data" })).toHaveCount(0);
});
