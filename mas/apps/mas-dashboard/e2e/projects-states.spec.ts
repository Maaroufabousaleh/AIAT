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

test("project creation can save a prepared project without starting CEO work", async ({
  page,
}) => {
  const projectId = "projects-e2e-prepared";
  const projects: typeof PROJECT_FIXTURE = [];
  const createRequest: { body: Record<string, unknown> | null } = {
    body: null,
  };
  let projectState = "INIT";
  let startCalls = 0;

  // Every API request in this journey is intercepted. This keeps the test
  // independent of live services and makes provider dispatch impossible.
  await page.route("**/api/**", async (route) => {
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "No API fixture for this request" }),
    });
  });

  await page.route("**/api/projects**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const method = route.request().method();

    if (pathname === `/api/projects/${projectId}/start` && method === "POST") {
      startCalls += 1;
      if (startCalls === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "CEO queue is temporarily unavailable" }),
        });
        return;
      }
      projectState = "FEASIBILITY_CHECK";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          project_id: projectId,
          state: projectState,
          status: "started",
          directive_delivery: "accepted",
        }),
      });
      return;
    }

    if (route.request().method() === "POST") {
      if (pathname !== "/api/projects") {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "No project mutation fixture" }),
        });
        return;
      }
      const body = route.request().postDataJSON() as Record<string, unknown>;
      createRequest.body = body;
      const created = {
        id: projectId,
        name: String(body.name),
        description: typeof body.description === "string" ? body.description : "",
        state: body.start_immediately === false ? "INIT" : "FEASIBILITY_CHECK",
        created_at: "2026-10-07T00:00:00Z",
        updated_at: "2026-10-07T00:00:00Z",
      };
      projectState = created.state;
      projects.splice(0, projects.length, created);
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(created),
      });
      return;
    }

    if (pathname === "/api/projects") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(projects),
      });
      return;
    }

    if (pathname === `/api/projects/${projectId}`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...projects[0], state: projectState }),
      });
      return;
    }

    if (pathname === `/api/projects/${projectId}/transition`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(projectState === "INIT" ? ["project_created"] : []),
      });
      return;
    }

    if (
      pathname === `/api/projects/${projectId}/state-history` ||
      pathname === `/api/projects/${projectId}/decisions` ||
      pathname === `/api/projects/${projectId}/context` ||
      pathname === `/api/projects/${projectId}/evidence`
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
      return;
    }

    if (pathname === `/api/projects/${projectId}/workspace`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          next_actions: [],
          pending_approvals: [],
          recent_activity: [],
          worker_activity: [],
          artifacts: [],
          logs: [],
          cost_usage: { available: false, reason: "No usage in this fixture" },
          repository: null,
        }),
      });
      return;
    }

    if (
      pathname === `/api/projects/${projectId}/repository` ||
      pathname === `/api/projects/${projectId}/evidence/package`
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(pathname.endsWith("/repository") ? { workspace: null } : null),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: "No project API fixture" }),
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
  await page.getByRole("button", { name: "New Project" }).first().click();
  await page.getByPlaceholder("my-project").fill("Prepared alpha project");
  const startWork = page.getByRole("checkbox", {
    name: "Start CEO feasibility after creation",
  });
  await expect(startWork).toBeChecked();
  await startWork.uncheck();
  await page.getByRole("button", { name: "Create", exact: true }).click();

  await expect
    .poll(() => createRequest.body?.start_immediately)
    .toBe(false);
  await expect(
    page.getByRole("link", { name: "Prepared alpha project", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("INIT", { exact: true })).toBeVisible();
  expect(startCalls).toBe(0);

  await page
    .getByRole("link", { name: "Prepared alpha project", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Prepared alpha project" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start CEO work" }),
  ).toBeVisible();
  expect(startCalls).toBe(0);

  await page.getByRole("button", { name: "Start CEO work" }).click();
  await expect(page.getByText("CEO queue is temporarily unavailable")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry start" })).toBeVisible();
  await expect(
    page.getByRole("status", { name: "Project state: INIT" }),
  ).toBeVisible();
  expect(startCalls).toBe(1);

  await page.getByRole("button", { name: "Retry start" }).click();
  await expect(
    page.getByRole("status", { name: "Project state: FEASIBILITY CHECK" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start CEO work" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry start" })).toHaveCount(0);
  expect(startCalls).toBe(2);
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
