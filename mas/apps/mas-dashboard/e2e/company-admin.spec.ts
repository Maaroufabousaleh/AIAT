import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const EXISTING_ID = "company-existing-0001";
const CREATED_ID = "company-created-0002";

function existingCompany() {
  return {
    id: EXISTING_ID,
    slug: "aiat-default",
    name: "AIAT Default",
    description: "Main operator company",
    status: "ACTIVE",
    created_at: "2026-10-01T12:00:00Z",
  };
}

function createdCompany() {
  return {
    id: CREATED_ID,
    slug: "aiat-research",
    name: "AIAT Research",
    description: "Research work",
    status: "ACTIVE",
    created_at: "2026-10-07T10:00:00Z",
  };
}

function companyReadModel(company: ReturnType<typeof existingCompany> | ReturnType<typeof createdCompany>, manifest: Record<string, unknown> | null = null) {
  return {
    company,
    manifest: manifest ? {
      manifest_version: 1,
      digest: "digest-current",
      source: "e2e",
      status: "APPLIED",
      manifest_json: manifest,
      created_at: "2026-10-07T10:10:00Z",
    } : null,
    departments: company.id === EXISTING_ID ? [{ id: "dept_exec", name: "Executive", chief_worker_id: "ceo", status: "ACTIVE" }] : [],
    assignments: company.id === EXISTING_ID ? [{ worker_id: "ceo", department_id: "dept_exec", status: "ACTIVE" }] : [],
    budgets: company.id === EXISTING_ID ? [{ budget_key: "max_cost_usd", amount: 100 }] : [],
  };
}

test("company registry creates a company and applies a reviewed manifest through operator APIs", async ({ page }) => {
  let created = false;
  let applied = false;
  let applyAttempts = 0;
  const history: Array<Record<string, unknown>> = [];
  const observed: string[] = [];

  await page.route("**/api/companies**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    observed.push(`${method} ${path}`);

    if (path === "/api/companies" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(created ? [existingCompany(), createdCompany()] : [existingCompany()]) });
      return;
    }
    if (path === "/api/companies" && method === "POST") {
      created = true;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(createdCompany()) });
      return;
    }
    if (path === `/api/companies/${EXISTING_ID}` && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(companyReadModel(existingCompany(), { slug: "aiat-default", name: "AIAT Default" })) });
      return;
    }
    if (path === `/api/companies/${CREATED_ID}` && method === "GET") {
      const manifest = applied ? { schema_version: "1", slug: "aiat-research", name: "AIAT Research", ceo_worker_id: "ceo" } : null;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(companyReadModel(createdCompany(), manifest)) });
      return;
    }
    if (path.endsWith("/manifest/history") && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(path.includes(CREATED_ID) ? history : []) });
      return;
    }
    if (path.endsWith("/manifest/validate") && method === "POST") {
      const body = request.postDataJSON() as { manifest?: Record<string, unknown> };
      if (body.manifest?.description === "invalid for this fixture") {
        await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "ceo_worker_id must have a worker assignment" }) });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ valid: true, company_id: CREATED_ID, digest: "a".repeat(64), manifest: body.manifest, source: "dashboard_operator" }) });
      }
      return;
    }
    if (path.endsWith("/manifest/apply") && method === "POST") {
      applyAttempts += 1;
      if (applyAttempts === 1) {
        await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "company manifest revision changed" }) });
        return;
      }
      applied = true;
      history.splice(0, history.length, { id: "manifest-version-1", manifest_version: 1, digest: "a".repeat(64), status: "APPLIED", source: "dashboard_operator", compiled_by: "e2e", created_at: "2026-10-07T10:20:00Z" });
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(companyReadModel(createdCompany(), { schema_version: "1", slug: "aiat-research", name: "AIAT Research", ceo_worker_id: "ceo" })) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: `Unexpected company API: ${method} ${path}` }) });
  });

  await authenticate(page, "/companies");
  const main = page.getByRole("main", { name: "Companies" });
  await expect(main.getByRole("heading", { name: "Company registry" })).toBeVisible();
  await expect(main.getByText("AIAT Default", { exact: true }).first()).toBeVisible();
  await expect(main.getByRole("region", { name: "Company operating structure" }).getByText("Executive")).toBeVisible();
  await expect(main.getByRole("region", { name: "Company operating structure" }).getByText("max_cost_usd")).toBeVisible();

  await main.getByRole("button", { name: "New company" }).click();
  await main.getByLabel("Company name").fill("AIAT Research");
  await main.getByLabel("Slug").fill("aiat-research");
  await main.getByLabel("Description").fill("Research work");
  await main.getByRole("button", { name: "Create company" }).click();
  await expect(main.getByRole("heading", { name: "AIAT Research" })).toBeVisible();
  await expect(main.getByLabel("Manifest JSON")).toHaveValue(/aiat-research/);

  await main.getByText("Advanced manifest JSON").click();
  const editor = main.getByLabel("Manifest JSON");
  const starter = JSON.parse(await editor.inputValue()) as Record<string, unknown>;
  await editor.fill(JSON.stringify({ ...starter, description: "invalid for this fixture" }, null, 2));
  await main.getByRole("button", { name: "Validate manifest" }).click();
  await expect(main.getByRole("alert")).toContainText("Manifest validation failed (HTTP 422)");
  await expect(main.getByRole("alert")).toContainText("ceo_worker_id must have a worker assignment");

  await editor.fill(JSON.stringify({ ...starter, description: "Validated company manifest" }, null, 2));
  await main.getByRole("button", { name: "Validate manifest" }).click();
  await expect(main.getByText(`Validated digest: ${"a".repeat(64)}`)).toBeVisible();
  await main.getByRole("button", { name: "Apply validated manifest" }).click();
  await expect(main.getByRole("alert")).toContainText("Manifest apply conflict (HTTP 409)");
  await expect(main.getByRole("alert")).toContainText("company manifest revision changed");
  await expect(main.getByRole("button", { name: "Apply validated manifest" })).toBeDisabled();

  await main.getByRole("button", { name: "Validate manifest" }).click();
  await expect(main.getByText(`Validated digest: ${"a".repeat(64)}`)).toBeVisible();
  await main.getByRole("button", { name: "Apply validated manifest" }).click();
  await expect(main.getByRole("region", { name: "Manifest history" }).getByText("Version 1")).toBeVisible();
  await expect(main.getByRole("region", { name: "Manifest history" }).getByText("Active")).toBeVisible();

  expect(observed).toContain(`POST /api/companies/${CREATED_ID}/manifest/validate`);
  expect(observed).toContain(`POST /api/companies/${CREATED_ID}/manifest/apply`);
  expect(applyAttempts).toBe(2);
});
