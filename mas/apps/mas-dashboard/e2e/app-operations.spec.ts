import { expect, test, type Page } from "@playwright/test";

async function login(page: Page): Promise<void> {
  const username = process.env.E2E_DASHBOARD_USERNAME ?? "admin";
  const password = process.env.E2E_DASHBOARD_PASSWORD ?? "admin";

  await page.goto("/login");
  await page.getByPlaceholder("admin").fill(username);
  await page.locator('input[type="password"]').fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();
  await page.waitForURL(/\/$/);
}

test.describe("Operational UI smoke flows", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("credentials manager creates masked secret placeholders and deletes them", async ({ page }) => {
    const name = `E2E_SECRET_${Date.now()}`;
    const value = `real-secret-value-${Date.now()}`;

    await page.goto("/credentials");
    await expect(page.getByRole("heading", { name: "Credentials Manager" })).toBeVisible();
    await page.getByRole("button", { name: /new secret/i }).click();
    await page.getByPlaceholder("OPENAI_API_KEY").fill(name);
    await page.getByPlaceholder("sk-...").fill(value);
    await page.getByPlaceholder("OpenAI API key for LLM gateway").fill("E2E credential boundary test");
    await page.getByPlaceholder("llm-gateway, tool-service, ceo").fill("llm-gateway, tool-service");
    await page.getByPlaceholder("llm-call, tool-exec").fill("llm-call, tool-exec");
    await page.getByRole("button", { name: /save credential/i }).click();

    const row = page.getByRole("row", { name: new RegExp(name) });
    await expect(row).toBeVisible();
    await expect(row.getByText(`<${name}>`)).toBeVisible();
    await expect(page.getByText(value)).toHaveCount(0);

    page.on("dialog", (dialog) => dialog.accept());
    await row.getByTitle("Delete").click();
    await expect(row).toHaveCount(0);
  });

  test("workers are registerable, expandable, searchable, and status controlled", async ({ page }) => {
    const stamp = Date.now();
    const workerId = `e2e_worker_${stamp}`;

    await page.goto("/workers");
    await expect(page.getByRole("heading", { name: "Workers" })).toBeVisible();
    await page.getByRole("button", { name: /register worker/i }).click();
    await page.getByPlaceholder("my_worker_1").fill(workerId);
    await page.getByPlaceholder("My Worker Agent").fill("E2E Worker");
    await page.getByPlaceholder("What this worker does").fill("Registered by Playwright operational smoke test");
    await page.getByPlaceholder("dept_production").fill("dept_qa");
    await page.getByPlaceholder("https://github.com/org/repo").fill("https://github.com/example/e2e-worker");
    await page.getByPlaceholder("WorkerAgent").fill("adapter.main:E2EWorker");
    await page.getByRole("button", { name: /^register worker$/i }).nth(1).click();

    await page.getByPlaceholder("Search workers...").fill(workerId);
    const row = page.getByRole("row", { name: new RegExp(workerId) });
    await expect(row).toBeVisible();
    await row.click();
    await expect(page.getByText(/WorkerAgent|adapter\.main:E2EWorker/).first()).toBeVisible();
    await expect(page.getByText("example/e2e-worker")).toBeVisible();

    const power = row.getByRole("button");
    await power.click();
    await expect(row.getByText(/Active|Inactive/)).toBeVisible();
  });

  test("central tools UI lists, filters, and expands managed tools", async ({ page }) => {
    await page.goto("/tools");
    await expect(page.getByRole("heading", { name: "Tools" })).toBeVisible();
    await page.getByPlaceholder("Search tools...").fill("browser");
    await expect(page.getByText(/browser/i).first()).toBeVisible();
    const firstToolRow = page.locator("tbody tr").first();
    await firstToolRow.click();
    await expect(page.getByText("Input Schema").or(page.getByText(/Circuit Breaker/i).first())).toBeVisible();
  });

  test("system visualization exposes hierarchy, permissions, orchestration, and path tracing", async ({ page }) => {
    await page.goto("/system-viz");
    await expect(page.getByRole("heading", { name: "System Visualization" })).toBeVisible();

    await page.getByRole("button", { name: /permissions/i }).click();
    await expect(page.getByText(/communication/i).first()).toBeVisible();

    await page.getByRole("button", { name: /orchestration/i }).click();
    await expect(page.getByText(/select a flow/i)).toBeVisible();
    await page.getByText(/Document Review Flow|Escalation Flow|Simple Product Build Flow/i).first().click();
    await expect(page.getByText("Flow Details")).toBeVisible();

    await page.getByRole("button", { name: /trace path/i }).click();
    const selects = page.locator("select");
    await selects.nth(0).selectOption({ index: 1 });
    await selects.nth(1).selectOption({ index: 2 });
    await page.getByRole("button", { name: /find path/i }).click();
    await expect(page.getByRole("button", { name: /clear/i })).toBeVisible();
  });

  test("system, logs, metrics, and DLQ pages load operational controls", async ({ page }) => {
    await page.goto("/system");
    await expect(page.getByRole("heading", { name: "System Control" })).toBeVisible();
    await page.getByPlaceholder("e.g. 0 22 * * *").fill("not a cron");
    await expect(page.getByRole("button", { name: /save schedule/i })).toBeEnabled();

    await page.goto("/logs");
    await expect(page.getByRole("heading", { name: "Container Logs" })).toBeVisible();
    await page.getByRole("button", { name: /^load$/i }).click();
    await expect(page.getByText(/line|lines|max/i).last()).toBeVisible();

    await page.goto("/metrics");
    await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
    await expect(page.getByText(/LLM Calls\/min by Model/i)).toBeVisible();
    await page.getByRole("button", { name: "15m" }).click();

    await page.goto("/dlq");
    await expect(page.getByRole("heading", { name: "Dead Letter Queue" })).toBeVisible();
    await expect(page.getByText(/Queue is empty|message.*in queue/i).first()).toBeVisible();
  });
});
