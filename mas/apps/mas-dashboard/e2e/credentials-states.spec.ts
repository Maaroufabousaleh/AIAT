import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const CREDENTIALS_FIXTURE = [
  {
    id: "credentials-e2e-001",
    name: "E2E_READ_TOKEN",
    description: "Metadata retained after a failed credentials refresh",
    secret_type: "token",
    policy: {
      allowed_requesters: ["e2e"],
      allowed_contexts: ["test"],
      rate_limit_per_minute: 10,
      require_approval: true,
      enabled: true,
      expires_at: null,
    },
    usage_count: 2,
    last_used_at: "2026-08-01T00:00:00Z",
    created_by: "e2e",
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-02T00:00:00Z",
    placeholder: "<E2E_READ_TOKEN>",
  },
];

test("credentials list retains metadata when a refresh fails", async ({ page }) => {
  await page.route("**/api/credentials", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CREDENTIALS_FIXTURE),
    });
  });

  await authenticate(page, "/credentials");
  await expect(
    page.getByRole("heading", { name: "Credentials Manager", exact: true }),
  ).toBeVisible();
  const credentials = page.getByRole("main", { name: "Credentials manager" });
  await expect(credentials).toBeVisible();
  await expect(
    credentials.getByRole("region", { name: "Credential security model" }),
  ).toBeVisible();
  await expect(
    credentials.getByRole("button", { name: "Refresh credentials" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    credentials.getByRole("button", { name: "Create new credential" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    credentials.getByRole("link", { name: "View credential resolve audit log" }),
  ).toHaveCSS("min-height", "44px");
  const table = credentials.getByRole("table", { name: "Credentials" });
  await expect(table.locator("caption")).toHaveText(
    /redacted placeholders, policy status, and usage metadata/i,
  );
  for (const heading of [
    "Select all credentials",
    "Name",
    "Type",
    "Description",
    "Placeholder",
    "Status",
    "Uses",
    "Last Used",
    "Actions",
  ]) {
    await expect(
      table.getByRole("columnheader", { name: heading }),
    ).toHaveAttribute("scope", "col");
  }
  await expect(
    credentials.getByRole("checkbox", { name: "Select all credentials" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    credentials.getByRole("checkbox", { name: "Select E2E_READ_TOKEN" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    credentials.getByRole("button", {
      name: "Copy placeholder <E2E_READ_TOKEN>",
    }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    credentials.getByRole("button", { name: "Delete E2E_READ_TOKEN" }),
  ).toHaveCSS("min-height", "44px");

  await credentials
    .getByRole("button", { name: "Create new credential" })
    .click();
  const dialog = page.getByRole("dialog", { name: "New Credential" });
  await expect(dialog).toBeVisible();
  for (const label of [
    "Name *",
    "Type",
    "Value *",
    "Description",
    "Allowed Requesters (comma-separated, empty = any)",
    "Allowed Contexts (comma-separated, empty = any)",
    "Rate limit / min (0 = unlimited)",
  ]) {
    await expect(dialog.getByLabel(label)).toBeVisible();
  }
  await expect(
    dialog.getByRole("button", { name: "Show secret value" }),
  ).toHaveCSS("min-height", "44px");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(
    dialog.getByRole("button", { name: "Save Credential" }),
  ).toHaveCSS("min-height", "44px");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("E2E_READ_TOKEN", { exact: true })).toBeVisible();
  await expect(page.getByText("<E2E_READ_TOKEN>", { exact: true })).toBeVisible();

  await page.unroute("**/api/credentials");
  let failNextRead = true;
  await page.route("**/api/credentials", async (route) => {
    if (failNextRead) {
      failNextRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "credentials fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(CREDENTIALS_FIXTURE),
    });
  });

  await page.getByRole("button", { name: "Refresh credentials" }).click();
  await expect(page.getByText("Showing last known credentials")).toBeVisible();
  await expect(page.getByText(/latest credentials refresh failed/i)).toBeVisible();
  await expect(page.getByText("E2E_READ_TOKEN", { exact: true })).toBeVisible();
  await expect(page.getByText("<E2E_READ_TOKEN>", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known credentials")).toHaveCount(0);
  await expect(page.getByText("E2E_READ_TOKEN", { exact: true })).toBeVisible();
});
