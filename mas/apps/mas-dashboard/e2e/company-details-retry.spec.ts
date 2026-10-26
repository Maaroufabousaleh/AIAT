import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const COMPANY_ID = "10000000-0000-4000-8000-000000000071";
const company = {
  id: COMPANY_ID,
  slug: "company-detail-retry",
  name: "Company Detail Retry",
  description: "Detail retry fixture",
  status: "ACTIVE",
};
const manifest = {
  schema_version: "1",
  slug: company.slug,
  name: company.name,
  description: company.description,
  ceo_worker_id: "ceo",
  departments: [{ id: "executive", name: "Executive", chief_worker_id: "ceo", worker_ids: ["ceo"] }],
  worker_assignments: [{ worker_id: "ceo", department_id: "executive", status: "ACTIVE" }],
  budgets: {},
};

test("company detail failure offers a retry without presenting empty data as the hierarchy", async ({ page }) => {
  let detailRequests = 0;
  let retryRequested = false;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/companies" && request.method() === "GET") {
      await route.fulfill({ json: [company] });
      return;
    }
    if (path === "/api/workers" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === `/api/companies/${COMPANY_ID}` && request.method() === "GET") {
      detailRequests += 1;
      if (!retryRequested) {
        await route.fulfill({
          status: 503,
          json: { detail: "company detail read temporarily unavailable" },
        });
        return;
      }
      await route.fulfill({
        json: {
          company,
          manifest: { manifest_version: 1, status: "APPLIED", manifest_json: manifest },
          departments: [{ id: "executive", name: "Executive", chief_worker_id: "ceo" }],
          assignments: [{ worker_id: "ceo", department_id: "executive", status: "ACTIVE" }],
          budgets: [],
        },
      });
      return;
    }
    if (path === `/api/companies/${COMPANY_ID}/manifest/history` && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === `/api/companies/${COMPANY_ID}/budgets` && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/executive/reconciliation" && request.method() === "GET") {
      await route.fulfill({ json: { projects: { usage: {} }, budgets: { configured_count: 0 } } });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: `Unexpected request ${request.method()} ${path}` } });
  });

  await authenticate(page, "/companies");
  const main = page.getByRole("main", { name: "Companies" });
  const recovery = main.getByRole("region", { name: "Company detail recovery" });
  await expect(recovery).toBeVisible();
  await expect(recovery).toContainText("company detail read temporarily unavailable");
  await expect(recovery.getByRole("button", { name: "Retry company details" })).toBeVisible();
  await expect(main.getByRole("region", { name: "Company operating structure" })).toHaveCount(0);

  retryRequested = true;
  await recovery.getByRole("button", { name: "Retry company details" }).click();
  await expect(main.getByRole("region", { name: "Company operating structure" }).getByText("Executive", { exact: true })).toBeVisible();
  await expect(main.getByLabel("Manifest JSON")).toHaveValue(/company-detail-retry/);
  expect(detailRequests).toBeGreaterThan(1);
});
