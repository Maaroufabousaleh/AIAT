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

test("prepared project exposes the canonical start action and updates its state", async ({
  page,
}) => {
  let currentState = "INIT";
  let startCalls = 0;
  const currentProject = () => ({
    ...PROJECT_FIXTURE,
    state: currentState,
  });

  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(currentProject()),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/state-history`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/decisions`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/transition`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(currentState === "INIT" ? ["project_created"] : []),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/start`, async (route) => {
    startCalls += 1;
    currentState = "FEASIBILITY_CHECK";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        project_id: PROJECT_ID,
        state: currentState,
        status: "started",
        directive_delivery: "accepted",
      }),
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
  await expect(
    page.getByRole("button", { name: "Start CEO work" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Workflow", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "project created", exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Start CEO work" }).click();
  await expect(
    page.getByRole("status", { name: "Project state: FEASIBILITY CHECK" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Start CEO work" })).toHaveCount(0);
  expect(startCalls).toBe(1);
});

test("generated project documents can be previewed and downloaded through project-scoped routes", async ({
  page,
}) => {
  const documentId = "30000000-0000-4000-8000-000000000303";
  const blobKey = "private/project-output/pdr-v2.md";
  let previewStatus = 200;
  let downloadStatus = 200;
  const requests: string[] = [];

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
  await page.route(`**/api/projects/${PROJECT_ID}/context`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: documentId,
          document_id: documentId,
          project_id: PROJECT_ID,
          item_type: "DOCUMENT",
          name: "PDR v2",
          doc_type: "PDR",
          version: 2,
          status: "APPROVED",
          source: "document",
          read_only: true,
          blob_key: blobKey,
          mime_type: "text/markdown",
          description: "Generated project requirements document",
          created_by: "orchestrator",
          created_at: "2026-08-01T00:00:00Z",
        },
      ]),
    });
  });
  await page.route(
    `**/api/projects/${PROJECT_ID}/documents/${documentId}/preview`,
    async (route) => {
      requests.push(route.request().url());
      if (previewStatus !== 200) {
        await route.fulfill({
          status: previewStatus,
          contentType: "application/json",
          body: JSON.stringify({ error: `Document preview failed with status ${previewStatus}` }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "text/markdown; charset=utf-8",
        body: "# Project requirements\n\nGenerated safely.",
      });
    },
  );
  await page.route(
    `**/api/projects/${PROJECT_ID}/documents/${documentId}/download`,
    async (route) => {
      requests.push(route.request().url());
      if (downloadStatus !== 200) {
        await route.fulfill({
          status: downloadStatus,
          contentType: "application/json",
          body: JSON.stringify({ error: `Document download failed with status ${downloadStatus}` }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "text/markdown; charset=utf-8",
        headers: { "content-disposition": 'attachment; filename="pdr-v2.md"' },
        body: "# Project requirements\n\nGenerated safely.",
      });
    },
  );

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  await expect(page.getByText("PDR v2", { exact: true })).toBeVisible();
  await expect(page.getByText(blobKey, { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Preview PDR v2" }).click();
  const previewDialog = page.getByRole("dialog", { name: "PDR v2" });
  await expect(previewDialog).toBeVisible();
  await expect(previewDialog.getByText("# Project requirements")).toBeVisible();
  await previewDialog.getByRole("button", { name: "Close document preview" }).click();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download PDR v2" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("pdr-v2.md");

  previewStatus = 404;
  await page.getByRole("button", { name: "Preview PDR v2" }).click();
  await expect(
    page.getByRole("dialog", { name: "PDR v2" }).getByRole("alert"),
  ).toHaveText("Document preview failed with status 404");
  await page.getByRole("button", { name: "Close document preview" }).click();

  downloadStatus = 403;
  await page.getByRole("button", { name: "Download PDR v2" }).click();
  await expect(
    page.getByText("Document download failed with status 403", { exact: true }),
  ).toBeVisible();

  expect(requests).toHaveLength(4);
  expect(requests.every((url) => url.includes(`/projects/${PROJECT_ID}/documents/${documentId}/`))).toBe(true);
  expect(requests.some((url) => url.includes(blobKey))).toBe(false);
});

test("flow approval submits the exact bound project gate and refreshes flow state", async ({
  page,
}) => {
  const flowId = "flow-project-detail-approval";
  const instanceId = "flow-instance-project-detail-approval";
  const executionId = 7301;
  const gateId = "30000000-0000-4000-8000-000000000311";
  const unrelatedGateId = "30000000-0000-4000-8000-000000000312";
  const approvalNodeId = "operator-approval";
  const flowDefinition = {
    schema_version: "aiat.flow-node-schemas.v1.0",
    nodes: [
      {
        id: approvalNodeId,
        type: "approval",
        label: "Operator approval",
        config: { approver_role: "human_operator" },
        position: { x: 120, y: 100 },
      },
    ],
    edges: [],
  };
  const unrelatedGate = {
    id: unrelatedGateId,
    project_id: PROJECT_ID,
    gate_type: "PROJECT_REVIEW",
    status: "PENDING",
    flow_node_execution_id: null,
    created_at: "2026-08-03T00:00:00Z",
  };
  const boundGate = {
    id: gateId,
    project_id: PROJECT_ID,
    gate_type: "FLOW_APPROVAL",
    status: "PENDING",
    flow_node_execution_id: executionId,
    created_at: "2026-08-01T00:00:00Z",
  };
  let exposeBoundGate = false;
  let decisionPosts = 0;
  let projectReads = 0;
  let decisionReads = 0;
  let flowInstanceReads = 0;
  let executionReads = 0;
  let directNodeActionCalls = 0;
  const submittedDecisions: Array<{ gate_id?: string; decision?: string }> = [];
  let flowInstance = {
    id: instanceId,
    flow_id: flowId,
    flow_version: 1,
    project_id: PROJECT_ID,
    active_node_ids: [approvalNodeId],
    status: "WAITING_APPROVAL",
    context_json: {},
    retry_count: 0,
    max_retries: 3,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
  let executions: Array<Record<string, unknown>> = [
    {
      id: executionId,
      instance_id: instanceId,
      node_id: approvalNodeId,
      node_type: "approval",
      node_label: "Operator approval",
      status: "RUNNING",
      input_json: {},
      started_at: "2026-08-01T00:01:00Z",
    },
  ];

  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    projectReads += 1;
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
    if (route.request().method() === "POST") {
      decisionPosts += 1;
      submittedDecisions.push(route.request().postDataJSON());
      if (decisionPosts === 1) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "Temporary approval service outage." }),
        });
        return;
      }
      const submittedGateId = route.request().postDataJSON().gate_id;
      expect(submittedGateId).toBe(gateId);
      flowInstance = { ...flowInstance, status: "COMPLETED", active_node_ids: [] };
      executions = [
        {
          ...executions[0],
          status: "COMPLETED",
          output_json: { approval_gate_id: gateId },
          completed_at: "2026-08-01T00:02:00Z",
        },
      ];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "decision_recorded", gate_id: gateId }),
      });
      return;
    }
    decisionReads += 1;
    const gates = exposeBoundGate
      ? [unrelatedGate, boundGate]
      : [unrelatedGate];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(gates),
    });
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
  await page.route(`**/api/projects/${PROJECT_ID}/flow-instance`, async (route) => {
    flowInstanceReads += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(flowInstance),
    });
  });
  await page.route(`**/api/flows/${flowId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: flowId, definition_json: flowDefinition }),
    });
  });
  await page.route(`**/api/flows/instances/${instanceId}/executions`, async (route) => {
    executionReads += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(executions),
    });
  });
  await page.route(`**/api/flows/instances/${instanceId}/node-action`, async (route) => {
    directNodeActionCalls += 1;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ error: "Flow approval must use the project decision route." }),
    });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("tab", { name: "Flow", exact: true }).click();
  await expect(page.getByText("No exact pending gate is bound to this active approval.")).toBeVisible();
  await expect(page.getByTestId("approval-approve-button")).toHaveCount(0);

  exposeBoundGate = true;
  await page.getByRole("button", { name: "Refresh approval state" }).click();
  await expect(page.getByTestId("approval-approve-button")).toBeEnabled();
  await page.getByTestId("approval-approve-button").click();
  await expect(page.getByText("Temporary approval service outage.")).toBeVisible();
  await expect(page.getByTestId("approval-approve-button")).toBeEnabled();

  await page.getByTestId("approval-approve-button").click();
  await expect(page.getByText("COMPLETED", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("approval-approve-button")).toHaveCount(0);

  expect(submittedDecisions).toHaveLength(2);
  expect(submittedDecisions).toEqual([
    { gate_id: gateId, decision: "APPROVED", comments: `Submitted from flow approval node ${approvalNodeId}: approved` },
    { gate_id: gateId, decision: "APPROVED", comments: `Submitted from flow approval node ${approvalNodeId}: approved` },
  ]);
  expect(decisionPosts).toBe(2);
  expect(projectReads).toBeGreaterThan(1);
  expect(decisionReads).toBeGreaterThan(1);
  expect(flowInstanceReads).toBeGreaterThan(1);
  expect(executionReads).toBeGreaterThan(1);
  expect(directNodeActionCalls).toBe(0);
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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
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
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
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

test("project detail links its evidence summary to the inspectable evidence items", async ({
  page,
}) => {
  const evidencePackage = {
    schema_version: "aiat.project-evidence-package.v1",
    project_id: PROJECT_ID,
    policy_id: "software-delivery",
    policy_version: "1.0",
    status: "incomplete",
    completeness_score: 0.5,
    checks: [],
    categories: [
      {
        category: "worker",
        status: "present",
        required: true,
        item_count: 1,
        evidence_refs: ["worker-run-1"],
      },
    ],
    items: [
      {
        id: "worker-run-1",
        category: "worker",
        kind: "worker-run",
        status: "completed",
        source: "canonical",
      },
    ],
    notices: [],
  };

  await page.route(`**/api/projects/${PROJECT_ID}`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(PROJECT_FIXTURE) });
  });
  for (const suffix of ["state-history", "decisions", "transition"]) {
    await page.route(`**/api/projects/${PROJECT_ID}/${suffix}`, async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
  }
  await page.route(`**/api/projects/${PROJECT_ID}/workspace`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(WORKSPACE_FIXTURE) });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/repository`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ workspace: null }) });
  });
  await page.route(`**/api/governance/runs?**`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/issues?**`, async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/evidence`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        policy_id: "software-delivery",
        policy_version: "1.0",
        status: "incomplete",
        completeness_score: 0.5,
        checks: [],
        evidence_refs: {},
      }),
    });
  });
  await page.route(`**/api/projects/${PROJECT_ID}/evidence/package`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(evidencePackage),
    });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByRole("tab", { name: "Evidence", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evidence package coverage" })).toBeVisible();

  const detailsLink = page.getByRole("link", { name: "Open project evidence details" });
  await expect(detailsLink).toHaveAttribute("href", `/projects/${PROJECT_ID}/evidence`);
  await detailsLink.click();

  await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT_ID}/evidence$`));
  await expect(page.getByRole("table", { name: "Evidence items" })).toContainText("worker-run-1");
});
