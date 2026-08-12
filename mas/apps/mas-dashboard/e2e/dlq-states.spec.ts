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
  await expect(
    page.getByRole("heading", { name: "Dead Letter Queue", exact: true }),
  ).toBeVisible();
  const queue = page.getByRole("main", { name: "Dead-letter queue" });
  await expect(queue).toBeVisible();
  await expect(
    queue.getByRole("region", { name: "Dead letter queue summary" }),
  ).toBeVisible();
  await expect(
    queue.getByRole("toolbar", { name: "Queue filters and sorting" }),
  ).toBeVisible();
  await expect(
    queue.getByRole("button", { name: "Refresh dead letter queue" }),
  ).toHaveCSS("min-height", "44px");
  await expect(queue.locator("#dlq-sort")).toHaveCSS("min-height", "44px");
  const severityFilters = queue
    .getByRole("group", { name: "Filter by severity" })
    .getByRole("button");
  await expect(severityFilters).toHaveCount(5);
  for (const filter of await severityFilters.all()) {
    await expect(filter).toHaveCSS("min-height", "44px");
  }
  await expect(severityFilters.first()).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("dlq-e2e-", { exact: true })).toBeVisible();
  const entries = queue.getByRole("list", {
    name: "Dead letter queue entries",
  });
  await expect(entries).toBeVisible();
  await expect(entries.getByRole("listitem")).toHaveCount(1);
  await expect(
    queue.getByRole("checkbox", { name: "Select dead letter dlq-e2e-001" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    queue.getByRole("button", { name: "Replay dead letter dlq-e2e-001" }),
  ).toHaveCSS("min-height", "44px");
  const inspectEnvelope = queue.locator(
    'button[aria-controls="dlq-dlq-e2e-001-envelope"]',
  );
  await expect(inspectEnvelope).toHaveAccessibleName("Inspect envelope");
  await expect(inspectEnvelope).toHaveCSS("min-height", "44px");
  await inspectEnvelope.click();
  await expect(inspectEnvelope).toHaveAttribute("aria-expanded", "true");
  await expect(
    queue.getByRole("region", { name: "Envelope JSON" }),
  ).toBeVisible();

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

test("dead-letter queue access denial on first load hides queue controls", async ({
  page,
}) => {
  await page.route("**/api/dlq", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "dead-letter queue access denied" }),
    });
  });

  await authenticate(page, "/dlq");
  const queue = page.getByRole("main", { name: "Dead-letter queue" });
  await expect(
    queue.getByRole("region", { name: "Dead-letter queue access status" }),
  ).toBeVisible();
  await expect(
    queue.getByRole("heading", { name: "Dead-letter queue access denied" }),
  ).toBeVisible();
  await expect(
    queue.getByText("No live dead-letter data is available", { exact: true }),
  ).toBeVisible();
  await expect(
    queue.getByRole("button", { name: "Refresh dead letter queue" }),
  ).toHaveCount(0);
  await expect(
    queue.getByRole("toolbar", { name: "Queue filters and sorting" }),
  ).toHaveCount(0);
  await expect(queue.getByRole("checkbox")).toHaveCount(0);
  await expect(queue.getByRole("button", { name: /Replay/ })).toHaveCount(0);
});

test("dead-letter queue access denial after a successful read retains messages without mutation controls", async ({
  page,
}) => {
  let reads = 0;
  await page.route("**/api/dlq", async (route) => {
    reads += 1;
    if (reads > 1) {
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "dead-letter queue access expired" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(DLQ_FIXTURE),
    });
  });

  await authenticate(page, "/dlq");
  const queue = page.getByRole("main", { name: "Dead-letter queue" });
  await expect(queue.getByText("dlq-e2e-", { exact: true })).toBeVisible();
  await queue.getByRole("button", { name: "Refresh dead letter queue" }).click();

  await expect(
    queue.getByRole("region", { name: "Dead-letter queue access status" }),
  ).toBeVisible();
  await expect(queue.getByText("dlq-e2e-", { exact: true })).toBeVisible();
  await expect(
    queue.getByRole("button", { name: "Refresh dead letter queue" }),
  ).toHaveCount(0);
  await expect(
    queue.getByRole("toolbar", { name: "Queue filters and sorting" }),
  ).toHaveCount(0);
  await expect(queue.getByRole("checkbox")).toHaveCount(0);
  await expect(queue.getByRole("button", { name: /Replay/ })).toHaveCount(0);
  await expect(
    queue.getByRole("button", { name: "Inspect envelope" }),
  ).toBeVisible();
});
