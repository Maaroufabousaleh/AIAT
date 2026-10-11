import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const WORKER_RUN_ID = "30000000-0000-4000-8000-000000000103";

const PACKAGE_FIXTURE = {
  schema_version: "aiat.project-evidence-package.v1",
  project_id: "project-evidence-stale",
  policy_id: "software-delivery",
  policy_version: "1.0",
  status: "incomplete",
  completeness_score: 0.5,
  checks: [
    { name: "repository", required: true, passed: true },
    {
      name: "worker runs terminal",
      required: true,
      passed: false,
      reason: "worker run is still active",
    },
  ],
  categories: [
    {
      category: "repository",
      status: "present",
      required: true,
      item_count: 1,
      evidence_refs: ["repo-1"],
    },
    {
      category: "worker",
      status: "incomplete",
      required: true,
      item_count: 1,
      evidence_refs: ["worker-1"],
    },
  ],
  items: [
    {
      id: "repo-1",
      category: "repository",
      kind: "repository",
      status: "observed",
      source: "canonical",
    },
    {
      id: "42",
      category: "tests",
      kind: "test-report",
      status: "stored",
      source: "artifacts",
      checksum: "sha256:fixture-checksum",
    },
    {
      id: WORKER_RUN_ID,
      category: "workers",
      kind: "worker_run",
      status: "QUEUED",
      source: "worker_runs",
    },
    {
      id: "../unexpected-route",
      category: "workers",
      kind: "worker_run",
      status: "unavailable",
      source: "worker_runs",
    },
  ],
  notices: [],
};

test("project evidence opens a canonical artifact record from its item list", async ({
  page,
}) => {
  await page.route(
    "**/api/projects/project-evidence-open/evidence/package",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PACKAGE_FIXTURE,
          project_id: "project-evidence-open",
        }),
      });
    },
  );
  await page.route("**/api/evidence/artifact/42", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        schema_version: "aiat.evidence-detail.v1",
        kind: "artifact",
        id: "42",
        source: "project-artifacts",
        record: {
          kind: "test-report",
          status: "stored",
          checksum: "sha256:fixture-checksum",
        },
      }),
    });
  });

  await authenticate(page, "/projects/project-evidence-open/evidence");

  const artifactLink = page.getByRole("link", {
    name: "Open artifact evidence 42",
  });
  await expect(
    page.getByRole("link", { name: "Open artifact evidence repo-1" }),
  ).toHaveCount(0);
  await expect(artifactLink).toHaveAttribute(
    "href",
    "/evidence/artifact/42",
  );
  await expect(
    page.getByRole("link", { name: `Open WorkerRun evidence ${WORKER_RUN_ID}` }),
  ).toHaveAttribute("href", `/workers/runs/${WORKER_RUN_ID}`);
  await expect(
    page.getByRole("link", { name: "Open WorkerRun evidence ../unexpected-route" }),
  ).toHaveCount(0);
  await artifactLink.click();
  await expect(page).toHaveURL(/\/evidence\/artifact\/42$/);
  await expect(page.getByTestId("ceo-evidence-detail")).toContainText(
    "sha256:fixture-checksum",
  );
});

