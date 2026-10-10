import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const department = {
  teamId: "dept_production",
  displayName: "Production",
  tier: "admin",
  admin: {
    agentId: "production-admin",
    displayName: "Production Lead",
    role: "department_admin",
    class: "admin",
    budget: { max_llm_calls: 10, max_tool_calls: 20, max_cost_usd: 5 },
    tools: ["read_project"],
  },
  workers: [
    {
      agentId: "research-worker",
      displayName: "Research Worker",
      role: "research",
      minInstances: 0,
      maxInstances: 1,
      budget: { max_llm_calls: 5, max_tool_calls: 10, max_cost_usd: 2 },
      tools: ["browser_search"],
    },
  ],
};

test("operator can inspect a department and open the canonical worker registry", async ({
  page,
}) => {
  const interceptedPaths: string[] = [];
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    interceptedPaths.push(url.pathname);

    const payloadByPath: Record<string, unknown> = {
      "/api/system/hierarchy": {
        teams: [department],
        hierarchy: [
          {
            teamId: department.teamId,
            displayName: department.displayName,
            role: "Production department",
            admin: {
              agentId: department.admin.agentId,
              displayName: department.admin.displayName,
              role: department.admin.role,
              tools: department.admin.tools,
            },
            workers: department.workers,
            tier: department.tier,
            children: [],
          },
        ],
      },
      "/api/system/permissions": {
        policy: {},
        teamTiers: { [department.teamId]: department.tier },
        messageTypes: {},
        communicationMatrix: {},
      },
      "/api/system/orchestration": {
        states: [],
        transitions: {},
        flows: [],
        dbFlows: [],
      },
      "/api/system/org-graph": { nodes: [], edges: [], capability_edges: [] },
      "/api/workers": [
        {
          id: "worker-production-001",
          worker_id: "research-worker",
          name: "Research Worker",
          team_id: department.teamId,
          status: "ACTIVE",
          evaluation_status: "approved",
          version: "1.0.0",
        },
        {
          id: "worker-qa-001",
          worker_id: "qa-worker",
          name: "QA Worker",
          team_id: "dept_qa",
          status: "ACTIVE",
          evaluation_status: "approved",
          version: "1.0.0",
        },
      ],
      "/api/runtimes": { runtimes: [] },
    };

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payloadByPath[url.pathname] ?? {}),
    });
  });

  await authenticate(page, "/system-viz");
  const system = page.getByRole("main", { name: "System Visualization" });
  await expect(system.getByRole("heading", { name: "System Visualization" })).toBeVisible();
  await page
    .locator('[aria-label="Production: communication policy overlay off"]')
    .click();

  const details = page.getByRole("complementary", { name: "Selection details" });
  await expect(details.getByText("Production", { exact: true })).toBeVisible();
  await expect(details.getByText("Research Worker", { exact: true })).toBeVisible();
  await expect(
    details.getByText("Search the roster by team ID:").locator(".."),
  ).toContainText(department.teamId);

  const openWorkers = details.getByRole("link", {
    name: `Open worker registry for ${department.displayName}`,
  });
  await page.evaluate(() => {
    window.localStorage.setItem("mas.workers.search", "stale-previous-team");
  });
  await expect(openWorkers).toHaveAttribute(
    "href",
    `/workers?team_id=${department.teamId}`,
  );
  await openWorkers.click();
  await expect(page).toHaveURL(new RegExp(`/workers\\?team_id=${department.teamId}$`));
  await expect(page.getByRole("heading", { name: "Hiring Board" })).toBeVisible();
  const registry = page.getByRole("region", { name: "Registered workers" });
  await expect(registry).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "Search workers" })).toHaveValue(
    department.teamId,
  );
  await expect(registry.getByLabel("research-worker worker row")).toBeVisible();
  await expect(registry.getByLabel("qa-worker worker row")).toHaveCount(0);

  expect(interceptedPaths).toEqual(
    expect.arrayContaining([
      "/api/system/hierarchy",
      "/api/system/permissions",
      "/api/system/orchestration",
      "/api/system/org-graph",
      "/api/workers",
      "/api/runtimes",
    ]),
  );
});

test("worker registry ignores malformed team ID query filters", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(url.pathname === "/api/workers" ? [] : {}),
    });
  });

  await authenticate(page, "/workers?team_id=dept%2Fproduction");
  await expect(page.getByRole("searchbox", { name: "Search workers" })).toHaveValue("");
});
