import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const DLQ_FIXTURE = {
  dead_letters: [
    {
      id: "dlq-e2e-001",
      stream: "e2e-stream",
      message_type: "e2e.message",
      failure_reason: "Retained after a failed refresh",
      retry_count: 1,
      created_at: "2026-08-01T00:00:00Z",
      envelope: {},
    },
  ],
  total: 1,
};

test("dead-letter queue retains the last known state when a refresh fails", async ({
  page,
}) => {
  await page.route("**/api/dlq", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DLQ_FIXTURE),
    });
  });

  await authenticate(page, "/dlq");
  await expect(page.getByRole("heading", { name: "Dead Letter Queue" })).toBeVisible();
  await expect(page.getByText("dlq-e2e-", { exact: true })).toBeVisible();

  await page.unroute("**/api/dlq");
  let failNextRead = true;
  await page.route("**/api/dlq", async (route) => {
    if (failNextRead) {
      failNextRead = false;
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "dead-letter fixture unavailable" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DLQ_FIXTURE),
    });
  });

  await page.getByRole("button", { name: "Refresh dead letter queue" }).click();
  await expect(page.getByText("Showing last known dead-letter queue")).toBeVisible();
  await expect(page.getByText(/latest dead-letter refresh failed/i)).toBeVisible();
  await expect(page.getByText("dlq-e2e-", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Showing last known dead-letter queue")).toHaveCount(0);
  await expect(page.getByText("dlq-e2e-", { exact: true })).toBeVisible();
});
