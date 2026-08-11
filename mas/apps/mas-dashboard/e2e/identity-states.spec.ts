import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

test("identity tables label stale records and preserve a retry path", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route("**/api/identity/identities**", async (route) => {
    requestCount += 1;
    if (requestCount === 1 || requestCount >= 3) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "identity-e2e-001",
              worker_id: "worker-e2e",
              service: "mail",
              state: requestCount === 1 ? "ACTIVE" : "RECOVERED",
            },
          ],
        }),
      });
      return;
    }
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ error: "identity fixture unavailable" }),
    });
  });

  await authenticate(page, "/identities");
  await expect(page.getByRole("heading", { name: "Identities" })).toBeVisible();
  await expect(page.getByText("identity-e2e-001")).toBeVisible();
  const table = page.getByRole("table", { name: "Identities records" });
  await expect(table).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "id", exact: true })).toHaveAttribute("scope", "col");
  await expect(page.getByRole("button", { name: "Suspend identity-e2e-001" })).toHaveCSS("min-height", "44px");

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByText("Showing last known records")).toBeVisible();
  await expect(page.getByText(/latest refresh failed/i)).toBeVisible();
  await expect(page.getByText("identity-e2e-001")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known records")).toHaveCount(0);
  await expect(page.getByText("RECOVERED")).toBeVisible();
  await expect(page.getByText(/latest refresh failed/i)).toHaveCount(0);
});
