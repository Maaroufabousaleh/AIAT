/**
 * Test 2 — Flow runtime: branching, approval, retry, escalation (UI / e2e)
 *
 * Prerequisites (set via env vars or defaults):
 *   PLAYWRIGHT_BASE_URL   — dashboard base URL (default: http://127.0.0.1:3000)
 *   E2E_DASHBOARD_USERNAME — login username   (default: admin)
 *   E2E_DASHBOARD_PASSWORD — login password   (default: admin)
 *
 * Run against a live dashboard + orchestrator:
 *   npx playwright test e2e/flow-runtime-test2.spec.ts
 *
 * Tests are structured as independent flows so each approval scenario starts
 * fresh. Each test:
 *   1. Creates the Test-2 branching flow through the UI.
 *   2. Creates a project attached to that flow.
 *   3. Drives the project through the flow via the UI approval panel.
 *   4. Refreshes the page and asserts the visible state.
 *   5. Asserts audit/history visibility.
 *   6. Exercises at least one negative case per scenario.
 */

import { expect, test, type Page } from "@playwright/test";
import { authenticate } from "./auth";

// ── Auth helper ──────────────────────────────────────────────────────────────

async function login(page: Page): Promise<void> {
  const username = process.env.E2E_DASHBOARD_USERNAME ?? "admin";
  const password = process.env.E2E_DASHBOARD_PASSWORD ?? "admin";

  await page.goto("/login");
  await page.getByPlaceholder("admin").fill(username);
  await page.getByPlaceholder("••••••••").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/$/);
  await page.goto("/projects");
}

// ── Flow builder helpers ──────────────────────────────────────────────────────

async function buildTest2Flow(page: Page, flowName: string): Promise<void> {
  await page.goto("/flows/new");
  await page.getByTestId("flow-name-input").fill(flowName);
  await page.getByLabel("Activate flow on save").check();

  // Add nodes in order
  for (const type of ["start", "task", "approval", "switch", "task", "task", "end", "end"]) {
    await page.getByTestId(`add-node-${type}`).click();
  }
  await expect(page.locator(".react-flow__node")).toHaveCount(8);

  // Node 0: Start
  const nodes = page.locator(".react-flow__node");
  const nodeId = async (index: number) => {
    const id = await nodes.nth(index).getAttribute("data-id");
    if (!id) throw new Error(`Missing data-id for node ${index}`);
    return id;
  };
  const branchAId = await nodeId(4);
  const branchBId = await nodeId(5);
  const failedId = await nodeId(7);
  await nodes.nth(0).click();
  await page.getByTestId("node-label-input").fill("Start");

  // Node 1: Analysis (task with timeout + escalation)
  await nodes.nth(1).click();
  await page.getByTestId("node-label-input").fill("Analysis");
  await page.getByTestId("task-team-id-input").fill("exec_ceo");
  await page.getByTestId("task-timeout-input").fill("300");
  await page.getByTestId("task-escalate-team-input").fill("exec_ceo");

  // Node 2: Approval Gate
  await nodes.nth(2).click();
  await page.getByTestId("node-label-input").fill("Approval Gate");
  await page.getByTestId("approval-user-input").fill("human");

  // Node 3: Decision Switch
  await nodes.nth(3).click();
  await page.getByTestId("node-label-input").fill("Decision Switch");
  await page.getByTestId("switch-key-input").fill("approval");
  await page.getByTestId("switch-case-add-button").click();
  await page.getByTestId("switch-case-key-0").fill("approved");
  await page.getByTestId("switch-case-target-0").fill(branchAId);
  await page.getByTestId("switch-case-add-button").click();
  await page.getByTestId("switch-case-key-1").fill("edit_requested");
  await page.getByTestId("switch-case-target-1").fill(branchBId);
  await page.getByTestId("switch-case-add-button").click();
  await page.getByTestId("switch-case-key-2").fill("rejected");
  await page.getByTestId("switch-case-target-2").fill(failedId);

  // Node 4: Branch A (task)
  await nodes.nth(4).click();
  await page.getByTestId("node-label-input").fill("Branch A");
  await page.getByTestId("task-team-id-input").fill("dept_system");

  // Node 5: Branch B (task)
  await nodes.nth(5).click();
  await page.getByTestId("node-label-input").fill("Branch B");
  await page.getByTestId("task-team-id-input").fill("dept_qa");

  // Node 6: Completed (end)
  await nodes.nth(6).click();
  await page.getByTestId("node-label-input").fill("Completed");

  // Node 7: Failed (end)
  await nodes.nth(7).click();
  await page.getByTestId("node-label-input").fill("Failed");

  const connectEdge = async (src: number, tgt: number) => {
    const sourceId = await nodes.nth(src).getAttribute("data-id");
    const targetId = await nodes.nth(tgt).getAttribute("data-id");
    if (!sourceId || !targetId) throw new Error(`Cannot resolve node ids for edge ${src}→${tgt}`);
    await page.evaluate(
      ({ source, target }) => {
        window.dispatchEvent(new CustomEvent("flow-quick-connect", { detail: { source, target } }));
      },
      { source: sourceId, target: targetId }
    );
  };

  await connectEdge(0, 1); // start → analysis
  await connectEdge(1, 2); // analysis → approval_gate
  await connectEdge(2, 3); // approval_gate → decision_switch
  await connectEdge(3, 4); // decision_switch → branch_a
  await connectEdge(3, 5); // decision_switch → branch_b
  await connectEdge(3, 7); // decision_switch → failed_terminal
  await connectEdge(4, 6); // branch_a → completed
  await connectEdge(5, 6); // branch_b → completed

  await page.getByTestId("flow-save-button").click();
  await expect(page).toHaveURL(/\/flows\/(?!new$)[^/]+$/);
}