test("project evidence retains the last package through a failed refresh", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route(
    "**/api/projects/project-evidence-stale/evidence/package",
    async (route) => {
      requestCount += 1;
      if (requestCount === 2) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ error: "evidence fixture unavailable" }),
        });
        return;
      }
      const payload =
        requestCount >= 3
          ? { ...PACKAGE_FIXTURE, status: "complete", completeness_score: 1 }
          : PACKAGE_FIXTURE;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(payload),
      });
    },
  );

  await authenticate(page, "/projects/project-evidence-stale/evidence");
  await expect(
    page.getByRole("heading", { name: "Project evidence" }),
  ).toBeVisible();
  await expect(
    page.getByRole("main", { name: "Project evidence" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to project" })).toHaveCSS(
    "min-height",
    "44px",
  );
  await expect(
    page.getByRole("button", { name: "Refresh project evidence" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Required checks" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Category coverage" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Evidence items" }),
  ).toBeVisible();
  const evidenceTable = page.getByRole("table", { name: "Evidence items" });
  await expect(evidenceTable.locator("caption")).toHaveText(
    /included in the project evidence package/i,
  );
  for (const heading of ["ID", "Category", "Kind", "Status", "Source"]) {
    await expect(
      evidenceTable.getByRole("columnheader", { name: heading }),
    ).toHaveAttribute("scope", "col");
  }
  await expect(
    page.getByText("incomplete", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("software-delivery")).toBeVisible();

  await page.getByRole("button", { name: "Refresh project evidence" }).click();
  await expect(page.getByTestId("project-evidence-stale")).toBeVisible();
  await expect(
    page.getByText(/last successful package remains visible/i),
  ).toBeVisible();
  await expect(page.getByText("software-delivery")).toBeVisible();

  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByTestId("project-evidence-stale")).toHaveCount(0);
  await expect(
    page.getByText("complete", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("100% under software-delivery v1.0"),
  ).toBeVisible();
});

test("project evidence fails closed when the initial package read is denied", async ({
  page,
}) => {
  await page.route(
    "**/api/projects/project-evidence-denied/evidence/package",
    async (route) => {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "evidence access denied" }),
      });
    },
  );

  await authenticate(page, "/projects/project-evidence-denied/evidence");

  await expect(
    page.getByRole("region", { name: "Project evidence access status" }),
  ).toContainText("cannot be read while authorization is unavailable");
  await expect(
    page.getByRole("heading", { name: "Project evidence access denied" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Refresh project evidence" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Back to project" })).toBeVisible();
});

test("project evidence rejects a successful package response for another project", async ({
  page,
}) => {
  await page.route(
    "**/api/projects/project-evidence-mismatch/evidence/package",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...PACKAGE_FIXTURE, project_id: "different-project" }),
      });
    },
  );

  await authenticate(page, "/projects/project-evidence-mismatch/evidence");

  await expect(
    page.getByText("Evidence package project identity did not match the requested project"),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: `Open WorkerRun evidence ${WORKER_RUN_ID}` }),
  ).toHaveCount(0);
});

test("project evidence retains the last-known package after read denial", async ({
  page,
}) => {
  let packageReads = 0;
  await page.route(
    "**/api/projects/project-evidence-retained/evidence/package",
    async (route) => {
      packageReads += 1;
      if (packageReads > 1) {
        await route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({ detail: "evidence authorization expired" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PACKAGE_FIXTURE,
          project_id: "project-evidence-retained",
        }),
      });
    },
  );

  await authenticate(page, "/projects/project-evidence-retained/evidence");
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toBeVisible();
  await expect(page.getByText("software-delivery")).toBeVisible();

  await page.getByRole("button", { name: "Refresh project evidence" }).click();

  await expect(
    page.getByRole("region", { name: "Project evidence access status" }),
  ).toContainText("last-known evidence package");
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toBeVisible();
  await expect(page.getByText("software-delivery")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Refresh project evidence" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry" })).toHaveCount(0);
});

test("project evidence denies a second project without showing the first project's package", async ({
  page,
}) => {
  await page.route(
    "**/api/projects/project-evidence-open/evidence/package",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...PACKAGE_FIXTURE,
          project_id: "project-evidence-open",
        }),
      });
    },
  );
  await page.route(
    "**/api/projects/project-evidence-next/evidence/package",
    async (route) => {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ detail: "evidence access denied" }),
      });
    },
  );

  await authenticate(page, "/projects/project-evidence-open/evidence");
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: `Open WorkerRun evidence ${WORKER_RUN_ID}` }),
  ).toBeVisible();

  await page.goto("/projects/project-evidence-next/evidence");

  await expect(page).toHaveURL(/\/projects\/project-evidence-next\/evidence$/);
  await expect(
    page.getByRole("region", { name: "Project evidence access status" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Evidence package summary" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: `Open WorkerRun evidence ${WORKER_RUN_ID}` }),
  ).toHaveCount(0);
});
