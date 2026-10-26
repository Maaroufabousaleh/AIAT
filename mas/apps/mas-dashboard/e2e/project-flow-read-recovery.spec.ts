import { expect, test, type Page, type Route } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "project-flow-read-e2e-001";
const FLOW_ID = "flow-read-e2e-001";
const INSTANCE_ID = "flow-instance-read-e2e-001";

const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  name: "Project flow read recovery",
  description: "Exercises read-only flow recovery states.",
  state: "ACTIVE",
  created_at: "2026-10-01T12:00:00Z",
  updated_at: "2026-10-01T12:00:00Z",
};

const WORKSPACE_FIXTURE = {
  next_actions: [],
  pending_approvals: [],
  recent_activity: [],
  worker_activity: [],
  artifacts: [],
  logs: [],
  cost_usage: { available: false, reason: "No usage in this fixture." },
  repository: null,
};

const FLOW_FIXTURE = {
  id: FLOW_ID,
  name: "Read recovery flow",
  version: 1,
  definition_json: {
    schema_version: "aiat.flow-node-schemas.v1.0",
    nodes: [
      { id: "start", type: "start", label: "Start", config: {}, position: { x: 80, y: 80 } },
      { id: "work", type: "task", label: "Build feature", config: {}, position: { x: 280, y: 80 } },
      { id: "finish", type: "end", label: "Complete", config: {}, position: { x: 480, y: 80 } },
    ],
    edges: [
      { id: "start-work", source: "start", target: "work" },
      { id: "work-finish", source: "work", target: "finish" },
    ],
  },
};

const INSTANCE_FIXTURE = {
  id: INSTANCE_ID,
  flow_id: FLOW_ID,
  flow_version: 1,
  project_id: PROJECT_ID,
  active_node_ids: ["work"],
  status: "RUNNING",
  context_json: {},
  retry_count: 0,
  max_retries: 3,
  created_at: "2026-10-01T12:00:00Z",
  updated_at: "2026-10-01T12:01:00Z",
};

const EXECUTIONS_FIXTURE = [
  {
    id: "execution-read-e2e-001",
    instance_id: INSTANCE_ID,
    node_id: "work",
    node_type: "task",
    node_label: "Build feature",
    status: "RUNNING",
    started_at: "2026-10-01T12:01:00Z",
  },
];

type FixtureResponse = { status: number; body: unknown };

type FlowApiOptions = {
  flowInstance: (read: number) => FixtureResponse;
  activeFlows?: (read: number) => FixtureResponse;
};

