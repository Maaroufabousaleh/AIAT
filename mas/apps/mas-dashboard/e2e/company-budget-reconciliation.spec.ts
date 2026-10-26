import { expect, test, type Page, type Route } from "@playwright/test";

import { authenticate } from "./auth";

const ALPHA_ID = "10000000-0000-4000-8000-000000000001";
const BETA_ID = "10000000-0000-4000-8000-000000000002";

const companies = [
  { id: ALPHA_ID, slug: "alpha-labs", name: "Alpha Labs", status: "ACTIVE", created_at: "2026-10-01T12:00:00Z" },
  { id: BETA_ID, slug: "beta-labs", name: "Beta Labs", status: "ACTIVE", created_at: "2026-10-02T12:00:00Z" },
];

function reconciliation({
  spend,
  modelCalls,
  toolCalls,
  configured,
  limit,
  used,
  available,
  activeReservations,
}: {
  spend: number;
  modelCalls: number;
  toolCalls: number;
  configured: number;
  limit: number;
  used: number;
  available: number;
  activeReservations: number;
}) {
  return {
    schema_version: "aiat.executive-reconciliation.v1",
    status: "reconciled",
    coverage: { project_count: 2, project_usage_count: 2, budget_count: configured, budget_reservation_count: activeReservations },
    projects: { usage: { total_cost_usd: spend, llm_calls: modelCalls, tool_calls: toolCalls, total_tokens: 300, failed_calls: 0 } },
    budgets: {
      configured_count: configured,
      limit_usd: limit,
      used_usd: used,
      available_usd: available,
      reservation_states: { RESERVED: activeReservations, COMMITTED: 0 },
      overages: [],
      reservation_reconciliation: { active_amount_usd: activeReservations ? 4.25 : 0, anomaly_count: 0 },
    },
  };
}

async function json(route: Route, status: number, payload: unknown): Promise<void> {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  });
}

async function installCompanyReadMocks(
  page: Page,
  onReconciliation: (companyId: string, route: Route) => Promise<void>,
  onBudgetBalances: (companyId: string, route: Route) => Promise<void> = async (_companyId, route) => {
    await json(route, 200, []);
  },
): Promise<void> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/companies" && request.method() === "GET") {
      await json(route, 200, companies);
      return;
    }
    if (path === "/api/workers" && request.method() === "GET") {
      await json(route, 200, []);
      return;
    }
    const budgetCompany = companies.find((row) => path === `/api/companies/${row.id}/budgets`);
    if (budgetCompany && request.method() === "GET") {
      await onBudgetBalances(budgetCompany.id, route);
      return;
    }
    const company = companies.find((row) => path === `/api/companies/${row.id}`);
    if (company && request.method() === "GET") {
      await json(route, 200, {
        company,
        manifest: {
          manifest_version: 1,
          digest: `digest-${company.slug}`,
          status: "APPLIED",
          manifest_json: {
            schema_version: "1",
            slug: company.slug,
            name: company.name,
            description: "Company budget fixture",
            ceo_worker_id: "ceo",
            departments: [{ id: "executive", name: "Executive", chief_worker_id: "ceo", worker_ids: ["ceo"] }],
            worker_assignments: [{ worker_id: "ceo", department_id: "executive", status: "ACTIVE" }],
            budgets: { max_cost_usd: 100 },
          },
        },
        departments: [{ id: "executive", name: "Executive", chief_worker_id: "ceo", status: "ACTIVE" }],
        assignments: [{ worker_id: "ceo", department_id: "executive", status: "ACTIVE" }],
        budgets: [{ budget_key: "max_cost_usd", amount: 100 }],
      });
      return;
    }
    if (companies.some((row) => path === `/api/companies/${row.id}/manifest/history`) && request.method() === "GET") {
      await json(route, 200, []);
      return;
    }
    if (path === "/api/executive/reconciliation" && request.method() === "GET") {
      const companyId = url.searchParams.get("company_id") ?? "";
      await onReconciliation(companyId, route);
      return;
    }

    await json(route, 200, []);
  });
}

