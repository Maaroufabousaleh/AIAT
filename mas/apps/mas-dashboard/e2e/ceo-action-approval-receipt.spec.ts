import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const REQUEST_ID = "70000000-0000-4000-8000-000000000001";
const SECOND_REQUEST_ID = "70000000-0000-4000-8000-000000000002";
const ACTION = "system.shutdown";

const pendingRequest = {
  id: REQUEST_ID,
  action: ACTION,
  actor_id: "operator",
  actor_role: "operator",
  privilege_level: "privileged",
  risk_level: "critical",
  payload_json: { reason: "planned maintenance" },
  requested_at: "2026-10-07T12:00:00Z",
  decision: "pending_approval",
};

const secondPendingRequest = {
  ...pendingRequest,
  id: SECOND_REQUEST_ID,
  action: "team.restart",
};

test("CEO privileged-action receipt opens the matching approval without deciding it", async ({ page }) => {
  let actionPosts = 0;
  let approvalPosts = 0;
  const unexpectedApiRequests: string[] = [];
  const externalOrigins: string[] = [];
  const appOrigin = new URL(process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4000").origin;

  page.on("request", (request) => {
    const origin = new URL(request.url()).origin;
    if (origin !== appOrigin) externalOrigins.push(origin);
  });

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === "/api/projects" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/workers" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/governance/model-profiles" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/governance/model-profiles/catalogue" && request.method() === "GET") {
      await route.fulfill({ json: { schema_version: "receipt-e2e-v1", entries: [] } });
      return;
    }
    if (path === "/api/governance/runs" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/governance/stewards" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/executive/actions/ceo/privileged-actions" && request.method() === "POST") {
      actionPosts += 1;
      await route.fulfill({
        json: {
          schema_version: "aiat.executive-action.v1",
          role: "ceo",
          action: "request_privileged_action",
          requested_by: "operator",
          authenticated_principal: "operator",
          result: {
            allowed: false,
            level: "privileged",
            decision: "pending_approval",
            record_id: REQUEST_ID,
            risk: "critical",
            reason: "pending_human_approval",
          },
          evidence: {
            schema_version: "aiat.executive-action.v1",
            role: "ceo",
            action: ACTION,
            kind: "privileged_action",
            record_id: REQUEST_ID,
          },
        },
      });
      return;
    }
    if (path === "/api/ceo/privileged-actions/pending" && request.method() === "GET") {
      await route.fulfill({ json: [pendingRequest, secondPendingRequest] });
      return;
    }
    if (path === "/api/ceo/privileged-actions/audit" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/approvals/project-decisions" && request.method() === "GET") {
      await route.fulfill({ json: { items: [], next_cursor: null } });
      return;
    }
    if (path === `/api/ceo/privileged-actions/${REQUEST_ID}/approve` && request.method() === "POST") {
      approvalPosts += 1;
      await route.fulfill({ json: { record_id: REQUEST_ID, decision: "approved" } });
      return;
    }

    unexpectedApiRequests.push(`${request.method()} ${path}`);
    await route.fulfill({ status: 501, json: { detail: "Unexpected API request in approval-receipt fixture." } });
  });

  await authenticate(page, "/governance");
  const actions = page.getByRole("region", { name: "Executive actions" });
  await actions.getByText("Advanced action and payload").click();
  await actions.getByLabel("Action", { exact: true }).fill(ACTION);
  await actions.getByLabel("Payload (JSON object)").fill('{"reason":"planned maintenance"}');
  await actions.getByRole("checkbox").check();
  await actions.getByRole("button", { name: "Request privileged action" }).click();

  const result = page.getByRole("status", { name: "Executive action result" });
  const receipt = result.getByRole("region", { name: "Privileged action pending approval" });
  await expect(receipt.getByRole("heading", { name: "Request awaiting human approval" })).toBeVisible();
  await expect(receipt.getByText(ACTION, { exact: true })).toBeVisible();
  await expect(receipt.getByText("critical", { exact: true })).toBeVisible();
  await expect(receipt.getByText(REQUEST_ID, { exact: true })).toBeVisible();

  const reviewLink = receipt.getByRole("link", { name: "Review this request in the Approval inbox" });
  await expect(reviewLink).toHaveAttribute("href", `/approvals?request_id=${REQUEST_ID}`);
  await reviewLink.click();

  await expect(page).toHaveURL(new RegExp(`/approvals\\?request_id=${REQUEST_ID}`));
  const approvals = page.getByRole("main", { name: "Privileged action approvals" });
  const details = approvals.getByRole("region", { name: "Request details" });
  await expect(details.getByText(REQUEST_ID)).toBeVisible();
  await expect(details.getByText(ACTION, { exact: true })).toBeVisible();
  await expect(details.getByText("This page records an audited CEO decision only. It does not dispatch the governed action.")).toBeVisible();
  await expect(details).toBeFocused();
  await expect(details.getByRole("button", { name: "Approve request" })).toBeDisabled();
  await expect(details.getByRole("button", { name: "Reject request" })).toBeDisabled();

  await page.evaluate((id) => {
    window.history.pushState({}, "", `/approvals?request_id=${id}`);
  }, SECOND_REQUEST_ID);
  await expect(page).toHaveURL(new RegExp(`/approvals\\?request_id=${SECOND_REQUEST_ID}`));
  await expect(details.getByText(SECOND_REQUEST_ID)).toBeVisible();
  await expect(details.getByText("team.restart", { exact: true })).toBeVisible();
  await approvals.getByRole("button", { name: "Refresh approvals" }).click();
  await expect(details.getByText(SECOND_REQUEST_ID)).toBeVisible();
  await expect(details.getByText("team.restart", { exact: true })).toBeVisible();

  expect(actionPosts).toBe(1);
  expect(approvalPosts).toBe(0);
  expect(unexpectedApiRequests).toEqual([]);
  expect(externalOrigins).toEqual([]);
});