async function installProjectApiMocks(page: Page, options: FlowApiOptions) {
  let instanceReads = 0;
  let activeFlowReads = 0;
  const mutations: string[] = [];

  const fulfill = (route: Route, response: FixtureResponse) => route.fulfill({
    status: response.status,
    contentType: "application/json",
    body: JSON.stringify(response.body),
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname, searchParams } = url;
    const method = request.method();

    if (pathname === `/api/projects/${PROJECT_ID}` && method === "GET") {
      return fulfill(route, { status: 200, body: PROJECT_FIXTURE });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/state-history` ||
        pathname === `/api/projects/${PROJECT_ID}/decisions` ||
        pathname === `/api/projects/${PROJECT_ID}/transition`) {
      return fulfill(route, { status: 200, body: [] });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/workspace`) {
      return fulfill(route, { status: 200, body: WORKSPACE_FIXTURE });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/repository`) {
      return fulfill(route, { status: 200, body: { configured: false, workspace: null } });
    }
    if (pathname === `/api/governance/runs` || pathname === `/api/projects/${PROJECT_ID}/issues`) {
      return fulfill(route, { status: 200, body: [] });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/flow-instance` && method === "GET") {
      instanceReads += 1;
      return fulfill(route, options.flowInstance(instanceReads));
    }
    if (pathname === `/api/projects/${PROJECT_ID}/flow-instance` && method === "POST") {
      mutations.push(`${method} ${pathname}`);
      return fulfill(route, { status: 201, body: INSTANCE_FIXTURE });
    }
    if (pathname === `/api/flows/${FLOW_ID}` && method === "GET") {
      return fulfill(route, { status: 200, body: FLOW_FIXTURE });
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/executions` && method === "GET") {
      return fulfill(route, { status: 200, body: EXECUTIONS_FIXTURE });
    }
    if (pathname === "/api/flows" && searchParams.get("is_active") === "true" && method === "GET") {
      activeFlowReads += 1;
      return fulfill(route, options.activeFlows?.(activeFlowReads) ?? { status: 200, body: [] });
    }

    if (method !== "GET") mutations.push(`${method} ${pathname}`);
    return fulfill(route, { status: 200, body: [] });
  });

  return { mutations };
}

async function openFlowTab(page: Page) {
  await authenticate(page, `/projects/${PROJECT_ID}`);
  await expect(page.getByRole("heading", { name: PROJECT_FIXTURE.name })).toBeVisible();
  await page.getByRole("tab", { name: "Flow", exact: true }).click();
}

test("project detail retains its loaded flow through a failed refresh and recovers", async ({ page }) => {
  const api = await installProjectApiMocks(page, {
    flowInstance: (read) => read === 2
      ? { status: 503, body: { error: "temporarily unavailable" } }
      : { status: 200, body: INSTANCE_FIXTURE },
  });

  await openFlowTab(page);
  const flowPanel = page.locator("#project-panel-flow");
  await expect(flowPanel.getByText("RUNNING", { exact: true }).first()).toBeVisible();
  await expect(flowPanel.getByText("Build feature", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Refresh project data" }).click();
  const stale = page.getByTestId("project-flow-stale");
  await expect(stale).toBeVisible();
  await expect(stale.getByText(/HTTP 503/)).toBeVisible();
  await expect(flowPanel.getByText("RUNNING", { exact: true }).first()).toBeVisible();
  await expect(flowPanel.getByText("Build feature", { exact: true }).first()).toBeVisible();

  await stale.getByRole("button", { name: "Retry flow" }).click();
  await expect(page.getByTestId("project-flow-stale")).toHaveCount(0);
  await expect(flowPanel.getByText("RUNNING", { exact: true }).first()).toBeVisible();
  expect(api.mutations).toEqual([]);
});

test("first flow-read failure shows unavailable with retry instead of a no-flow state", async ({ page }) => {
  const api = await installProjectApiMocks(page, {
    flowInstance: (read) => read === 1
      ? { status: 503, body: { error: "temporarily unavailable" } }
      : { status: 200, body: INSTANCE_FIXTURE },
  });

  await openFlowTab(page);
  const unavailable = page.getByTestId("project-flow-unavailable");
  await expect(unavailable).toBeVisible();
  await expect(unavailable.getByText(/HTTP 503/)).toBeVisible();
  await expect(page.getByText("No flow attached to this project.")).toHaveCount(0);
  await expect(page.getByText("No active flows available.")).toHaveCount(0);

  await unavailable.getByRole("button", { name: "Retry flow" }).click();
  await expect(page.getByTestId("project-flow-unavailable")).toHaveCount(0);
  await expect(page.locator("#project-panel-flow").getByText("RUNNING", { exact: true }).first()).toBeVisible();
  expect(api.mutations).toEqual([]);
});

test("canonical missing-flow 404 still offers active flows for assignment", async ({ page }) => {
  const api = await installProjectApiMocks(page, {
    flowInstance: () => ({ status: 404, body: { error: "No flow attached" } }),
    activeFlows: () => ({ status: 200, body: [FLOW_FIXTURE] }),
  });

  await openFlowTab(page);
  await expect(page.getByText("No flow attached to this project.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Read recovery flow/ })).toBeVisible();
  await expect(page.getByTestId("project-flow-unavailable")).toHaveCount(0);
  expect(api.mutations).toEqual([]);
});

test("active-flow catalogue outage is retryable and never reported as an empty catalogue", async ({ page }) => {
  const api = await installProjectApiMocks(page, {
    flowInstance: () => ({ status: 404, body: { error: "No flow attached" } }),
    activeFlows: (read) => read === 1
      ? { status: 503, body: { error: "temporarily unavailable" } }
      : { status: 200, body: [FLOW_FIXTURE] },
  });

  await openFlowTab(page);
  await expect(page.getByText("No flow attached to this project.")).toBeVisible();
  const unavailable = page.getByTestId("project-active-flows-unavailable");
  await expect(unavailable).toBeVisible();
  await expect(unavailable.getByText(/HTTP 503/)).toBeVisible();
  await expect(page.getByText("No active flows available.")).toHaveCount(0);

  await unavailable.getByRole("button", { name: "Retry active flows" }).click();
  await expect(page.getByTestId("project-active-flows-unavailable")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Read recovery flow/ })).toBeVisible();
  expect(api.mutations).toEqual([]);
});

test("flow-read authorization denial hides flow actions and does not imply no flow", async ({ page }) => {
  const api = await installProjectApiMocks(page, {
    flowInstance: () => ({ status: 403, body: { error: "forbidden" } }),
  });

  await openFlowTab(page);
  await expect(page.getByRole("region", { name: "Project access status" })).toBeVisible();
  await expect(page.getByText("No flow attached to this project.")).toHaveCount(0);
  await expect(page.getByTestId(`assign-flow-${FLOW_ID}`)).toHaveCount(0);
  expect(api.mutations).toEqual([]);
});
