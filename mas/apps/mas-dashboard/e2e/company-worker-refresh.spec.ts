import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const COMPANY_ID = "company-worker-refresh-0001";

const company = {
  id: COMPANY_ID,
  slug: "aiat-worker-refresh",
  name: "AIAT Worker Refresh",
  description: "Company worker-choice retry fixture",
  status: "ACTIVE",
  created_at: "2026-10-09T10:00:00Z",
};

const manifest = {
  schema_version: "1",
  slug: company.slug,
  name: company.name,
  description: company.description,
  ceo_worker_id: "ceo",
  departments: [{ id: "executive", name: "Executive", chief_worker_id: "ceo", worker_ids: ["ceo"] }],
  worker_assignments: [{ worker_id: "ceo", department_id: "executive", status: "ACTIVE", permission_grants: ["company.manage"] }],
  budgets: {},
  policy: {},
};

test("company worker choices can recover after the worker registry is temporarily unavailable", async ({ page }) => {
  let workerRequests = 0;

  await page.route("**/api/workers", async (route) => {
    workerRequests += 1;
    if (workerRequests === 1) {
      await route.fulfill({ status: 503, json: { detail: "Worker registry temporarily unavailable." } });
      return;
    }
    await route.fulfill({
      json: [
        { worker_id: "ceo", name: "Chief Executive", status: "ACTIVE" },
        { worker_id: "worker-engineer", name: "Engineering Worker", status: "ACTIVE" },
      ],
    });
  });

  await page.route("**/api/companies**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/companies" && request.method() === "GET") {
      await route.fulfill({ json: [company] });
      return;
    }
    if (path === `/api/companies/${COMPANY_ID}` && request.method() === "GET") {
      await route.fulfill({
        json: {
          company,
          manifest: { manifest_version: 1, digest: "digest-current", status: "APPLIED", manifest_json: manifest },
          departments: manifest.departments,
          assignments: manifest.worker_assignments,
          budgets: [],
        },
      });
      return;
    }
    if (path === `/api/companies/${COMPANY_ID}/manifest/history` && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: `Unexpected company request: ${request.method()} ${path}` } });
  });

  await authenticate(page, "/companies");
  const main = page.getByRole("main", { name: "Companies" });
  await expect(main.getByText(/Registered worker choices could not be refreshed: Worker registry temporarily unavailable\./)).toBeVisible();

  await main.getByRole("button", { name: "Refresh workers" }).click();

  await expect(main.getByText("2 registered workers available for assignment.")).toBeVisible();
  await expect(main.getByLabel("New assignment worker").locator('option[value="worker-engineer"]')).toBeAttached();
  expect(workerRequests).toBe(2);
});
