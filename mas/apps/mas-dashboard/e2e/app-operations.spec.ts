import { expect, test, type Page } from "@playwright/test";
import { authenticate } from "./auth";

async function login(page: Page): Promise<void> {
  await authenticate(page);
}

test.describe("Operational UI smoke flows", () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test("credentials manager creates masked secret placeholders and deletes them", async ({
    page,
  }) => {
    const name = `E2E_SECRET_${Date.now()}`;
    const value = `real-secret-value-${Date.now()}`;

    await page.goto("/credentials");
    await expect(
      page.getByRole("heading", { name: "Credentials Manager" }),
    ).toBeVisible();
    await page.getByRole("button", { name: /new secret/i }).click();
    await page.getByPlaceholder("OPENAI_API_KEY").fill(name);
    await page.getByPlaceholder("sk-...").fill(value);
    await page
      .getByPlaceholder("OpenAI API key for LLM gateway")
      .fill("E2E credential boundary test");
    await page
      .getByPlaceholder("llm-gateway, tool-service, ceo")
      .fill("llm-gateway, tool-service");
    await page
      .getByPlaceholder("llm-call, tool-exec")
      .fill("llm-call, tool-exec");
    await page.getByRole("button", { name: /save credential/i }).click();

    const row = page.getByRole("row", { name: new RegExp(name) });
    await expect(row).toBeVisible();
    await expect(row.getByText(`<${name}>`)).toBeVisible();
    await expect(page.getByText(value)).toHaveCount(0);

    page.on("dialog", (dialog) => dialog.accept());
    await row.getByTitle("Delete").click();
    await expect(row).toHaveCount(0);
  });

  test("workers are registerable, expandable, searchable, and status controlled", async ({
    page,
  }) => {
    const stamp = Date.now();
    const workerId = `e2e_worker_${stamp}`;

    await page.goto("/workers");
    await expect(
      page.getByRole("heading", { name: "Hiring Board" }),
    ).toBeVisible();
    await expect(page.getByText("Delta Integration Readiness")).toBeVisible();
    await expect(page.getByText("Docling document ingestion")).toBeVisible();
    await expect(
      page.getByText("GitHub REST metadata and task API"),
    ).toBeVisible();
    await expect(page.getByText("trufflehog", { exact: true })).toBeVisible();
    await expect(page.getByText("server-side named credentials")).toBeVisible();
    await page.getByRole("button", { name: /register worker/i }).click();
    await page.getByPlaceholder("my_worker_1").fill(workerId);
    await page.getByPlaceholder("My Worker Agent").fill("E2E Worker");
    await page
      .getByPlaceholder("What this worker does")
      .fill("Registered by Playwright operational smoke test");
    await page.getByPlaceholder("office_chrm").fill("dept_qa");
    await page.getByPlaceholder("WorkerAgent").fill("adapter.main:E2EWorker");
    await page
      .getByRole("button", { name: /^register worker$/i })
      .nth(1)
      .click();

    await page.getByRole("textbox", { name: "Search workers" }).fill(workerId);
    const row = page.getByRole("row", { name: new RegExp(workerId) });
    await expect(row).toBeVisible();
    await row.click();
    await expect(
      page.getByText(/WorkerAgent|adapter\.main:E2EWorker/).first(),
    ).toBeVisible();

    await row.getByTitle("Drain").click();
    await expect(row.getByText("Draining")).toBeVisible();
  });

  test("central tools UI lists, filters, and expands managed tools", async ({
    page,
  }) => {
    await page.goto("/tools");
    await expect(page.getByRole("heading", { name: "Tools" })).toBeVisible();
    await page.getByRole("textbox", { name: "Search tools" }).fill("browser");
    await expect(page.getByText(/browser/i).first()).toBeVisible();
    const firstToolRow = page.locator("tbody tr").first();
    await firstToolRow.click();
    await expect(
      page
        .getByText("Input Schema")
        .or(page.getByText(/Circuit Breaker/i).first()),
    ).toBeVisible();
  });

  test("system visualization exposes hierarchy, permissions, orchestration, and path tracing", async ({
    page,
  }) => {
    await page.goto("/system-viz");
    await expect(
      page.getByRole("heading", { name: "System Visualization" }),
    ).toBeVisible();
    await expect(page.getByText("Mermaid Export")).toBeVisible();
    await expect(page.getByLabel("Mermaid export source")).toContainText(
      "graph TD",
    );

    await page.getByRole("tab", { name: /permissions/i }).click();
    await expect(page.getByText(/communication/i).first()).toBeVisible();

    await page.getByRole("tab", { name: /orchestration/i }).click();
    await expect(page.getByText(/select a flow/i)).toBeVisible();
    await page
      .getByText(
        /Document Review Flow|Escalation Flow|Simple Product Build Flow/i,
      )
      .first()
      .click();
    await expect(page.getByText("Flow Details")).toBeVisible();

    await page.getByRole("button", { name: /toggle path trace mode/i }).click();
    const selects = page.locator("select");
    await selects.nth(0).selectOption({ index: 1 });
    await selects.nth(1).selectOption({ index: 2 });
    await page.getByRole("button", { name: /find path/i }).click();
    await expect(page.getByRole("button", { name: /clear/i })).toBeVisible();
  });

  test("project workspace exposes next actions, audit timeline, artifacts, and usage", async ({
    page,
  }) => {
    const name = `gamma-workspace-${Date.now()}`;

    await page.goto("/projects");
    await page.getByRole("button", { name: /new project/i }).click();
    await page.getByPlaceholder("my-project").fill(name);
    await page
      .getByPlaceholder("What should the agents build?")
      .fill("Gamma workspace Playwright coverage");
    await page.getByRole("button", { name: /^create$/i }).click();

    const row = page.getByRole("row", { name: new RegExp(name) });
    await expect(row).toBeVisible();
    await Promise.all([
      page.waitForURL(/\/projects\/[^/]+$/),
      row.getByRole("link", { name, exact: true }).click(),
    ]);

    await expect(
      page.getByRole("tab", { name: "Workspace", exact: true }),
    ).toBeVisible();
    await expect(page.getByText("Next Operator Action")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Audit Timeline" }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "Resources" }).click();
    await expect(
      page.getByRole("heading", { name: "Artifacts" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Worker Activity" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Project Logs" }),
    ).toBeVisible();

    await page.getByRole("tab", { name: "Cost" }).click();
    await expect(
      page.getByRole("heading", { name: "Cost And Usage" }),
    ).toBeVisible();
  });

  test("system, logs, metrics, and DLQ pages load operational controls", async ({
    page,
  }) => {
    await page.goto("/system");
    await expect(
      page.getByRole("heading", { name: "System Control" }),
    ).toBeVisible();
    await page.getByPlaceholder("e.g. 0 22 * * *").fill("not a cron");
    await expect(
      page.getByRole("button", { name: /save cron schedule/i }),
    ).toBeEnabled();

    await page.goto("/logs");
    await expect(
      page.getByRole("heading", { name: "Container Logs" }),
    ).toBeVisible();
    await page.getByRole("button", { name: /^load$/i }).click();
    await expect(page.getByText(/line|lines|max/i).last()).toBeVisible();

    await page.goto("/metrics");
    await expect(page.getByRole("heading", { name: "Metrics" })).toBeVisible();
    await expect(page.getByText(/LLM Calls\/min by Model/i)).toBeVisible();
    await page.getByRole("button", { name: "15m" }).click();
    await expect(
      page.getByRole("link", { name: "LiteLLM Analytics" }),
    ).toHaveAttribute("href", "/analytics/litellm");
    await expect(
      page.getByRole("link", { name: "OmniRoute Analytics" }),
    ).toHaveAttribute("href", "/analytics/omniroute");

    await page.goto("/dlq");
    await expect(
      page.getByRole("heading", { name: "Dead Letter Queue" }),
    ).toBeVisible();
    await expect(
      page.getByText(/Queue is empty|message.*in queue/i).first(),
    ).toBeVisible();
  });
});
