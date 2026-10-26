import { expect, test } from "@playwright/test";

import type {
  Flow,
  FlowDefinition,
  FlowInstance,
  FlowNodeExecution,
} from "@/lib/flow-types";
import { authenticate } from "./auth";

const PROJECT_ID = "alpha-flow-project";
const FLOW_ID = "alpha-software-flow";
const INSTANCE_ID = "alpha-flow-instance";

const STARTER_DEFINITION: FlowDefinition = {
  schema_version: "aiat.flow-node-schemas.v1.0",
  nodes: [
    {
      id: "start",
      type: "start",
      label: "Start",
      config: {},
      position: { x: 80, y: 80 },
    },
    {
      id: "work",
      type: "task",
      label: "Build feature",
      config: { action: "implement" },
      position: { x: 280, y: 80 },
    },
    {
      id: "finish",
      type: "end",
      label: "Complete",
      config: {},
      position: { x: 480, y: 80 },
    },
  ],
  edges: [
    { id: "edge-start-work", source: "start", target: "work" },
    { id: "edge-work-finish", source: "work", target: "finish" },
  ],
};

function projectFixture() {
  return {
    id: PROJECT_ID,
    name: "Alpha software project",
    description: "A disposable project for the flow UI journey.",
    state: "INIT",
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:00Z",
  };
}

function blankWorkspace() {
  return {
    next_actions: [],
    pending_approvals: [],
    recent_activity: [],
    worker_activity: [],
    artifacts: [],
    logs: [],
    cost_usage: { available: false, reason: "No usage recorded in this fixture." },
  };
}

function instanceFixture(
  status: FlowInstance["status"],
  activeTaskNodeId: string,
): FlowInstance {
  return {
    id: INSTANCE_ID,
    flow_id: FLOW_ID,
    flow_version: 1,
    project_id: PROJECT_ID,
    active_node_ids: status === "RUNNING" ? [activeTaskNodeId] : [],
    status,
    context_json: {},
    retry_count: 0,
    max_retries: 3,
    created_at: "2026-10-01T12:00:00Z",
    updated_at: "2026-10-01T12:00:00Z",
  };
}

function executionFixture(
  status: FlowNodeExecution["status"],
  activeTaskNodeId: string,
): FlowNodeExecution {
  return {
    id: `execution-${status.toLowerCase()}`,
    instance_id: INSTANCE_ID,
    node_id: activeTaskNodeId,
    node_type: "task",
    node_label: "Build feature",
    status,
    error: status === "FAILED" ? "A local task step failed." : undefined,
    started_at: "2026-10-01T12:01:00Z",
    completed_at: status === "FAILED" ? "2026-10-01T12:02:00Z" : undefined,
  };
}

