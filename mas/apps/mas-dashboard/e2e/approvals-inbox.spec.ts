import { expect, test, type Page } from "@playwright/test";
import { authenticate } from "./auth";

const ceoRequest = {
  id: "10000000-0000-4000-8000-000000000011",
  action: "system.restart",
  actor_id: "ceo-agent",
  actor_role: "ceo",
  privilege_level: "privileged",
  risk_level: "high",
  payload_json: { service: "router", credential_token: "must-not-render" },
  requested_at: "2026-10-07T12:10:00Z",
  decision: "pending_approval",
};

const projectDecision = {
  id: "20000000-0000-4000-8000-000000000021",
  project_id: "30000000-0000-4000-8000-000000000031",
  project_name: "Alpha research project",
  project_state: "HUMAN_APPROVAL",
  company_id: "40000000-0000-4000-8000-000000000041",
  gate_type: "human_approval",
  status: "PENDING",
  created_at: "2026-10-07T12:00:00Z",
};

async function mockCeoReads(
  page: Page,
  getPending: () => unknown[] = () => [],
  getAudit: () => unknown[] = () => [],
) {
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(getPending()) });
  });
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/audit", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(getAudit()) });
  });
}

test("inbox combines CEO and project decisions, links project authority, and submits CEO action once", async ({ page }) => {
  let pending = [ceoRequest];
  let audit: Array<Record<string, unknown>> = [];
  const decisionBodies: Array<Record<string, unknown>> = [];
  const projectQueries: URLSearchParams[] = [];

  await mockCeoReads(page, () => pending, () => audit);
  await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
    projectQueries.push(new URL(route.request().url()).searchParams);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [projectDecision], next_cursor: null }),
    });
  });
  await page.route((url) => url.pathname === `/api/ceo/privileged-actions/${ceoRequest.id}/approve`, async (route) => {
    decisionBodies.push(route.request().postDataJSON() as Record<string, unknown>);
    await new Promise((resolve) => setTimeout(resolve, 200));
    pending = [];
    audit = [{ ...ceoRequest, payload_json: undefined, decision: "approved", decided_by: "dashboard-operator", reason: "Restart after reviewed maintenance window" }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ record_id: ceoRequest.id, decision: "approved" }) });
  });

  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  const inbox = main.getByRole("list", { name: "Unified approval inbox" });
  await expect(main.getByRole("heading", { name: "Pending requests" })).toBeVisible();
  await expect(inbox.getByText("CEO privileged")).toBeVisible();
  await expect(inbox.getByText("Project decision")).toBeVisible();
  await expect(inbox.getByText("Alpha research project")).toBeVisible();
  await expect(inbox.getByText(/Company 40000000-0000-4000-8000-000000000041/)).toBeVisible();
  await expect(inbox.getByText("Pending · high risk")).toBeVisible();
  await expect(inbox.getByText("PENDING", { exact: true })).toBeVisible();
  await expect(inbox.getByRole("link", { name: "Open Alpha research project project decision context" }))
    .toHaveAttribute("href", "/projects/30000000-0000-4000-8000-000000000031");
  expect(projectQueries).toHaveLength(1);
  expect(projectQueries[0]?.get("limit")).toBe("50");
  expect(projectQueries[0]?.has("cursor")).toBe(false);

  await expect(inbox.getByText(/must-not-render/)).toHaveCount(0);
  await inbox.getByRole("button", { name: "View details" }).click();
  const details = main.getByRole("region", { name: "Request details" });
  await details.getByLabel("Decision reason (required)").fill("Restart after reviewed maintenance window");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();
  const approve = details.getByRole("button", { name: "Approve request" });
  await approve.evaluate((button) => {
    (button as HTMLButtonElement).click();
    (button as HTMLButtonElement).click();
  });

  await expect(main.getByRole("status").filter({ hasText: "Decision recorded: approved" })).toBeVisible();
  await expect(inbox.getByText("Alpha research project")).toBeVisible();
  expect(decisionBodies).toHaveLength(1);
  expect(decisionBodies[0]).toEqual({ approved: true, reason: "Restart after reviewed maintenance window" });
});

test("project decision paging sends the cursor and appends bounded rows", async ({ page }) => {
  const secondDecision = {
    ...projectDecision,
    id: "20000000-0000-4000-8000-000000000022",
    project_id: "30000000-0000-4000-8000-000000000032",
    project_name: "Beta engineering project",
    created_at: "2026-10-07T11:00:00Z",
  };
  const queries: URLSearchParams[] = [];
  await mockCeoReads(page);
  await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
    const query = new URL(route.request().url()).searchParams;
    queries.push(query);
    const cursor = query.get("cursor");
    const pageBody = cursor === "page-two-cursor"
      ? { items: [secondDecision], next_cursor: null }
      : { items: [projectDecision], next_cursor: "page-two-cursor" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(pageBody) });
  });

  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  const inbox = main.getByRole("list", { name: "Unified approval inbox" });
  await expect(inbox.getByText("Alpha research project")).toBeVisible();
  await main.getByRole("button", { name: "Load more project decisions" }).click();
  await expect(inbox.getByText("Beta engineering project")).toBeVisible();
  await expect(inbox.getByRole("listitem")).toHaveCount(2);
  expect(queries).toHaveLength(2);
  expect(queries[0].get("limit")).toBe("50");
  expect(queries[0].has("cursor")).toBe(false);
  expect(queries[1].get("limit")).toBe("50");
  expect(queries[1].get("cursor")).toBe("page-two-cursor");
});

test("stale CEO decisions show a conflict state and cannot be submitted again", async ({ page }) => {
  let calls = 0;
  await mockCeoReads(page, () => [ceoRequest]);
  await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], next_cursor: null }) });
  });
  await page.route((url) => url.pathname === `/api/ceo/privileged-actions/${ceoRequest.id}/approve`, async (route) => {
    calls += 1;
    await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "already decided" }) });
  });

  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  await main.getByRole("button", { name: "View details" }).click();
  const details = main.getByRole("region", { name: "Request details" });
  await details.getByLabel("Decision reason (required)").fill("Reviewed request");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();
  await details.getByRole("button", { name: "Approve request" }).click();

  await expect(main.getByRole("status").filter({ hasText: "no longer pending" })).toBeVisible();
  await expect(details.getByText("Refresh the inbox to see its current status.")).toBeVisible();
  await expect(details.getByRole("button", { name: "Approve request" })).toHaveCount(0);
  expect(calls).toBe(1);
});

test("inbox has a useful empty state when both queues are clear", async ({ page }) => {
  await mockCeoReads(page);
  await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], next_cursor: null }) });
  });

  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  await expect(main.getByText("No approvals are currently waiting for review.")).toBeVisible();
  await expect(main.getByText("No project decisions are waiting for review.")).toBeVisible();
  await expect(main.getByRole("button", { name: "Load more project decisions" })).toHaveCount(0);
});
