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
    // The visible label is "New Secret"; the accessible name is the
    // descriptive action label exposed to keyboard/screen-reader users.
    await page.getByRole("button", { name: /create new credential/i }).click();
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
        .or(page.getByText(/Circuit Breaker/i).first())
        .first(),
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

    await page.getByRole("tab", { name: /team hierarchy/i }).click();
    await page.getByRole("button", { name: /show communication policy/i }).click();
    await expect(
      page.getByRole("region", { name: "Communication policy overlay controls" }),
    ).toBeVisible();
    await page
      .getByRole("combobox", { name: "Communication policy sender role" })
      .selectOption("worker");
    await expect(page.getByText("Allowed path").first()).toBeVisible();
    await expect(page.getByText("Denied path").first()).toBeVisible();
    await page
      .getByRole("button", { name: /hide communication policy/i })
      .click();

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

  test("system visualization exposes partial data failures with a retry path", async ({
    page,
  }) => {
    await page.route("**/api/system/permissions**", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "permissions fixture unavailable" }),
      });
    });

    await page.goto("/system-viz");
    await expect(
      page.getByRole("heading", { name: "System Visualization" }),
    ).toBeVisible();
    await expect(
      page.getByText("Some visualization data is stale or unavailable"),
    ).toBeVisible();
    await expect(page.getByText(/permissions failed to refresh/i)).toBeVisible();

    await page.getByRole("tab", { name: /permissions/i }).click();
    await expect(page.getByText("Permissions data unavailable")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Retry$/i }).first(),
    ).toBeVisible();
  });

  test("system visualization exposes an offline hierarchy state", async ({
    page,
  }) => {
    await page.route("**/api/system/hierarchy**", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "hierarchy fixture unavailable" }),
      });
    });

    await page.goto("/system-viz");
    await expect(page.getByText("Visualization unavailable")).toBeVisible();
    await expect(
      page.getByText(/Failed to load system hierarchy/i),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Retry$/i }).first(),
    ).toBeVisible();
  });

  test("PM integrations preserve conflicts and expose a stale refresh retry", async ({
    page,
  }) => {
    let requestCount = 0;
    await page.route("**/api/integrations/pm**", async (route) => {
      requestCount += 1;
      if (requestCount === 1) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            connections: [
              {
                id: "connection-e2e-001",
                display_name: "E2E YouTrack",
                provider_kind: "youtrack",
                base_url: "https://example.invalid",
                status: "ACTIVE",
              },
            ],
            conflicts: [{ id: "conflict-e2e-001", status: "OPEN" }],
            outbox: [],
            runs: [],
            lifecyclePlans: [],
          }),
        });
        return;
      }
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "integration fixture unavailable" }),
      });
    });

    await page.goto("/integrations");
    await expect(
      page.getByRole("heading", { name: "PM integrations" }),
    ).toBeVisible();
    await expect(page.getByText("E2E YouTrack")).toBeVisible();
    await expect(page.getByText("conflict-e2e-001")).toBeVisible();

    await page.getByRole("button", { name: "Refresh" }).click();
    await expect(
      page.getByText("Showing last known integration state"),
    ).toBeVisible();
    await expect(page.getByText(/latest integration refresh failed/i)).toBeVisible();
    await expect(page.getByText("conflict-e2e-001")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
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
    await expect(page.getByText(/state_transition:/i).first()).toBeVisible();

    await page.getByRole("tab", { name: "Cost" }).click();
    await expect(
      page.getByRole("heading", { name: "Cost And Usage" }),
    ).toBeVisible();
    await expect(page.getByText("Usage telemetry unavailable")).toHaveCount(0);
    for (const label of [
      "Total cost",
      "LLM calls",
      "Tool calls",
      "Tokens",
      "Failed calls",
    ]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }

    await page.getByTestId("project-tab-evidence").click();
    await expect(
      page.getByRole("heading", { name: "Completion Evidence" }),
    ).toBeVisible();
    await expect(page.getByText(/worker runs terminal/i)).toBeVisible();

    const projectId = new URL(page.url()).pathname.split("/").pop();
    if (!projectId) throw new Error("project id missing from detail URL");
    await page.route(`**/api/projects/${projectId}`, async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "project fixture unavailable" }),
      });
    });
    await page.getByRole("button", { name: "Refresh project data" }).click();
    await expect(
      page.getByText("Showing last known project state"),
    ).toBeVisible();
    await expect(page.getByText(/latest project refresh failed/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
    await expect(page.getByRole("heading", { name })).toBeVisible();
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
