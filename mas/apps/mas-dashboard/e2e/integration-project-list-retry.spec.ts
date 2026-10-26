import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const PROJECT_ID = "81818181-8181-4181-8181-818181818181";

test("PM project setup can retry a failed project-list read without refreshing integration state", async ({ page }) => {
  let projectReads = 0;
  const mutations: string[] = [];

  await page.route("**/api/projects**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname !== "/api/projects" || url.searchParams.get("limit") !== "1000") {
      await route.continue();
      return;
    }

    if (request.method() !== "GET") mutations.push(`${request.method()} ${url.pathname}`);
    projectReads += 1;
    if (projectReads === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "fixture project read unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: PROJECT_ID, name: "Retryable PM project", state: "ACTIVE" }]),
    });
  });

  let integrationReads = 0;
  await page.route("**/api/integrations/pm**", async (route) => {
    if (route.request().method() !== "GET") {
      mutations.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    }
    integrationReads += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connections: [],
        conflicts: [],
        outbox: [],
        runs: [],
        lifecyclePlans: [],
      }),
    });
  });

  await authenticate(page, "/integrations");
  const integrations = page.getByRole("main", { name: "PM integrations" });
  const projectSetup = integrations.getByRole("region", { name: "Provision and bind a project" });

  await expect(projectSetup.getByRole("alert")).toContainText("Projects could not be loaded (HTTP 503)");
  await expect(projectSetup.getByRole("button", { name: "Retry project list" })).toBeVisible();
  await projectSetup.getByRole("button", { name: "Retry project list" }).click();

  await expect(projectSetup.getByLabel("Project to provision")).toContainText("Retryable PM project");
  expect(projectReads).toBe(2);
  expect(integrationReads).toBe(1);
  expect(mutations).toEqual([]);
});
