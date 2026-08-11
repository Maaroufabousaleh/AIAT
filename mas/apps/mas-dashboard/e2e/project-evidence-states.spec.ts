import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PACKAGE_FIXTURE = {
  schema_version: "aiat.project-evidence-package.v1",
  project_id: "project-evidence-stale",
  policy_id: "software-delivery",
  policy_version: "1.0",
  status: "incomplete",
  completeness_score: 0.5,
  checks: [
    { name: "repository", required: true, passed: true },
    { name: "worker runs terminal", required: true, passed: false, reason: "worker run is still active" },
  ],
  categories: [
    { category: "repository", status: "present", required: true, item_count: 1, evidence_refs: ["repo-1"] },
    { category: "worker", status: "incomplete", required: true, item_count: 1, evidence_refs: ["worker-1"] },
  ],
  items: [
    { id: "repo-1", category: "repository", kind: "repository", status: "observed", source: "canonical" },
  ],
  notices: [],
};

test("project evidence retains the last package through a failed refresh", async ({ page }) => {
  let requestCount = 0;
  await page.route("**/api/projects/project-evidence-stale/evidence/package", async (route) => {
    requestCount += 1;
    if (requestCount === 2) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "evidence fixture unavailable" }),
      });
      return;
    }
    const payload = requestCount >= 3
      ? { ...PACKAGE_FIXTURE, status: "complete", completeness_score: 1 }
      : PACKAGE_FIXTURE;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });

  await authenticate(page, "/projects/project-evidence-stale/evidence");
  await expect(page.getByRole("heading", { name: "Project evidence" })).toBeVisible();
  await expect(page.getByText("incomplete", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("software-delivery")).toBeVisible();

  await page.getByRole("button", { name: "Refresh" }).click();
  await expect(page.getByTestId("project-evidence-stale")).toBeVisible();
  await expect(page.getByText(/last successful package remains visible/i)).toBeVisible();
  await expect(page.getByText("software-delivery")).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("project-evidence-stale")).toHaveCount(0);
  await expect(page.getByText("complete", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("100% under software-delivery v1.0")).toBeVisible();
});
