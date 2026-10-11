import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { authenticate } from "./auth";

async function mockEmptyProjectInbox(page: Page) {
  await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], next_cursor: null }) });
  });
}

const pendingRequest = {
  id: "10000000-0000-4000-8000-000000000001",
  action: "system.shutdown",
  actor_id: "ceo-agent",
  actor_role: "ceo",
  privilege_level: "privileged",
  risk_level: "high",
  payload_json: { reason: "planned maintenance", credential_token: "must-not-render" },
  requested_at: "2026-10-07T12:00:00Z",
  decision: "pending_approval",
};

test("operator inspects a pending request, approves it, and sees the refreshed audit decision", async ({ page }) => {
  let pending = [pendingRequest];
  let audit: Array<Record<string, unknown>> = [];
  const decisionRequests: Array<{ approved: boolean; reason: string; decided_by?: string }> = [];

  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(pending) });
  });
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/audit", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(audit) });
  });
  await page.route((url) => url.pathname === `/api/ceo/privileged-actions/${pendingRequest.id}/approve`, async (route) => {
    const decisionRequest = route.request().postDataJSON() as { approved: boolean; reason: string; decided_by?: string };
    decisionRequests.push(decisionRequest);
    expect(decisionRequest).toMatchObject({ approved: true, reason: "Approved for scheduled maintenance" });
    expect(decisionRequest.decided_by).toBeUndefined();
    pending = [];
    audit = [{ ...pendingRequest, payload_json: undefined, decision: "approved", decided_by: "dashboard-operator", decided_at: "2026-10-07T12:05:00Z", reason: decisionRequest.reason }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ record_id: pendingRequest.id, decision: "approved" }) });
  });

  await mockEmptyProjectInbox(page);
  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  await expect(main.getByRole("heading", { name: "Pending requests" })).toBeVisible();
  await expect(main.getByText("system.shutdown").first()).toBeVisible();
  await main.getByRole("button", { name: "View details" }).click();
  const details = main.getByRole("region", { name: "Request details" });
  await expect(details.getByText("Request payload")).toBeVisible();
  await expect(details.getByText(/must-not-render/)).toHaveCount(0);
  await details.getByLabel("Decision reason (required)").fill("Approved for scheduled maintenance");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();
  await details.getByRole("button", { name: "Approve request" }).click();

  await expect(main.getByRole("status").filter({ hasText: "Decision recorded: approved" })).toBeVisible();
  await expect(main.getByText("No privileged-action requests are waiting for review.")).toBeVisible();
  await expect(main.getByRole("listitem").filter({ hasText: "dashboard-operator" }).getByText("approved", { exact: true })).toBeVisible();
  expect(decisionRequests[0]?.approved).toBe(true);
});

test("operator can reject a pending request with an audited reason", async ({ page }) => {
  let pending = [{ ...pendingRequest, id: "10000000-0000-4000-8000-000000000002", action: "company.delete" }];
  let audit: Array<Record<string, unknown>> = [];
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(pending) });
  });
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/audit", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(audit) });
  });
  await page.route((url) => url.pathname.endsWith("/approve"), async (route) => {
    const request = route.request().postDataJSON() as { approved: boolean; reason: string };
    expect(request).toMatchObject({ approved: false, reason: "Target was not verified" });
    const row = pending[0];
    pending = [];
    audit = [{ ...row, payload_json: undefined, decision: "rejected", decided_by: "dashboard-operator", decided_at: "2026-10-07T12:05:00Z", reason: request.reason }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ record_id: row.id, decision: "rejected" }) });
  });

  await mockEmptyProjectInbox(page);
  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  await main.getByRole("button", { name: "View details" }).click();
  const details = main.getByRole("region", { name: "Request details" });
  await details.getByLabel("Decision reason (required)").fill("Target was not verified");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();
  await details.getByRole("button", { name: "Reject request" }).click();
  await expect(main.getByRole("status").filter({ hasText: "Decision recorded: rejected" })).toBeVisible();
  await expect(main.getByRole("listitem").filter({ hasText: "dashboard-operator" }).getByText("rejected", { exact: true })).toBeVisible();
});

