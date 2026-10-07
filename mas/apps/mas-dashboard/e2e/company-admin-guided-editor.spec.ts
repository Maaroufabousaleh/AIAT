import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const COMPANY_ID = "company-guided-editor-0001";

const company = {
  id: COMPANY_ID,
  slug: "aiat-labs",
  name: "AIAT Labs",
  description: "Company editor fixture",
  status: "ACTIVE",
  created_at: "2026-10-07T12:00:00Z",
};

function baseManifest() {
  return {
    schema_version: "1",
    slug: company.slug,
    name: company.name,
    description: company.description,
    ceo_worker_id: "ceo",
    timezone: "UTC",
    departments: [{ id: "executive", name: "Executive", chief_worker_id: "ceo", worker_ids: ["ceo"] }],
    worker_assignments: [{
      worker_id: "ceo",
      department_id: "executive",
      status: "ACTIVE",
      tool_grants: [],
      permission_grants: ["company.manage", "company.delete", "approval.decide", "worker.read"],
      budget: {},
      approval_required: false,
      metadata: {},
    }],
    budgets: { max_cost_usd: 100, legacy_observation_limit: 4 },
    policy: {},
  };
}

test("company guided editor validates and applies department, worker, chief, and budget changes", async ({ page }) => {
  let currentManifest = baseManifest();
  let validationAttempts = 0;
  let applyAttempts = 0;
  const history: Array<Record<string, unknown>> = [];
  const validatedManifests: Array<Record<string, unknown>> = [];

  await page.route("**/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        { worker_id: "ceo", name: "Chief Executive", status: "ACTIVE" },
        { worker_id: "worker-engineer", name: "Engineering Worker", status: "ACTIVE" },
      ]),
    });
  });

  await page.route("**/api/companies**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/companies" && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([company]) });
      return;
    }
    if (path === `/api/companies/${COMPANY_ID}` && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          company,
          manifest: { manifest_version: history.length ? 2 : 1, digest: "digest-current", status: "APPLIED", manifest_json: currentManifest },
          departments: currentManifest.departments.map((department) => ({ ...department, status: "ACTIVE" })),
          assignments: currentManifest.worker_assignments,
          budgets: Object.entries(currentManifest.budgets).map(([budget_key, amount]) => ({ budget_key, amount })),
        }),
      });
      return;
    }
    if (path.endsWith("/manifest/history") && request.method() === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(history) });
      return;
    }
    if (path.endsWith("/manifest/validate") && request.method() === "POST") {
      validationAttempts += 1;
      const body = request.postDataJSON() as { manifest?: Record<string, unknown> };
      const manifest = body.manifest ?? {};
      validatedManifests.push(manifest);
      const departments = Array.isArray(manifest.departments) ? manifest.departments as Array<Record<string, unknown>> : [];
      const ids = departments.map((department) => department.id);
      if (new Set(ids).size !== ids.length) {
        await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "department IDs must be unique" }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ valid: true, company_id: COMPANY_ID, digest: `digest-${validationAttempts}`, manifest, source: "dashboard_operator" }),
      });
      return;
    }
    if (path.endsWith("/manifest/apply") && request.method() === "POST") {
      applyAttempts += 1;
      if (applyAttempts === 1) {
        await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "company manifest revision changed" }) });
        return;
      }
      currentManifest = (request.postDataJSON() as { manifest: typeof currentManifest }).manifest;
      history.splice(0, history.length, {
        id: "manifest-version-2",
        manifest_version: 2,
        digest: "digest-applied",
        status: "APPLIED",
        source: "dashboard_operator",
        compiled_by: "e2e-operator",
        created_at: "2026-10-07T12:10:00Z",
      });
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ company_id: COMPANY_ID, manifest_version: 2 }) });
      return;
    }
    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: `Unexpected company API: ${request.method()} ${path}` }) });
  });

  await authenticate(page, "/companies");
  const main = page.getByRole("main", { name: "Companies" });
  await expect(main.getByRole("heading", { name: "AIAT Labs" })).toBeVisible();
  await expect(main.getByText("2 registered workers available for assignment.")).toBeVisible();
  await expect(main.getByRole("note")).toContainText("legacy_observation_limit");
  await expect(main.getByText("max_cost_usd sets the company budget pool. A cost-producing run needs a positive per-run budget (budget_usd or budget.max_cost_usd) to reserve against that pool. A per-run budget of 0 behaves like an omitted cost budget; a company pool of 0 alone does not universally block dispatch when no cost budget is requested.")).toBeVisible();

  await main.getByRole("button", { name: "Add budget" }).click();
  await expect(main.getByLabel("Budget amount max_concurrent_runs")).toHaveValue("1");
  await expect(main.getByRole("button", { name: "Add budget" })).toBeDisabled();
  await expect(main.getByText("Both supported company budget keys are configured.")).toBeVisible();
  await main.getByLabel("Budget amount max_concurrent_runs").fill("2.5");
  await main.getByLabel("Budget amount max_concurrent_runs").press("Tab");
  await expect(main.getByRole("alert")).toContainText("max_concurrent_runs must be a whole number");
  await main.getByLabel("Budget amount max_concurrent_runs").fill("3");
  await main.getByLabel("Budget amount max_concurrent_runs").press("Tab");

  await main.getByRole("button", { name: "Add department" }).click();
  await main.getByLabel("Department ID 2").fill("temporary");
  await main.getByLabel("Department name 2").fill("Temporary");
  await main.getByLabel("New assignment department").selectOption("temporary");
  await main.getByRole("button", { name: "Remove department Temporary" }).click();
  await expect(main.getByLabel("New assignment department")).toHaveValue("executive");

  await main.getByRole("button", { name: "Add department" }).click();
  await main.getByLabel("Department ID 2").fill("executive");
  await main.getByRole("button", { name: "Validate manifest" }).click();
  await expect(main.getByRole("alert")).toContainText("Manifest validation failed (HTTP 422)");
  await expect(main.getByRole("alert")).toContainText("department IDs must be unique");

  await main.getByLabel("Department ID 2").fill("engineering");
  await main.getByLabel("Department name 2").fill("Engineering");
  await main.getByLabel("New assignment worker").selectOption("worker-engineer");
  await main.getByLabel("New assignment department").selectOption("engineering");
  await main.getByRole("button", { name: "Add assignment" }).click();
  await main.getByLabel("Department ID 2").fill("engineering_team");
  const ceoWorker = main.getByLabel("CEO worker");
  await expect(ceoWorker.locator('option[value="worker-engineer"]')).toHaveCount(0);
  await main.getByLabel("Chief for Engineering").selectOption("worker-engineer");
  await expect(ceoWorker.locator('option[value="worker-engineer"]')).toHaveCount(1);
  await main.getByText("Advanced manifest JSON").click();
  const advancedEditor = main.getByLabel("Manifest JSON");
  const grantDraft = JSON.parse(await advancedEditor.inputValue()) as {
    worker_assignments: Array<{ worker_id: string; permission_grants: string[] }>;
  };
  const engineerAssignment = grantDraft.worker_assignments.find((assignment) => assignment.worker_id === "worker-engineer");
  expect(engineerAssignment).toBeDefined();
  engineerAssignment!.permission_grants = ["project.read"];
  await advancedEditor.fill(JSON.stringify(grantDraft, null, 2));
  await main.getByText("Advanced manifest JSON").click();

  await ceoWorker.selectOption("worker-engineer");
  await expect(main.getByText(/CEO authority is now drafted for worker-engineer/)).toBeVisible();
  await expect(main.getByText(/validate and apply the manifest to save this change/)).toBeVisible();
  await expect(main.getByRole("button", { name: "Apply validated manifest" })).toBeDisabled();

  await main.getByLabel("Budget amount max_cost_usd").fill("250");
  await main.getByLabel("Budget amount max_cost_usd").press("Tab");

  await main.getByRole("button", { name: "Validate manifest" }).click();
  await expect(main.getByText("Validated digest: digest-2")).toBeVisible();
  const validated = validatedManifests.at(-1)!;
  expect(validated.departments).toEqual([
    { id: "executive", name: "Executive", chief_worker_id: "ceo", worker_ids: ["ceo"] },
    { id: "engineering_team", name: "Engineering", chief_worker_id: "worker-engineer", worker_ids: ["worker-engineer"] },
  ]);
  expect(validated.worker_assignments).toEqual(expect.arrayContaining([
    expect.objectContaining({ worker_id: "worker-engineer", department_id: "engineering_team", status: "ACTIVE" }),
  ]));
  expect(validated.ceo_worker_id).toBe("worker-engineer");
  const assignments = validated.worker_assignments as Array<{ worker_id: string; permission_grants: string[] }>;
  expect(assignments.find((assignment) => assignment.worker_id === "ceo")?.permission_grants).toEqual(["worker.read"]);
  expect(assignments.find((assignment) => assignment.worker_id === "worker-engineer")?.permission_grants).toEqual([
    "project.read", "company.manage", "company.delete", "approval.decide",
  ]);
  expect(validated.budgets).toEqual({ max_cost_usd: 250, max_concurrent_runs: 3, legacy_observation_limit: 4 });

  await main.getByRole("button", { name: "Apply validated manifest" }).click();
  await expect(main.getByRole("alert")).toContainText("Manifest apply conflict (HTTP 409)");
  await expect(main.getByRole("button", { name: "Apply validated manifest" })).toBeDisabled();

  await main.getByRole("button", { name: "Validate manifest" }).click();
  await main.getByRole("button", { name: "Apply validated manifest" }).click();
  await expect(main.getByRole("region", { name: "Manifest history" }).getByText("Version 2")).toBeVisible();
  await expect(main.getByRole("region", { name: "Company operating structure" }).getByText("Engineering", { exact: true })).toBeVisible();
  await expect(main.getByRole("region", { name: "Company operating structure" }).getByText("250")).toBeVisible();
  expect(applyAttempts).toBe(2);
  expect(validationAttempts).toBe(3);
});
