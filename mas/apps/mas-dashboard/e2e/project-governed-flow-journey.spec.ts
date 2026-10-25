import { expect, test, type Route } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "50000000-0000-4000-8000-000000000001";
const FLOW_ID = "50000000-0000-4000-8000-000000000002";
const INSTANCE_ID = "50000000-0000-4000-8000-000000000003";
const WORKER_ID = "50000000-0000-4000-8000-000000000004";
const WORKER_RUN_ID = "50000000-0000-4000-8000-000000000005";
const APPROVAL_ID = "50000000-0000-4000-8000-000000000006";

const FLOW_DEFINITION = {
  schema_version: "aiat.flow-node-schemas.v1.0",
  nodes: [
    { id: "start", type: "start", label: "Start", config: {} },
    {
      id: "research",
      type: "task",
      label: "Research Brief",
      config: {
        worker_id: WORKER_ID,
        task_type: "research_brief",
        model_mode: "required",
        model_profile_id: "approved-research-profile",
      },
    },
    {
      id: "review",
      type: "approval",
      label: "Research review",
      config: { approver_role: "human_operator" },
    },
    { id: "end", type: "end", label: "Complete", config: {} },
  ],
  edges: [
    { id: "start-research", source: "start", target: "research" },
    { id: "research-review", source: "research", target: "review" },
    { id: "review-end", source: "review", target: "end" },
  ],
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
      { category: "worker", status: "present", required: true, item_count: 1, evidence_refs: [WORKER_RUN_ID] },
      { category: "tests", status: "present", required: true, item_count: 1, evidence_refs: ["42"] },
    ],
    items: [
      { id: WORKER_RUN_ID, category: "workers", kind: "worker_run", status: "SUCCEEDED", source: "worker_runs" },
      { id: "42", category: "tests", kind: "research-report", status: "stored", source: "artifacts", checksum: "sha256:fixture-report" },
    ],
    notices: [],
  };
}