test("company usage and budget balances follow the selected company without stale responses", async ({ page }) => {
  let releaseAlpha!: () => void;
  let markAlphaSeen!: () => void;
  const alphaSeen = new Promise<void>((resolve) => { markAlphaSeen = resolve; });
  const alphaGate = new Promise<void>((resolve) => { releaseAlpha = resolve; });
  const reconciliationRequests: string[] = [];

  await installCompanyReadMocks(page, async (companyId, route) => {
    reconciliationRequests.push(companyId);
    if (companyId === ALPHA_ID) {
      markAlphaSeen();
      await alphaGate;
      await json(route, 200, reconciliation({
        spend: 12,
        modelCalls: 4,
        toolCalls: 3,
        configured: 1,
        limit: 100,
        used: 20,
        available: 80,
        activeReservations: 1,
      }));
      return;
    }
    if (companyId === BETA_ID) {
      await json(route, 200, reconciliation({
        spend: 9.5,
        modelCalls: 2,
        toolCalls: 1,
        configured: 2,
        limit: 70,
        used: 25,
        available: 45,
        activeReservations: 2,
      }));
      return;
    }
    await json(route, 400, { detail: "company_id is required" });
  }, async (companyId, route) => {
    if (companyId === ALPHA_ID) {
      await json(route, 200, [
        { budget_key: "engineering", limit: "10.00", used: "15.00", available: "0.00", currency: "USD", period: "lifetime" },
        { budget_key: "research", limit: "200.00", used: "2.00", available: "198.00", currency: "USD", period: "monthly" },
      ]);
      return;
    }
    await json(route, 200, [
      { budget_key: "beta-project", limit: "70.00", used: "25.00", available: "45.00", currency: "USD", period: "lifetime" },
    ]);
  });

  await authenticate(page, "/companies");
  const registry = page.getByRole("region", { name: "Company registry" });
  const financials = page.getByRole("region", { name: "Company usage and budgets" });
  const budgets = financials.getByRole("table", { name: "Company budget balances" });
  await alphaSeen;
  await expect(financials).toHaveAttribute("aria-busy", "true");
  await expect(financials.getByRole("status")).toContainText("Loading company usage");
  const engineering = budgets.getByRole("row", { name: /engineering/ });
  await expect(engineering.getByRole("cell").nth(0)).toHaveText("$10.00");
  await expect(engineering.getByRole("cell").nth(1)).toHaveText("$15.00");
  await expect(engineering.getByRole("cell").nth(2)).toHaveText("$0.00");

  await registry.getByRole("button", { name: /Beta Labs/ }).click();
  const betaProject = budgets.getByRole("row", { name: /beta-project/ });
  await expect(betaProject).toBeVisible();
  await expect(betaProject.getByRole("cell").nth(0)).toHaveText("$70.00");
  await expect(betaProject.getByRole("cell").nth(1)).toHaveText("$25.00");
  await expect(betaProject.getByRole("cell").nth(2)).toHaveText("$45.00");
  await expect(budgets.getByRole("row", { name: /engineering/ })).toHaveCount(0);
  await expect(financials.getByText("$9.50", { exact: true })).toBeVisible();
  await expect(financials.getByLabel("Company usage summary").getByText("$70.00", { exact: true })).toBeVisible();
  await expect(financials.getByText("$25.00 · $45.00", { exact: true })).toBeVisible();
  await expect(financials.getByText("$4.25", { exact: true })).toBeVisible();

  releaseAlpha();
  await expect(financials.getByText("$12.00", { exact: true })).toHaveCount(0);
  expect(reconciliationRequests).toEqual([ALPHA_ID, BETA_ID]);
});

test("company budget reconciliation provides retry and a clear unconfigured-budget state", async ({ page }) => {
  let reconciliationRequests = 0;
  let budgetRequests = 0;
  await installCompanyReadMocks(page, async (_companyId, route) => {
    reconciliationRequests += 1;
    if (reconciliationRequests === 1) {
      await json(route, 503, { detail: "reconciliation temporarily unavailable" });
      return;
    }
    await json(route, 200, reconciliation({
      spend: 0,
      modelCalls: 0,
      toolCalls: 0,
      configured: 0,
      limit: 0,
      used: 0,
      available: 0,
      activeReservations: 0,
    }));
  }, async (_companyId, route) => {
    budgetRequests += 1;
    if (budgetRequests === 1) {
      await json(route, 503, { detail: "budget pools temporarily unavailable" });
      return;
    }
    await json(route, 200, []);
  });

  await authenticate(page, "/companies");
  const financials = page.getByRole("region", { name: "Company usage and budgets" });
  await expect(financials.getByText("reconciliation temporarily unavailable")).toBeVisible();
  await expect(financials.getByText("budget pools temporarily unavailable")).toBeVisible();
  await financials.getByRole("button", { name: "Retry", exact: true }).click();
  await financials.getByRole("button", { name: "Retry budget pools" }).click();
  await expect(financials.getByText("$0.00", { exact: true }).first()).toBeVisible();
  await expect(financials.getByText(/No company budget pools are configured/)).toBeVisible();
  await expect(financials.getByText("No configured company budget pools were returned.")).toBeVisible();
  expect(reconciliationRequests).toBe(2);
  expect(budgetRequests).toBe(2);
});