test("operator builds, edits, starts, and retries a project flow", async ({
  page,
}) => {
  let flow: Flow | null = null;
  let activeTaskNodeId: string | null = null;
  let instance: FlowInstance | null = null;
  let executions: FlowNodeExecution[] = [];
  let retryAttempts = 0;
  const unhandledApiRequests: string[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname, searchParams } = url;
    const method = request.method();
    const fulfill = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify(body),
      });

    if (pathname === "/api/workers") return fulfill([]);
    if (pathname === "/api/governance/model-profiles") return fulfill([]);
    if (pathname === "/api/flow-templates") {
      return fulfill({
        templates: [
          {
            template_id: "software_delivery",
            name: "Software delivery starter",
            description: "A small start, implementation, and completion path.",
            definition_json: STARTER_DEFINITION,
          },
        ],
      });
    }
    if (pathname === "/api/flows/dry-run" && method === "POST") {
      return fulfill({ valid: true, errors: [], warnings: [] });
    }
    if (pathname === "/api/flows" && method === "GET") {
      const result = flow && (!searchParams.has("is_active") || flow.is_active)
        ? [flow]
        : [];
      return fulfill(result);
    }
    if (pathname === "/api/flows" && method === "POST") {
      const data = request.postDataJSON() as Omit<Flow, "id" | "version" | "created_by" | "created_at" | "updated_at">;
      flow = {
        ...data,
        id: FLOW_ID,
        version: 1,
        created_by: "e2e-operator",
        created_at: "2026-10-01T12:00:00Z",
        updated_at: "2026-10-01T12:00:00Z",
      };
      activeTaskNodeId =
        flow.definition_json.nodes.find((node) => node.type === "task")?.id ??
        null;
      return fulfill(flow, 201);
    }
    if (pathname === `/api/flows/${FLOW_ID}` && method === "GET") {
      return flow ? fulfill(flow) : fulfill({ error: "Flow not found" }, 404);
    }
    if (pathname === `/api/flows/${FLOW_ID}` && method === "PUT") {
      const changes = request.postDataJSON() as Partial<Flow>;
      flow = flow ? { ...flow, ...changes, updated_at: "2026-10-01T12:03:00Z" } : null;
      return flow ? fulfill(flow) : fulfill({ error: "Flow not found" }, 404);
    }
    if (pathname === "/api/projects" && method === "GET") {
      return fulfill([projectFixture()]);
    }
    if (pathname === "/api/projects" && method === "POST") {
      return fulfill(projectFixture(), 201);
    }
    if (pathname === `/api/projects/${PROJECT_ID}` && method === "GET") {
      return fulfill(projectFixture());
    }
    if (pathname === `/api/projects/${PROJECT_ID}/state-history`) {
      return fulfill([]);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/decisions`) {
      return fulfill([]);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/transition`) {
      return fulfill([]);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/workspace`) {
      return fulfill(blankWorkspace());
    }
    if (pathname === "/api/governance/runs" && method === "GET") {
      return fulfill([]);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/issues` && method === "GET") {
      return fulfill([]);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/repository`) {
      return fulfill({ workspace: null });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/flow-instance`) {
      if (method === "GET") {
        return instance ? fulfill(instance) : fulfill({ detail: "No flow attached" }, 404);
      }
      if (method === "POST" && flow) {
        if (!activeTaskNodeId) {
          return fulfill({ error: "Saved flow has no task node" }, 409);
        }
        instance = instanceFixture("NOT_STARTED", activeTaskNodeId);
        executions = [];
        return fulfill(instance, 201);
      }
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/executions` && method === "GET") {
      return fulfill(executions);
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/action` && method === "POST") {
      const body = request.postDataJSON() as { action?: string };
      if (body.action === "start") {
        if (!activeTaskNodeId) {
          return fulfill({ error: "Saved flow has no task node" }, 409);
        }
        instance = {
          ...instanceFixture("RUNNING", activeTaskNodeId),
          started_at: "2026-10-01T12:04:00Z",
        };
        executions = [executionFixture("RUNNING", activeTaskNodeId)];
      }
      return fulfill(instance);
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/node-action` && method === "POST") {
      const body = request.postDataJSON() as { action?: string };
      if (body.action === "fail") {
        if (!activeTaskNodeId) {
          return fulfill({ error: "Saved flow has no task node" }, 409);
        }
        instance = {
          ...instanceFixture("FAILED", activeTaskNodeId),
          retry_count: 0,
        };
        executions = [executionFixture("FAILED", activeTaskNodeId)];
      }
      return fulfill(instance);
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/retry` && method === "POST") {
      retryAttempts += 1;
      if (retryAttempts === 1) {
        return fulfill({ error: "Temporary flow retry issue." }, 503);
      }
      if (!activeTaskNodeId) {
        return fulfill({ error: "Saved flow has no task node" }, 409);
      }
      instance = { ...instanceFixture("RUNNING", activeTaskNodeId), retry_count: 1 };
      executions = [
        executionFixture("FAILED", activeTaskNodeId),
        executionFixture("RUNNING", activeTaskNodeId),
      ];
      return fulfill(instance);
    }

    unhandledApiRequests.push(`${method} ${pathname}`);
    return fulfill({ error: `No fixture for ${method} ${pathname}` }, 404);
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("flow-name-input").fill("Alpha Flow");
  await page.getByRole("checkbox", { name: "Activate flow on save" }).check();
  await page.getByTestId("template-software_delivery").click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByTestId("flow-dry-run-button").click();
  await expect(page.getByTestId("flow-dry-run-result")).toContainText("Flow is ready to run");
  await page.getByTestId("flow-save-button").click();

  await expect(page).toHaveURL(new RegExp(`/flows/${FLOW_ID}$`));
  await expect(page.getByTestId("flow-name-input")).toHaveValue("Alpha Flow");
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByTestId("flow-name-input").fill("Alpha Product Flow");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByTestId("flow-save-status")).toContainText("Saved");
  await expect(page.getByTestId("flow-run-guidance")).toContainText(
    "attach, start, and monitor this flow",
  );
  await page.getByRole("link", { name: "Open projects" }).click();

  await expect(page.getByRole("link", { name: "Open Alpha software project" })).toBeVisible();
  await page.getByRole("link", { name: "Open Alpha software project" }).click();
  await expect(page.getByRole("heading", { name: "Alpha software project" })).toBeVisible();
  await page.getByTestId("project-tab-flow").click();
  await page.getByTestId(`assign-flow-${FLOW_ID}`).click();
  await expect(page.getByText("NOT_STARTED", { exact: true }).first()).toBeVisible();

  await page.getByTestId("flow-start-button").click();
  await expect(page.getByText("RUNNING", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("current-node-label")).toHaveText("Build feature");
  await page.getByTestId("fail-node-button").click();
  await expect(page.getByText("FAILED", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Execution History" }).locator(".."),
  ).toContainText("Build feature");
  await expect(page.getByTestId("flow-retry-button")).toBeVisible();

  await page.getByTestId("flow-retry-button").click();
  await expect(page.getByText("Temporary flow retry issue.")).toBeVisible();
  await page.getByTestId("flow-retry-button").click();
  await expect(page.getByText("RUNNING", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("current-node-label")).toHaveText("Build feature");
  await expect(page.getByText("Temporary flow retry issue.")).toHaveCount(0);
  expect(retryAttempts).toBe(2);

  expect(unhandledApiRequests).toEqual([]);
});