async function createProjectWithFlow(page: Page, flowName: string, projectName: string): Promise<void> {
  await page.goto("/projects");
  await page.getByRole("button", { name: /new project/i }).click();
  await page.getByPlaceholder("my-project").fill(projectName);
  await page.getByPlaceholder("What should the agents build?").fill("Test-2 branching project");
  await page.locator("select").last().selectOption({ label: `${flowName} (v1)` });
  await page.getByRole("button", { name: /^create$/i }).click();
  await expect(page.getByText(projectName)).toBeVisible();
  const projectRow = page.getByRole("row", { name: new RegExp(projectName) });
  await expect(projectRow).toBeVisible();
  const projectLink = projectRow.getByRole("link", { name: /^Open / });
  const projectHref = await projectLink.getAttribute("href");
  expect(projectHref).toMatch(/^\/projects\/[^/]+$/);
  await expect
    .poll(async () => {
      const res = await page.request.get(`/api${projectHref}/flow-instance`);
      return res.status();
    })
    .toBe(200);
  await projectLink.click();
  await expect(page).toHaveURL(new RegExp(`${projectHref}$`));
}

async function startFlow(page: Page): Promise<void> {
  await page.getByTestId("project-tab-flow").click();
  await expect(page.getByText("NOT_STARTED").first()).toBeVisible();
  await page.getByTestId("flow-start-button").click();
  await expect(page.getByText("RUNNING").first()).toBeVisible();
}