test("Project Detail carries a governed research result through review and evidence", async ({ page }) => {
  let projectState = "KPI_PERSISTENCE";
  let runState = "QUEUED";
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
  let workerRunCreated = false;
  let decisions: Array<Record<string, unknown>> = [];
  let settlementRequest: Record<string, unknown> | null = null;
  const dispatchRequests: Array<Record<string, unknown>> = [];
  const decisionRequests: Array<Record<string, unknown>> = [];
  const unhandledRequests: string[] = [];

  const fulfill = (route: Route, body: unknown, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const { pathname } = url;
    const method = request.method();

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
      if (flowInstance.status !== "COMPLETED") {
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
        artifacts: runState === "SUCCEEDED"
          ? [{
              id: 42,
              path: "reports/research-brief.md",
              agent_id: WORKER_ID,
              size_bytes: 24,
              sha256: "a".repeat(64),
              metadata: {
                project_id: PROJECT_ID,
                worker_run_id: WORKER_RUN_ID,
                blob_bucket: "project-artifacts",
                blob_key: `${PROJECT_ID}/worker-runs/${WORKER_RUN_ID}/reports/research-brief.md`,
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
      return fulfill(route, workerRunCreated
        ? [{
            id: WORKER_RUN_ID,
            project_id: PROJECT_ID,
            worker_id: WORKER_ID,
            task_type: "research_brief",
            state: runState,
            model_resolution_snapshot_id: "50000000-0000-4000-8000-000000000007",
            created_at: "2026-10-25T12:01:00Z",
          }]
        : []);
    }
    if (pathname === `/api/projects/${PROJECT_ID}/flow-instance` && method === "GET") return fulfill(route, flowInstance);
    if (pathname === `/api/flows/${FLOW_ID}` && method === "GET") {
      return fulfill(route, { id: FLOW_ID, version: 1, definition_json: FLOW_DEFINITION });
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/executions` && method === "GET") return fulfill(route, executions);
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/action` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      if (body.action !== "start") return fulfill(route, { error: "Unsupported fixture action." }, 409);
      flowInstance = { ...flowInstance, status: "RUNNING", active_node_ids: ["start"] };
      executions = [{ id: "1", instance_id: INSTANCE_ID, node_id: "start", node_type: "start", node_label: "Start", status: "RUNNING", started_at: "2026-10-25T12:01:00Z" }];
      return fulfill(route, flowInstance);
    }
    if (pathname === `/api/flows/instances/${INSTANCE_ID}/node-action` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      dispatchRequests.push(body);
      if (body.node_id === "start" && body.action === "complete") {
        workerRunCreated = true;
        flowInstance = {
          ...flowInstance,
          status: "RUNNING",
          active_node_ids: ["research"],
          context_json: {
            active_worker_runs: {
              research: { run_id: WORKER_RUN_ID, state: "QUEUED", dispatch_mode: "queued" },
            },
          },
        };
        executions = [
          { id: "1", instance_id: INSTANCE_ID, node_id: "start", node_type: "start", node_label: "Start", status: "COMPLETED", started_at: "2026-10-25T12:01:00Z", completed_at: "2026-10-25T12:01:01Z" },
          { id: "2", instance_id: INSTANCE_ID, node_id: "research", node_type: "task", node_label: "Research Brief", status: "RUNNING", started_at: "2026-10-25T12:01:01Z" },
        ];
        return fulfill(route, flowInstance);
      }
      if (body.node_id === "research" && body.action === "complete") {
        settlementRequest = body;
        if (body.worker_run_id !== WORKER_RUN_ID || runState !== "SUCCEEDED") {
          return fulfill(route, { error: "Worker Run result is not ready for settlement." }, 409);
        }
        flowInstance = {
          ...flowInstance,
          status: "RUNNING",
          active_node_ids: ["review"],
          context_json: { report: "Canonical research report", worker_run_id: WORKER_RUN_ID },
        };
        executions = [
          { id: "1", instance_id: INSTANCE_ID, node_id: "start", node_type: "start", node_label: "Start", status: "COMPLETED", started_at: "2026-10-25T12:01:00Z", completed_at: "2026-10-25T12:01:01Z" },
          { id: "2", instance_id: INSTANCE_ID, node_id: "research", node_type: "task", node_label: "Research Brief", status: "COMPLETED", output_json: { report: "Canonical research report", worker_run_id: WORKER_RUN_ID }, started_at: "2026-10-25T12:01:01Z", completed_at: "2026-10-25T12:02:00Z" },
          { id: "3", instance_id: INSTANCE_ID, node_id: "review", node_type: "approval", node_label: "Research review", status: "RUNNING", started_at: "2026-10-25T12:02:00Z" },
        ];
        decisions = [{ id: APPROVAL_ID, project_id: PROJECT_ID, flow_node_execution_id: "3", status: "PENDING", gate_type: "human_operator", prompt: "Review the research report." }];
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
      decisions = [];
      flowInstance = {
        ...flowInstance,
        status: "COMPLETED",
        active_node_ids: [],
        context_json: { report: "Canonical research report", worker_run_id: WORKER_RUN_ID, approval: "approved" },
      };
      executions = [
        ...executions.filter((execution) => execution.node_id !== "review"),
        { id: "3", instance_id: INSTANCE_ID, node_id: "review", node_type: "approval", node_label: "Research review", status: "COMPLETED", output_json: { decision: "APPROVED" }, started_at: "2026-10-25T12:02:00Z", completed_at: "2026-10-25T12:03:00Z" },
        { id: "4", instance_id: INSTANCE_ID, node_id: "end", node_type: "end", node_label: "Complete", status: "COMPLETED", started_at: "2026-10-25T12:03:00Z", completed_at: "2026-10-25T12:03:00Z" },
      ];
      return fulfill(route, { status: "decision_recorded", gate_id: APPROVAL_ID });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/evidence` && method === "GET") {
      return fulfill(route, {
        project_id: PROJECT_ID,
        policy_id: "software-delivery",
        policy_version: "1.0",
        status: "complete",
        completeness_score: 1,
        checks: [
          { name: "worker_runs_terminal", required: true, passed: true, evidence_refs: [WORKER_RUN_ID] },
          { name: "flow_terminal", required: true, passed: true, evidence_refs: [INSTANCE_ID] },
          { name: "required_artifacts", required: true, passed: true, evidence_refs: ["42"] },
        ],
      });
    }
    if (pathname === `/api/projects/${PROJECT_ID}/evidence/package` && method === "GET") {
      return fulfill(route, evidencePackage(flowInstance.status === "COMPLETED"));
    }

    unhandledRequests.push(`${method} ${pathname}`);
    return fulfill(route, { error: `No fixture for ${method} ${pathname}` }, 404);
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await page.getByTestId("project-tab-workflow").click();
  await page.getByRole("button", { name: "kpi saved", exact: true }).click();
  await expect(page.getByTestId("project-evidence-incomplete-detail")).toBeVisible();
  await expect(page.getByText("flow terminal", { exact: true })).toBeVisible();

  await page.getByTestId("project-tab-flow").click();
  await page.getByTestId("flow-start-button").click();
  await expect(page.getByTestId("current-node-label")).toHaveText("Start");
  await page.getByTestId("complete-node-button").click();

  await expect(page.getByTestId("active-worker-run-research")).toBeVisible();
  await expect(page.getByRole("status", { name: "Worker Run state: QUEUED" })).toBeVisible();
  await expect(page.getByTestId("dispatch-worker-run-button")).toHaveCount(0);

  runState = "SUCCEEDED";
  await page.getByRole("button", { name: "Refresh run status" }).click();
  await expect(page.getByRole("status", { name: "Worker Run state: SUCCEEDED" })).toBeVisible();
  await page.getByTestId("apply-worker-run-research").click();
  await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeVisible();
  expect(settlementRequest).toEqual({ node_id: "research", action: "complete", worker_run_id: WORKER_RUN_ID });
  expect((settlementRequest as unknown as Record<string, unknown>).output).toBeUndefined();

  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText("COMPLETED", { exact: true }).first()).toBeVisible();
  expect(decisionRequests).toHaveLength(1);
  expect(decisionRequests[0]).toEqual(expect.objectContaining({ gate_id: APPROVAL_ID, decision: "APPROVED" }));

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
  await expect(page.getByRole("link", { name: `Open WorkerRun evidence ${WORKER_RUN_ID}` })).toBeVisible();

  // The flow review supplies evidence; only the explicit lifecycle transition
  // completes the project after its evidence gate passes.
  expect(dispatchRequests).toEqual([
    { node_id: "start", action: "complete" },
    { node_id: "research", action: "complete", worker_run_id: WORKER_RUN_ID },
  ]);
  expect(unhandledRequests).toEqual([]);
});
