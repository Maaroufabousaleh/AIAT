import { expect, test, type Route } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "50000000-0000-4000-8000-000000000001";
const FLOW_ID = "50000000-0000-4000-8000-000000000002";
const INSTANCE_ID = "50000000-0000-4000-8000-000000000003";
const WORKER_ID = "50000000-0000-4000-8000-000000000004";
const RESEARCH_RUN_ID = "50000000-0000-4000-8000-000000000005";
const APPROVAL_ID = "50000000-0000-4000-8000-000000000006";
const REPORT_RUN_ID = "50000000-0000-4000-8000-000000000007";

type FlowFixtureNode = {
  id: string;
  type: string;
  label: string;
  config: Record<string, unknown>;
};

const RESEARCH_TEMPLATE = {
  template_id: "research",
  name: "Research brief",
  description: "Bind a governed worker and any required approved Model Profile to each task before activation.",
  definition_json: {
    schema_version: "1.0",
    metadata: { template_id: "research", evidence_policy: "research" },
    nodes: [
      { id: "start", type: "start", label: "Start", config: {} },
      {
        id: "research",
        type: "task",
        label: "Research",
        config: { team_id: "office_cio", action: "research.collect" },
      },
      {
        id: "review",
        type: "approval",
        label: "Review findings",
        config: { approver_role: "cio" },
      },
      {
        id: "report",
        type: "task",
        label: "Write report",
        config: { team_id: "dept_system", action: "document.write" },
      },
      { id: "end", type: "end", label: "Complete", config: {} },
    ],
    edges: [
      { id: "start-research", source: "start", target: "research" },
      { id: "research-review", source: "research", target: "review" },
      { id: "review-report", source: "review", target: "report" },
      { id: "report-end", source: "report", target: "end" },
    ],
  },
};

function evidencePackage(isComplete: boolean) {
  return {
    schema_version: "aiat.project-evidence-package.v1",
    project_id: PROJECT_ID,
    policy_id: "software-delivery",
    policy_version: "1.0",
    status: isComplete ? "complete" : "incomplete",
    completeness_score: isComplete ? 1 : 0,
    checks: [
      { name: "worker_runs_terminal", required: true, passed: isComplete },
      { name: "flow_terminal", required: true, passed: isComplete },
      { name: "required_artifacts", required: true, passed: isComplete },
    ],
    categories: [
      { category: "worker", status: isComplete ? "present" : "missing", required: true, item_count: isComplete ? 2 : 0, evidence_refs: isComplete ? [RESEARCH_RUN_ID, REPORT_RUN_ID] : [] },
      { category: "tests", status: isComplete ? "present" : "missing", required: true, item_count: isComplete ? 1 : 0, evidence_refs: isComplete ? ["42"] : [] },
    ],
    items: isComplete ? [
      { id: RESEARCH_RUN_ID, category: "workers", kind: "worker_run", status: "SUCCEEDED", source: "worker_runs" },
      { id: REPORT_RUN_ID, category: "workers", kind: "worker_run", status: "SUCCEEDED", source: "worker_runs" },
      { id: "42", category: "tests", kind: "research-report", status: "stored", source: "artifacts", checksum: "sha256:fixture-report" },
    ] : [],
    notices: [],
  };
}