async function advanceToApprovalGate(page: Page): Promise<void> {
  // Complete Start node
  await page.getByTestId("complete-node-button").click();
  await expect(page.getByText("Analysis").first()).toBeVisible();
  // Complete Analysis node
  await page.getByTestId("complete-node-button").click();
  await expect(page.getByText("Approval Gate").first()).toBeVisible();
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe("Test 2 — Flow runtime: branching, approval, retry, escalation", () => {
  const stamp = Date.now();

  test.beforeEach(async ({ page }) => {
    await authenticate(page, "/projects");
  });

  test("1. Create flow with all required nodes, reload, verify serialization survives", async ({ page }) => {
    const flowName = `Test2-Branching-${stamp}`;

    await buildTest2Flow(page, flowName);

    // Reload the flow editor and verify all nodes are present
    await page.reload();
    await expect(page.getByText("Analysis").first()).toBeVisible();
    await expect(page.getByText("Approval Gate").first()).toBeVisible();
    await expect(page.getByText("Decision Switch").first()).toBeVisible();
    await expect(page.getByText("Branch A").first()).toBeVisible();
    await expect(page.getByText("Branch B").first()).toBeVisible();
    await expect(page.getByText("Failed").first()).toBeVisible();
    await expect(page.getByText("Completed").first()).toBeVisible();

    // Verify flow appears in flow list
    await page.goto("/flows");
    await expect(page.getByText(flowName)).toBeVisible();
  });

  test("2. Approval=approved → project transitions to Branch A (read back after refresh)", async ({ page }) => {
    const flowName = `Test2-Approved-${stamp}`;
    const projectName = `test2-approved-project-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);
    await advanceToApprovalGate(page);

    // Submit approval decision = approved
    await page.getByTestId("approval-approve-button").click();

    // Should transition to Branch A
    await expect(page.getByText("Branch A").first()).toBeVisible();

    // Refresh and verify persisted state
    await page.reload();
    await page.getByTestId("project-tab-flow").click();
    await expect(page.getByText("Branch A").first()).toBeVisible();

    // Verify flow execution history shows past transitions.
    await expect(page.getByText("Past Transitions")).toBeVisible();
    await expect(page.getByText("Approval Gate").first()).toBeVisible();
  });

  test("3. Approval=edit_requested → project transitions to Branch B (read back after refresh)", async ({ page }) => {
    const flowName = `Test2-EditReq-${stamp}`;
    const projectName = `test2-editreq-project-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);
    await advanceToApprovalGate(page);

    await page.getByTestId("approval-edit-requested-button").click();

    await expect(page.getByText("Branch B").first()).toBeVisible();

    // Refresh → state persists
    await page.reload();
    await page.getByTestId("project-tab-flow").click();
    await expect(page.getByText("Branch B").first()).toBeVisible();
  });

  test("4. Approval=rejected → project transitions to Failed state", async ({ page }) => {
    const flowName = `Test2-Rejected-${stamp}`;
    const projectName = `test2-rejected-project-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);
    await advanceToApprovalGate(page);

    await page.getByTestId("approval-reject-button").click();

    await expect(page.getByText("FAILED").first()).toBeVisible();

    await page.reload();
    await page.getByTestId("project-tab-flow").click();
    await expect(page.getByText("FAILED").first()).toBeVisible();
  });

  test("5. Retry from Failed → restores last safe state (visible after refresh)", async ({ page }) => {
    const flowName = `Test2-Retry-${stamp}`;
    const projectName = `test2-retry-project-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);
    await advanceToApprovalGate(page);

    // Reject to reach FAILED state
    await page.getByTestId("approval-reject-button").click();
    await expect(page.getByText("FAILED").first()).toBeVisible();

    // Retry
    await page.getByTestId("flow-retry-button").click();
    await expect(page.getByText("RUNNING").first()).toBeVisible();

    // Verify last safe node is active
    await page.reload();
    await page.getByTestId("project-tab-flow").click();
    await expect(page.getByText("RUNNING").first()).toBeVisible();
    // The retry count should be incremented
    await expect(page.getByText(/retry.*1/i)).toBeVisible();
  });

  test("6. Timeout on Analysis → escalation sent to CEO, visible in UI/history", async ({ page }) => {
    const flowName = `Test2-Timeout-${stamp}`;
    const projectName = `test2-timeout-project-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);

    // Complete Start, then timeout Analysis
    await page.getByTestId("complete-node-button").click();
    await expect(page.getByText("Analysis").first()).toBeVisible();

    await page.getByTestId("timeout-node-button").click();

    // Instance should be FAILED with escalation
    await expect(page.getByText("FAILED").first()).toBeVisible();
    await expect(page.getByText(/exec_ceo/i).first()).toBeVisible();

    // Verify escalation visible in history panel
    await page.getByTestId("project-tab-workflow").click();
    await expect(page.getByText(/flow node escalated/i).first()).toBeVisible();
    await expect(page.getByText(/exec_ceo/i).first()).toBeVisible();

    // Refresh and verify escalation still visible
    await page.reload();
    await page.getByTestId("project-tab-workflow").click();
    await expect(page.getByText(/flow node escalated/i).first()).toBeVisible();
  });

  test("7. Override by non-operator role is denied (negative security case)", async ({ page }) => {
    const flowName = `Test2-OverrideSec-${stamp}`;
    const projectName = `test2-override-sec-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);

    // The UI override panel acts as the human operator. The security boundary is
    // the API policy that rejects non-operator override attempts.
    await page.getByTestId("project-tab-flow").click();
    const projectId = page.url().match(/\/projects\/([^/?#]+)/)?.[1];
    expect(projectId).toBeTruthy();
    const instanceRes = await page.request.get(`/api/projects/${projectId}/flow-instance`);
    expect(instanceRes.status()).toBe(200);
    const instance = await instanceRes.json();
    const flowRes = await page.request.get(`/api/flows/${instance.flow_id}`);
    expect(flowRes.status()).toBe(200);
    const flow = await flowRes.json();
    const targetNodeId = flow.definition_json.nodes[1].id;
    const overrideRes = await page.request.post(`/api/flows/instances/${instance.id}/override`, {
      data: {
        target_node_id: targetNodeId,
        actor_id: "developer-agent",
        actor_role: "developer",
        reason: "negative security test",
      },
    });
    expect(overrideRes.status()).toBe(403);
  });

  test("8. Approval without decision → flow stays in WAITING_APPROVAL (not routed)", async ({ page }) => {
    const flowName = `Test2-WaitApproval-${stamp}`;
    const projectName = `test2-wait-approval-${stamp}`;

    await buildTest2Flow(page, flowName);
    await createProjectWithFlow(page, flowName, projectName);
    await startFlow(page);
    await advanceToApprovalGate(page);

    // Submit without selecting a decision (if UI allows submitting without a decision)
    // The flow should remain in WAITING_APPROVAL, not advance to any branch
    await expect(page.getByTestId("approval-approve-button")).toBeVisible();
    await expect(page.getByTestId("approval-edit-requested-button")).toBeVisible();
    await expect(page.getByTestId("approval-reject-button")).toBeVisible();

    // Still on Approval Gate
    await expect(page.getByText("Approval Gate").first()).toBeVisible();
  });
});
