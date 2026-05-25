import { expect, test, type Page } from "@playwright/test";
import { authenticate } from "./auth";

async function login(page: Page) {
  const username = process.env.E2E_DASHBOARD_USERNAME ?? "admin";
  const password = process.env.E2E_DASHBOARD_PASSWORD ?? "admin";

  await page.goto("/login");
  await page.getByPlaceholder("admin").fill(username);
  await page.getByPlaceholder("••••••••").fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/$/);
  await page.goto("/projects");
}

async function openNodeConfig(page: Page, index: number) {
  const node = page.locator(".react-flow__node").nth(index);
  await expect(node).toBeVisible();
  await node.click();
  await expect(page.getByTestId("node-label-input")).toBeVisible();
}

async function renameNode(page: Page, label: string) {
  await page.getByTestId("node-label-input").fill(label);
}

async function connectNodes(page: Page, sourceIndex: number, targetIndex: number) {
  const sourceId = await page.locator(".react-flow__node").nth(sourceIndex).getAttribute("data-id");
  const targetId = await page.locator(".react-flow__node").nth(targetIndex).getAttribute("data-id");
  if (!sourceId || !targetId) {
    throw new Error("Unable to resolve flow node ids");
  }
  await page.evaluate(
    ({ source, target }) => {
      window.dispatchEvent(new CustomEvent("flow-quick-connect", { detail: { source, target } }));
    },
    { source: sourceId, target: targetId }
  );
}

async function createProjectWithFlow(page: Page, flowName: string, projectName: string) {
  await page.goto("/projects");
  await page.getByRole("button", { name: /new project/i }).click();
  await page.getByPlaceholder("my-project").fill(projectName);
  await page.getByPlaceholder("What should the agents build\?").fill("Operator validation project");
  await page.locator("select").last().selectOption({ label: `${flowName} (v2)` });
  await page.getByRole("button", { name: /^create$/i }).click();
  await expect(page.getByText(projectName)).toBeVisible();
  const row = page.getByRole("row", { name: new RegExp(projectName) });
  const href = await row.getByRole("link", { name: /view/i }).getAttribute("href");
  if (!href) throw new Error("Created project row is missing its detail link");
  await expect.poll(async () => {
    const response = await page.request.get(`/api${href}/flow-instance`);
    return response.status();
  }).toBe(200);
  await row.getByRole("link", { name: /view/i }).click();
}

test("operator can build, version, assign, refresh, and override a flow from the UI", async ({
  page,
}) => {
  const stamp = Date.now();
  const flowName = `Simple Product Build Flow ${stamp}`;
  const projectName = `flow-ui-project-${stamp}`;

  await authenticate(page, "/projects");
  await page.goto("/flows/new");

  await page.getByTestId("flow-name-input").fill(flowName);
  await page.getByLabel("Active").check();
  for (const type of ["start", "task", "task", "approval", "task", "end"]) {
    await page.getByTestId(`add-node-${type}`).click();
  }

  await expect(page.locator(".react-flow__node")).toHaveCount(6);

  await openNodeConfig(page, 0);
  await renameNode(page, "Intake");

  await openNodeConfig(page, 1);
  await renameNode(page, "Feasibility Review");
  await page.getByTestId("task-team-id-input").fill("office_cfo+office_cio+office_chrm+office_cso");
  await page.getByTestId("task-timeout-input").fill("900");
  await page.getByTestId("task-escalate-team-input").fill("exec_ceo");

  await openNodeConfig(page, 2);
  await renameNode(page, "PDR Creation");
  await page.getByTestId("task-team-id-input").fill("exec_coo+dept_production");
  await page.getByTestId("task-retries-input").fill("2");

  await openNodeConfig(page, 3);
  await renameNode(page, "Human Approval");
  await page.getByTestId("approval-user-input").fill("human");

  await openNodeConfig(page, 4);
  await renameNode(page, "Implementation");
  await page.getByTestId("task-team-id-input").fill("office_cto");

  await openNodeConfig(page, 5);
  await renameNode(page, "Done");

  await connectNodes(page, 0, 1);
  await connectNodes(page, 1, 2);
  await connectNodes(page, 2, 3);
  await connectNodes(page, 3, 4);
  await connectNodes(page, 4, 5);

  await page.getByTestId("flow-save-button").click();
  await expect(page).toHaveURL(/\/flows\/(?!new$)[^/]+$/);
  await expect(page.getByTestId("flow-name-input")).toHaveValue(flowName);

  await page.reload();
  await expect(page.getByText("Feasibility Review")).toBeVisible();
  await expect(page.getByText("Human Approval")).toBeVisible();

  await page.getByTestId("add-node-task").click();
  await expect(page.locator(".react-flow__node")).toHaveCount(7);
  await openNodeConfig(page, 6);
  await renameNode(page, "QA Review");
  await page.getByTestId("task-team-id-input").fill("dept_qa");
  await connectNodes(page, 4, 6);
  await connectNodes(page, 6, 5);

  await page.getByTestId("flow-save-version-button").click();
  await expect(page).toHaveURL(/\/flows\/(?!new$)[^/]+$/);

  await page.goto("/flows");
  await expect(page.getByText(flowName)).toHaveCount(2);

  await createProjectWithFlow(page, flowName, projectName);

  await page.getByRole("button", { name: /^flow$/i }).click();
  await expect(page.getByText("NOT_STARTED").nth(1)).toBeVisible();
  await page.getByTestId("flow-start-button").click();
  await expect(page.getByText("Current Node")).toBeVisible();
  await expect(page.getByText("Intake").first()).toBeVisible();
  await expect(page.getByText("Feasibility Review").first()).toBeVisible();

  await page.getByTestId("override-node-select").selectOption({ label: "Implementation" });
  await page.getByTestId("override-reason-input").fill("Operator expedited implementation");
  await page.getByTestId("override-node-button").click();
  await expect(page.getByText("Implementation").first()).toBeVisible();

  await page.reload();
  await page.getByRole("button", { name: /^flow$/i }).click();
  await expect(page.getByText("Implementation").first()).toBeVisible();
  await page.getByRole("button", { name: /^workflow$/i }).click();
  await expect(page.getByText("Operator expedited implementation")).toBeVisible();
});
