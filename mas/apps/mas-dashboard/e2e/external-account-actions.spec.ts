import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

const ACCOUNT = {
  id: "external-account-e2e-001",
  worker_id: "worker-external-e2e-001",
  service: "github",
  service_category: "github_organization",
  state: "ACTIVE",
};

async function mockExternalAccountAction(
  page: Page,
  result: Record<string, unknown>,
  captureAction: (payload: Record<string, unknown>) => void,
) {
  await page.route("**/api/identity/external-accounts", async (route) => {
    if (route.request().method() === "POST") {
      captureAction(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(result),
      });
      return;
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [ACCOUNT] }),
    });
  });
}

test("credential rotation success explains the approval handoff", async ({ page }) => {
  let actionPayload: Record<string, unknown> | undefined;
  await mockExternalAccountAction(
    page,
    {
      account_id: ACCOUNT.id,
      approval: { id: "approval-external-e2e-001", state: "PENDING" },
      rotation: "PENDING_APPROVAL",
    },
    (payload) => {
      actionPayload = payload;
    },
  );
  let confirmation = "";
  page.on("dialog", async (dialog) => {
    confirmation = dialog.message();
    await dialog.accept();
  });

  await authenticate(page, "/external-accounts");
  await page.getByRole("button", { name: `Rotate ${ACCOUNT.id}` }).click();
  await expect.poll(() => confirmation).toBe(
    "Request credential rotation for this external account? A human approval is required.",
  );

  const status = page.getByRole("status", {
    name: "External account action status",
  });
  await expect(status).toContainText(
    "Credential rotation request is awaiting human approval.",
  );
  await expect(
    status.getByRole("link", { name: "Review identity approvals" }),
  ).toHaveAttribute("href", "/identity-approvals");
  expect(actionPayload).toEqual({
    action: "external.rotate_credentials",
    id: ACCOUNT.id,
    worker_id: ACCOUNT.worker_id,
    service: ACCOUNT.service,
    service_category: ACCOUNT.service_category,
  });
});

test("account closure success explains that closure is approval-gated", async ({ page }) => {
  await mockExternalAccountAction(
    page,
    {
      account_id: ACCOUNT.id,
      state: "PENDING_APPROVAL",
      approval: { id: "approval-external-e2e-002", state: "PENDING" },
    },
    () => {},
  );
  let confirmation = "";
  page.on("dialog", async (dialog) => {
    confirmation = dialog.message();
    await dialog.accept();
  });

  await authenticate(page, "/external-accounts");
  await page.getByRole("button", { name: `Close ${ACCOUNT.id}` }).click();
  await expect.poll(() => confirmation).toBe(
    "Request closure of this external account? A human approval is required.",
  );

  const status = page.getByRole("status", {
    name: "External account action status",
  });
  await expect(status).toContainText(
    "Account closure request is awaiting human approval.",
  );
  await expect(
    status.getByRole("link", { name: "Review identity approvals" }),
  ).toHaveAttribute("href", "/identity-approvals");
});