test("approval page presents a retryable error when the audit API is unavailable", async ({ page }) => {
  let fail = true;
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/pending", async (route) => {
    if (fail) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporarily unavailable" }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/audit", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await mockEmptyProjectInbox(page);
  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  await expect(main.getByRole("alert").getByText("Request failed (HTTP 503)")).toBeVisible();
  fail = false;
  await main.getByRole("button", { name: "Retry" }).click();
  await expect(main.getByText("No privileged-action requests are waiting for review.")).toBeVisible();
});

test("a project-decision outage does not block reviewing a CEO approval", async ({ page }) => {
  let pending = [pendingRequest];
  let audit: Array<Record<string, unknown>> = [];
  let decisionCalls = 0;
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/pending", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(pending) });
  });
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/audit", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(audit) });
  });
  await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporarily unavailable" }) });
  });
  await page.route((url) => url.pathname === `/api/ceo/privileged-actions/${pendingRequest.id}/approve`, async (route) => {
    decisionCalls += 1;
    const request = route.request().postDataJSON() as { approved: boolean; reason: string };
    pending = [];
    audit = [{ ...pendingRequest, payload_json: undefined, decision: request.approved ? "approved" : "rejected", decided_by: "dashboard-operator", reason: request.reason }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ record_id: pendingRequest.id, decision: "approved" }) });
  });

  await authenticate(page, "/approvals");
  const main = page.getByRole("main", { name: "Privileged action approvals" });
  const inbox = main.getByRole("list", { name: "Unified approval inbox" });
  await expect(main.getByText("Some approval queues could not be loaded. Retry to refresh the inbox.")).toBeVisible();
  await expect(inbox.getByText("system.shutdown")).toBeVisible();
  await expect(main.getByText("The project decision queue is unavailable. CEO privileged-action requests remain available.")).toBeVisible();

  await inbox.getByRole("button", { name: "View details" }).click();
  const details = main.getByRole("region", { name: "Request details" });
  await details.getByLabel("Decision reason (required)").fill("Reviewed the CEO request");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();
  await expect(details.getByRole("button", { name: "Approve request" })).toBeEnabled();
  await details.getByRole("button", { name: "Approve request" }).click();

  await expect(main.getByRole("status").filter({ hasText: "Decision recorded: approved" })).toBeVisible();
  expect(decisionCalls).toBe(1);
});

test("approval decisions stay disabled until a failed refresh recovers", async ({ page }) => {
  let fail = false;
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/pending", async (route) => {
    if (fail) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "temporarily unavailable" }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([pendingRequest]) });
  });
  await page.route((url) => url.pathname === "/api/ceo/privileged-actions/audit", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await mockEmptyProjectInbox(page);
  await authenticate(page, `/approvals?request_id=${pendingRequest.id}`);

  const main = page.getByRole("main", { name: "Privileged action approvals" });
  const details = main.getByRole("region", { name: "Request details" });
  await expect(details.getByLabel("Decision reason (required)")).toBeVisible();
  await details.getByLabel("Decision reason (required)").fill("Reviewed the request details");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();

  fail = true;
  await main.getByRole("button", { name: "Refresh approvals" }).click();
  await expect(main.getByRole("alert").getByText("Request failed (HTTP 503)")).toBeVisible();
  await expect(details.getByText("Current pending status could not be verified. Refresh the inbox before recording a decision.")).toBeVisible();
  await expect(details.getByRole("button", { name: "Approve request" })).toHaveCount(0);
  await expect(details.getByRole("button", { name: "Reject request" })).toHaveCount(0);

  fail = false;
  await main.getByRole("button", { name: "Retry" }).click();
  await expect(details.getByRole("button", { name: "Approve request" })).toBeDisabled();
  await expect(details.getByRole("button", { name: "Reject request" })).toBeDisabled();
  await expect(details.getByLabel("Decision reason (required)")).toHaveValue("");
  await details.getByLabel("Decision reason (required)").fill("Re-reviewed after the inbox recovered");
  await details.getByLabel("I reviewed this request and intend to record a decision.").check();
  await expect(details.getByRole("button", { name: "Approve request" })).toBeEnabled();
  await expect(details.getByRole("button", { name: "Reject request" })).toBeEnabled();
});