test("Research Brief template runs through Project Detail review and evidence", async ({ page }) => {
  let projectState = "INIT";
  let createdProject: Record<string, unknown> | null = null;
  let savedFlow: Record<string, unknown> | null = null;
  const flowCreateRequests: Array<Record<string, unknown>> = [];
  const projectCreateRequests: Array<Record<string, unknown>> = [];
  const dryRunRequests: Array<Record<string, unknown>> = [];
  const flowNodeIds = { start: "", research: "", review: "", report: "" };
  const runStates: Record<string, string> = {};
  const createdRunIds: string[] = [];
  let flowInstance = {
    id: INSTANCE_ID,
    flow_id: FLOW_ID,
    flow_version: 1,
    project_id: PROJECT_ID,
    active_node_ids: [] as string[],
    status: "NOT_STARTED",
    context_json: {} as Record<string, unknown>,
    created_at: "2026-10-25T12:00:00Z",
    updated_at: "2026-10-25T12:00:00Z",
  };
  let executions: Array<Record<string, unknown>> = [];
  let decisions: Array<Record<string, unknown>> = [];
  let approvalRecorded = false;
  const settlementRequests: Array<Record<string, unknown>> = [];
  const dispatchRequests: Array<Record<string, unknown>> = [];
  const decisionRequests: Array<Record<string, unknown>> = [];
  const unhandledRequests: string[] = [];

  const fulfill = (route: Route, body: unknown, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
  const savedNode = (nodeId: string) => {
    const definition = savedFlow?.definition_json as
      | { nodes: FlowFixtureNode[] }
      | undefined;
    return definition?.nodes.find((node) => node.id === nodeId);
  };

  const projectEvidenceReady = () =>
    flowInstance.status === "COMPLETED" &&
    approvalRecorded &&
    runStates[RESEARCH_RUN_ID] === "SUCCEEDED" &&
    runStates[REPORT_RUN_ID] === "SUCCEEDED";

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;
    const method = request.method();

    if (pathname === "/api/workers" && method === "GET") {
      return fulfill(route, [{
        id: WORKER_ID,
        name: "Approved research worker",
        status: "ACTIVE",
        adapter_type: "opencode",
        model_mode: "aiat_gateway",
      }]);
    }
    if (pathname === "/api/governance/model-profiles" && method === "GET") {
      return fulfill(route, [{
        profile_id: "approved-research-profile",
        status: "approved",
        purpose: "research",
      }]);
    }
    if (pathname === "/api/flow-templates" && method === "GET") {
      return fulfill(route, {
        schema_version: "aiat.flow-template.v1",
        templates: [RESEARCH_TEMPLATE],
      });
    }
    if (pathname === "/api/flows/dry-run" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      dryRunRequests.push(body);
      const definition = body.definition_json as { nodes: FlowFixtureNode[] };
      const tasks = definition.nodes.filter((node) => node.type === "task");
      if (tasks.some((node) => !node.config.worker_id || !node.config.model_profile_id)) {
        return fulfill(route, {
          valid: false,
          errors: [{ code: "RESEARCH_TEMPLATE_BINDING_REQUIRED", message: "Bind a worker and Model Profile to each task." }],
          warnings: [],
        });
      }
      return fulfill(route, { valid: true, errors: [], warnings: [] });
    }
    if (pathname === "/api/flows" && method === "GET") {
      return fulfill(route, savedFlow ? [savedFlow] : []);
    }
    if (pathname === "/api/flows" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      flowCreateRequests.push(body);
      savedFlow = {
        ...body,
        id: FLOW_ID,
        version: 1,
        created_by: "e2e-operator",
        created_at: "2026-10-25T12:00:00Z",
        updated_at: "2026-10-25T12:00:00Z",
      };
      const definition = body.definition_json as { nodes: FlowFixtureNode[] };
      flowNodeIds.start = definition.nodes.find((node) => node.type === "start")?.id ?? "";
      flowNodeIds.research = definition.nodes.find((node) => node.config.action === "research.collect")?.id ?? "";
      flowNodeIds.review = definition.nodes.find((node) => node.type === "approval")?.id ?? "";
      flowNodeIds.report = definition.nodes.find((node) => node.config.action === "document.write")?.id ?? "";
      return fulfill(route, savedFlow, 201);
    }
    if (pathname === "/api/projects" && method === "GET") {
      return fulfill(route, createdProject ? [createdProject] : []);
    }
    if (pathname === "/api/projects" && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      projectCreateRequests.push(body);
      projectState = body.start_immediately === true ? "KPI_PERSISTENCE" : "INIT";
      createdProject = {
        id: PROJECT_ID,
        name: String(body.name ?? "Research Brief integration project"),
        description: body.description ?? null,
        state: projectState,
        created_at: "2026-10-25T12:00:00Z",
        updated_at: "2026-10-25T12:00:00Z",
      };
      if (body.flow_id === FLOW_ID) {
        flowInstance = {
          ...flowInstance,
          project_id: PROJECT_ID,
          flow_id: FLOW_ID,
          status: "NOT_STARTED",
          active_node_ids: [],
        };
      }
      return fulfill(route, createdProject, 201);
    }
    if (pathname === `/api/flows/${FLOW_ID}` && method === "GET") {
      return savedFlow
        ? fulfill(route, savedFlow)
        : fulfill(route, { detail: "Flow is not available." }, 404);
    }

    if (pathname === `/api/projects/${PROJECT_ID}` && method === "GET") {
      return fulfill(route, {
        id: PROJECT_ID,
        name: "Research Brief integration project",
        description: "A project-scoped governed research fixture.",
        state: projectState,
        created_at: "2026-10-25T12:00:00Z",
        updated_at: "2026-10-25T12:00:00Z",
      });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/state-history` && method === "GET") return fulfill(route, []);
    if (pathname === `/api/projects/${PROJECT_ID}/decisions` && method === "GET") return fulfill(route, decisions);
    if (pathname === `/api/projects/${PROJECT_ID}/transition` && method === "GET") {
      return fulfill(route, projectState === "COMPLETED" ? [] : ["kpi_saved"]);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/transition` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      if (body.event !== "kpi_saved") return fulfill(route, { error: "Unexpected project event." }, 409);
      if (!projectEvidenceReady()) {
        return fulfill(route, {
          detail: {
            code: "PROJECT_EVIDENCE_INCOMPLETE",
            message: "Project cannot be completed until its required evidence is complete.",
            policy_id: "software-delivery",
            policy_version: "1.0",
            evidence_status: "incomplete",
            failed_check_names: ["flow_terminal", "required_artifacts"],
            failed_check_count: 2,
            failed_check_names_truncated: false,
          },
        }, 409);
      }
      projectState = "COMPLETED";
      return fulfill(route, { prior_state: "KPI_PERSISTENCE", event: "kpi_saved", next_state: "COMPLETED" });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/workspace` && method === "GET") {
      return fulfill(route, {
        next_actions: [],
        pending_approvals: decisions,
        recent_activity: [],
        worker_activity: [],
        artifacts: runStates[REPORT_RUN_ID] === "SUCCEEDED"
          ? [{
              id: 42,
              path: "reports/research-brief.md",
              agent_id: WORKER_ID,
              size_bytes: 24,
              sha256: "a".repeat(64),
              metadata: {
                project_id: PROJECT_ID,
                worker_run_id: REPORT_RUN_ID,
                blob_bucket: "project-artifacts",
                blob_key: `${PROJECT_ID}/worker-runs/${REPORT_RUN_ID}/reports/research-brief.md`,
                blob_sha256: "a".repeat(64),
                blob_size_bytes: 24,
              },
            }]
          : [],
        logs: [],
        cost_usage: { available: false, reason: "This isolated UI fixture has no billing data." },
      });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/repository` && method === "GET") {
      return fulfill(route, { workspace: null });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/issues` && method === "GET") return fulfill(route, []);
    if (pathname === `/api/governance/runs` && method === "GET") {
      return fulfill(route, createdRunIds.map((runId) => ({
            id: runId,
            project_id: PROJECT_ID,
            worker_id: WORKER_ID,
            task_type: runId === RESEARCH_RUN_ID ? "research.collect" : "document.write",
            state: runStates[runId],
            model_resolution_snapshot_id: "50000000-0000-4000-8000-000000000007",
            created_at: runId === RESEARCH_RUN_ID ? "2026-10-25T12:01:00Z" : "2026-10-25T12:03:00Z",
          })));
    }
    if (pathname === `/api/projects/${PROJECT_ID}/flow-instance` && method === "GET") return fulfill(route, flowInstance);
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/executions` && method === "GET") return fulfill(route, executions);
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/action` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      if (body.action !== "start" || !flowNodeIds.start || savedFlow?.is_active !== true) {
        return fulfill(route, { error: "Unsupported fixture action." }, 409);
      }
      flowInstance = { ...flowInstance, status: "RUNNING", active_node_ids: [flowNodeIds.start] };
      executions = [{ id: "1", instance_id: INSTANCE_ID, node_id: flowNodeIds.start, node_type: "start", node_label: "Start", status: "RUNNING", started_at: "2026-10-25T12:01:00Z" }];
      return fulfill(route, flowInstance);
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/node-action` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      dispatchRequests.push(body);
      if (body.node_id === flowNodeIds.start && body.action === "complete") {
        const task = savedNode(flowNodeIds.research);
        if (
          !task ||
          task.config.worker_id !== WORKER_ID ||
          task.config.model_profile_id !== "approved-research-profile"
        ) {
          return fulfill(route, { error: "Research task binding is unavailable." }, 409);
        }
        runStates[RESEARCH_RUN_ID] = "QUEUED";
        createdRunIds.push(RESEARCH_RUN_ID);
        flowInstance = {
          ...flowInstance,
          status: "RUNNING",
          active_node_ids: [flowNodeIds.research],
          context_json: {
            active_worker_runs: {
              [flowNodeIds.research]: { run_id: RESEARCH_RUN_ID, state: "QUEUED", dispatch_mode: "queued" },
            },
          },
        };
        executions = [
          { id: "1", instance_id: INSTANCE_ID, node_id: flowNodeIds.start, node_type: "start", node_label: "Start", status: "COMPLETED", started_at: "2026-10-25T12:01:00Z", completed_at: "2026-10-25T12:01:01Z" },
          { id: "2", instance_id: INSTANCE_ID, node_id: flowNodeIds.research, node_type: "task", node_label: "Research", status: "RUNNING", started_at: "2026-10-25T12:01:01Z" },
        ];
        return fulfill(route, flowInstance);
      }
      if (body.node_id === flowNodeIds.research && body.action === "complete") {
        settlementRequests.push(body);
        if (body.worker_run_id !== RESEARCH_RUN_ID || runStates[RESEARCH_RUN_ID] !== "SUCCEEDED") {
          return fulfill(route, { error: "Worker Run result is not ready for settlement." }, 409);
        }
        flowInstance = {
          ...flowInstance,
          status: "RUNNING",
          active_node_ids: [flowNodeIds.review],
          context_json: { research_summary: "Canonical research findings", worker_run_id: RESEARCH_RUN_ID },
        };
        executions = [
          { id: "1", instance_id: INSTANCE_ID, node_id: flowNodeIds.start, node_type: "start", node_label: "Start", status: "COMPLETED", started_at: "2026-10-25T12:01:00Z", completed_at: "2026-10-25T12:01:01Z" },
          { id: "2", instance_id: INSTANCE_ID, node_id: flowNodeIds.research, node_type: "task", node_label: "Research", status: "COMPLETED", output_json: { research_summary: "Canonical research findings", worker_run_id: RESEARCH_RUN_ID }, started_at: "2026-10-25T12:01:01Z", completed_at: "2026-10-25T12:02:00Z" },
          { id: "3", instance_id: INSTANCE_ID, node_id: flowNodeIds.review, node_type: "approval", node_label: "Review findings", status: "RUNNING", started_at: "2026-10-25T12:02:00Z" },
        ];
        decisions = [{ id: APPROVAL_ID, project_id: PROJECT_ID, flow_node_execution_id: "3", status: "PENDING", gate_type: "cio", prompt: "Review the research findings." }];
        return fulfill(route, flowInstance);
      }
      if (body.node_id === flowNodeIds.report && body.action === "complete") {
        settlementRequests.push(body);
        if (body.worker_run_id !== REPORT_RUN_ID || runStates[REPORT_RUN_ID] !== "SUCCEEDED") {
          return fulfill(route, { error: "Worker Run result is not ready for settlement." }, 409);
        }
        flowInstance = {
          ...flowInstance,
          status: "COMPLETED",
          active_node_ids: [],
          context_json: {
            research_summary: "Canonical research findings",
            report: "Final evidence-backed research report",
            worker_run_id: REPORT_RUN_ID,
            approval: "approved",
          },
        };
        executions = [
          ...executions.filter((execution) => execution.node_id !== flowNodeIds.report && execution.node_type !== "end"),
          { id: "4", instance_id: INSTANCE_ID, node_id: flowNodeIds.report, node_type: "task", node_label: "Write report", status: "COMPLETED", output_json: { report: "Final evidence-backed research report", worker_run_id: REPORT_RUN_ID }, started_at: "2026-10-25T12:03:00Z", completed_at: "2026-10-25T12:04:00Z" },
          { id: "5", instance_id: INSTANCE_ID, node_id: "end", node_type: "end", node_label: "Complete", status: "COMPLETED", started_at: "2026-10-25T12:04:00Z", completed_at: "2026-10-25T12:04:00Z" },
        ];
        return fulfill(route, flowInstance);
      }
      return fulfill(route, { error: "Unexpected node action in fixture." }, 409);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/decisions` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      decisionRequests.push(body);
      if (body.gate_id !== APPROVAL_ID || body.decision !== "APPROVED") {
        return fulfill(route, { error: "Approval must target the active research review gate." }, 409);
      }
      const task = savedNode(flowNodeIds.report);
      if (
        !task ||
        task.config.worker_id !== WORKER_ID ||
        task.config.model_profile_id !== "approved-research-profile"
      ) {
        return fulfill(route, { error: "Report task binding is unavailable." }, 409);
      }
      decisions = [];
      approvalRecorded = true;
      runStates[REPORT_RUN_ID] = "QUEUED";
      createdRunIds.push(REPORT_RUN_ID);
      flowInstance = {
        ...flowInstance,
        status: "RUNNING",
        active_node_ids: [flowNodeIds.report],
        context_json: {
          research_summary: "Canonical research findings",
          active_worker_runs: {
            [flowNodeIds.report]: { run_id: REPORT_RUN_ID, state: "QUEUED", dispatch_mode: "queued" },
          },
        },
      };
      executions = [
        ...executions.filter((execution) => execution.node_id !== flowNodeIds.review),
        { id: "3", instance_id: INSTANCE_ID, node_id: flowNodeIds.review, node_type: "approval", node_label: "Review findings", status: "COMPLETED", output_json: { decision: "APPROVED" }, started_at: "2026-10-25T12:02:00Z", completed_at: "2026-10-25T12:03:00Z" },
        { id: "4", instance_id: INSTANCE_ID, node_id: flowNodeIds.report, node_type: "task", node_label: "Write report", status: "RUNNING", started_at: "2026-10-25T12:03:00Z" },
      ];
      return fulfill(route, { status: "decision_recorded", gate_id: APPROVAL_ID });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/evidence` && method === "GET") {
      return fulfill(route, {
        project_id: PROJECT_ID,
        policy_id: "software-delivery",
        policy_version: "1.0",
        status: projectEvidenceReady() ? "complete" : "incomplete",
        completeness_score: projectEvidenceReady() ? 1 : 0,
        checks: [
          { name: "worker_runs_terminal", required: true, passed: projectEvidenceReady(), evidence_refs: projectEvidenceReady() ? [RESEARCH_RUN_ID, REPORT_RUN_ID] : [] },
          { name: "flow_terminal", required: true, passed: flowInstance.status === "COMPLETED", evidence_refs: flowInstance.status === "COMPLETED" ? [INSTANCE_ID] : [] },
          { name: "required_artifacts", required: true, passed: runStates[REPORT_RUN_ID] === "SUCCEEDED", evidence_refs: runStates[REPORT_RUN_ID] === "SUCCEEDED" ? ["42"] : [] },
        ],
      });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/evidence/package` && method === "GET") {
      return fulfill(route, evidencePackage(projectEvidenceReady()));
    }

    unhandledRequests.push(`${method} ${pathname}`);
    return fulfill(route, { error: `No fixture for ${method} ${pathname}` }, 404);
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("flow-name-input").fill("Bound Research Brief");
  await page.getByTestId("template-research").click();
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Research");
  await expect(page.getByTestId("research-template-binding-checklist")).toContainText("Write report");
  await page.getByLabel("Activate flow on save").check();
  await page.getByTestId("flow-dry-run-button").click();
  await expect(page.getByText(/Bind a governed worker to each Research Brief task/)).toBeVisible();
  expect(dryRunRequests).toHaveLength(0);

  await page.getByRole("button", { name: "Configure Research assignment" }).click();
  await page.getByTestId("task-worker-select").first().selectOption(WORKER_ID);
  await page.getByTestId("task-model-profile-select").first().selectOption("approved-research-profile");
  await page.getByRole("button", { name: "Configure Write report assignment" }).click();
  await page.getByTestId("task-worker-select").first().selectOption(WORKER_ID);
  await page.getByTestId("task-model-profile-select").first().selectOption("approved-research-profile");
  await expect(page.getByTestId("research-template-bindings-complete")).toBeVisible();
  await page.getByTestId("flow-dry-run-button").click();
  await expect(page.getByTestId("flow-dry-run-result")).toBeVisible();
  expect(dryRunRequests).toHaveLength(1);
  const flowResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/flows") && response.request().method() === "POST",
  );
  await page.getByTestId("flow-save-button").click();
  expect((await flowResponse).status()).toBe(201);
  expect(flowCreateRequests).toHaveLength(1);
  const createdFlowPayload = flowCreateRequests[0];
  expect(createdFlowPayload.is_active).toBe(true);
  expect(dryRunRequests[0]).toEqual(
    expect.objectContaining({ definition_json: createdFlowPayload.definition_json }),
  );
  const savedDefinition = createdFlowPayload.definition_json as {
    nodes: Array<{ id: string; type: string; label: string; config: Record<string, unknown> }>;
    edges: Array<{ source: string; target: string }>;
  };
  const researchTask = savedDefinition.nodes.find((node) => node.config.action === "research.collect");
  const reportTask = savedDefinition.nodes.find((node) => node.config.action === "document.write");
  expect(researchTask?.config).toEqual(expect.objectContaining({
    action: "research.collect",
    worker_id: WORKER_ID,
    model_profile_id: "approved-research-profile",
  }));
  expect(reportTask?.config).toEqual(expect.objectContaining({
    action: "document.write",
    worker_id: WORKER_ID,
    model_profile_id: "approved-research-profile",
  }));
  expect(savedDefinition.nodes.find((node) => node.type === "approval")?.label).toBe("Review findings");

  await page.goto("/projects");
  await page.getByRole("button", { name: "New Project" }).first().click();
  await page.getByPlaceholder("my-project").fill("Research Brief integration project");
  await page.getByRole("checkbox", { name: "Start CEO feasibility after creation" }).check();
  await page.locator("select").last().selectOption(FLOW_ID);
  const projectResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/projects") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Create", exact: true }).click();
  expect((await projectResponse).status()).toBe(201);
  expect(projectCreateRequests).toHaveLength(1);
  expect(projectCreateRequests[0]).toEqual(expect.objectContaining({
    name: "Research Brief integration project",
    flow_id: FLOW_ID,
    start_immediately: true,
  }));
  await page.getByRole("link", { name: "Open Research Brief integration project" }).click();
  await expect(page.getByRole("heading", { name: "Research Brief integration project" })).toBeVisible();

  await page.getByTestId("project-tab-workflow").click();
  await page.getByRole("button", { name: "kpi saved", exact: true }).click();
  await expect(page.getByTestId("project-evidence-incomplete-detail")).toBeVisible();
  await expect(page.getByText("flow terminal", { exact: true })).toBeVisible();

  await page.getByTestId("project-tab-flow").click();
  await expect(page.getByTestId("flow-start-button")).toBeVisible();
  await expect(page.getByText("NOT_STARTED", { exact: true }).first()).toBeVisible();
  await page.getByTestId("flow-start-button").click();
  await expect(page.getByTestId("current-node-label")).toHaveText("Start");
  await page.getByTestId("complete-node-button").click();

  await expect(page.getByTestId(`active-worker-run-${flowNodeIds.research}`)).toBeVisible();
  await expect(page.getByRole("status", { name: "Worker Run state: QUEUED" })).toBeVisible();
  await expect(page.getByTestId("dispatch-worker-run-button")).toHaveCount(0);

  runStates[RESEARCH_RUN_ID] = "SUCCEEDED";
  await page.getByRole("button", { name: "Refresh run status" }).click();
  await expect(page.getByRole("status", { name: "Worker Run state: SUCCEEDED" })).toBeVisible();
  await page.getByTestId(`apply-worker-run-${flowNodeIds.research}`).click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  await expect(page.getByTestId(`approval-worker-results-${flowNodeIds.review}`)).toBeVisible();
  await expect(page.getByTestId(`approval-worker-run-${RESEARCH_RUN_ID}`)).toHaveAttribute(
    "href",
    `/workers/runs/${RESEARCH_RUN_ID}`,
  );
  expect(settlementRequests[0]).toEqual({ node_id: flowNodeIds.research, action: "complete", worker_run_id: RESEARCH_RUN_ID });
  expect(settlementRequests[0].output).toBeUndefined();

  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByTestId(`active-worker-run-${flowNodeIds.report}`)).toBeVisible();
  await expect(page.getByRole("status", { name: "Worker Run state: QUEUED" })).toBeVisible();
  expect(decisionRequests).toHaveLength(1);
  expect(decisionRequests[0]).toEqual(expect.objectContaining({ gate_id: APPROVAL_ID, decision: "APPROVED" }));

  runStates[REPORT_RUN_ID] = "SUCCEEDED";
  await page.getByRole("button", { name: "Refresh run status" }).click();
  await expect(page.getByRole("status", { name: "Worker Run state: SUCCEEDED" })).toBeVisible();
  await page.getByTestId(`apply-worker-run-${flowNodeIds.report}`).click();
  await expect(page.getByText("COMPLETED", { exact: true }).first()).toBeVisible();
  expect(settlementRequests[1]).toEqual({ node_id: flowNodeIds.report, action: "complete", worker_run_id: REPORT_RUN_ID });
  expect(settlementRequests[1].output).toBeUndefined();

  await page.getByTestId("project-tab-workflow").click();
  await page.getByRole("button", { name: "kpi saved", exact: true }).click();
  await expect(page.getByText("COMPLETED", { exact: true }).first()).toBeVisible();
  expect(projectState).toBe("COMPLETED");

  await page.getByTestId("project-tab-evidence").click();
  await expect(page.getByRole("heading", { name: "Completion Evidence" })).toBeVisible();
  await expect(page.getByText("100% complete")).toBeVisible();
  await page.getByRole("link", { name: "Open project evidence details" }).click();
  await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT_ID}/evidence$`));
  await expect(page.getByRole("link", { name: `Open artifact evidence 42` })).toBeVisible();
  await expect(page.getByRole("link", { name: `Open WorkerRun evidence ${RESEARCH_RUN_ID}` })).toBeVisible();
  await expect(page.getByRole("link", { name: `Open WorkerRun evidence ${REPORT_RUN_ID}` })).toBeVisible();

  // The flow review supplies evidence; only the explicit lifecycle transition
  // completes the project after its evidence gate passes.
  expect(dispatchRequests).toEqual([
    { node_id: flowNodeIds.start, action: "complete" },
    { node_id: flowNodeIds.research, action: "complete", worker_run_id: RESEARCH_RUN_ID },
    { node_id: flowNodeIds.report, action: "complete", worker_run_id: REPORT_RUN_ID },
  ]);
  expect(unhandledRequests).toEqual([]);
});
