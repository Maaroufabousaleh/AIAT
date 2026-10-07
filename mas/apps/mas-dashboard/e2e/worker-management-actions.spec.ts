import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const CANDIDATE_RECORD_ID = "10000000-0000-4000-8000-000000000001";
const LOCAL_WORKER_RECORD_ID = "10000000-0000-4000-8000-000000000002";

test("worker management keeps evaluation and status actions governed and reports failures", async ({
  page,
}) => {
  const workers = [
    {
      id: CANDIDATE_RECORD_ID,
      worker_id: "external-candidate",
      name: "External candidate",
      status: "INACTIVE",
      evaluation_status: "pending",
      source_repo: "https://example.invalid/workers/candidate",
      version_pin: "a".repeat(40),
      team_id: "dept_qa",
      adapter_entrypoint: "ExternalWorkerAgent",
      adapter_module: "workers.external_candidate",
      sandbox_profile: "sandboxed",
    },
    {
      id: LOCAL_WORKER_RECORD_ID,
      worker_id: "local-worker",
      name: "Local worker",
      status: "ACTIVE",
      evaluation_status: "approved",
      team_id: "dept_qa",
      adapter_entrypoint: "LocalWorkerAgent",
      adapter_module: "workers.local_worker",
      sandbox_profile: "trusted",
    },
  ];
  const evaluation = {
    id: "evaluation-001",
    verdict: "approved",
    overall_score: 0.94,
    risk_tier: "low",
    recommended_status: "INACTIVE",
    requires_human_approval: true,
    blocked_reasons: [],
    checks: { provenance: { passed: true, status: "PASSED" } },
  };
  const unexpectedApiRequests: string[] = [];
  let failLocalDeactivateOnce = true;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/workers" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(workers),
      });
      return;
    }
    if (path === "/api/runtimes" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ runtimes: [] }),
      });
      return;
    }
    if (
      path === `/api/workers/${CANDIDATE_RECORD_ID}/evaluations` &&
      method === "GET"
    ) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([evaluation]),
      });
      return;
    }
    if (
      path === `/api/workers/${CANDIDATE_RECORD_ID}/evaluate` &&
      method === "POST"
    ) {
      workers[0].evaluation_status = "approved";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(evaluation),
      });
      return;
    }
    if (path.endsWith("/status") && method === "PATCH") {
      const workerId = path.split("/").at(-2);
      const body = request.postDataJSON() as {
        action: string;
        new_status?: string;
      };

      if (workerId === CANDIDATE_RECORD_ID && body.action === "ACTIVATE") {
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: {
              message: "Worker activation requires current governed records",
              blockers: ["missing active immutable WorkerShell"],
            },
          }),
        });
        return;
      }

      if (
        workerId === LOCAL_WORKER_RECORD_ID &&
        body.action === "DEACTIVATE" &&
        failLocalDeactivateOnce
      ) {
        failLocalDeactivateOnce = false;
        await route.abort("failed");
        return;
      }

      const target = workers.find((worker) => worker.id === workerId);
      if (target && body.new_status) target.status = body.new_status;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ worker_id: workerId, status: target?.status }),
      });
      return;
    }

    unexpectedApiRequests.push(`${method} ${path}`);
    await route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ error: "Unexpected API request in worker fixture" }),
    });
  });

  await authenticate(page, "/workers");
  const hiringBoard = page.getByRole("main", { name: "Hiring Board" });
  const candidate = hiringBoard.getByRole("row", {
    name: "external-candidate worker row",
  });
  await expect(candidate.getByText("Inactive", { exact: true })).toBeVisible();
  await candidate.click();
  await expect(page.getByText("Blocked until approval")).toBeVisible();
  const activateCandidate = candidate.getByRole("button", {
    name: "Activate external-candidate",
  });
  await expect(activateCandidate).toBeDisabled();

  await candidate
    .getByRole("button", { name: "Evaluate external-candidate" })
    .click();
  await expect(page.getByText("Latest Evaluation")).toBeVisible();
  await expect(page.getByText("approved", { exact: true }).first()).toBeVisible();
  await expect(activateCandidate).toBeEnabled();

  await activateCandidate.click();
  await expect(
    page.getByRole("alert").filter({
      hasText: "Worker activation requires current governed records",
    }),
  ).toBeVisible();
  await expect(page.getByText("missing active immutable WorkerShell")).toBeVisible();
  await expect(candidate.getByText("Inactive", { exact: true })).toBeVisible();

  const localWorker = hiringBoard.getByRole("row", {
    name: "local-worker worker row",
  });
  await localWorker.click();
  const deactivate = localWorker.getByRole("button", {
    name: "Deactivate local-worker",
  });
  await deactivate.click();
  await expect(
    page.getByRole("alert").filter({
      hasText: "Network error while changing worker status",
    }),
  ).toBeVisible();
  await expect(localWorker.getByText("Active", { exact: true })).toBeVisible();

  await deactivate.click();
  await expect(localWorker.getByText("Inactive", { exact: true })).toBeVisible();
  expect(unexpectedApiRequests).toEqual([]);
});

test("worker configuration is prefilled, saved through PUT, and refreshed", async ({
  page,
}) => {
  const worker = {
    id: LOCAL_WORKER_RECORD_ID,
    worker_id: "local-worker",
    name: "Local worker",
    status: "ACTIVE",
    evaluation_status: "approved",
    team_id: "dept_qa",
    adapter_entrypoint: "LocalWorkerAgent",
    adapter_module: "workers.local_worker",
    sandbox_profile: "trusted",
  };
  let submittedBody: Record<string, unknown> | undefined;
  const unexpectedApiRequests: string[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/workers" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([worker]) });
      return;
    }
    if (path === "/api/runtimes" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ runtimes: [] }) });
      return;
    }
    if (path === `/api/workers/${LOCAL_WORKER_RECORD_ID}` && method === "PUT") {
      submittedBody = request.postDataJSON() as Record<string, unknown>;
      Object.assign(worker, submittedBody);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(worker) });
      return;
    }

    unexpectedApiRequests.push(`${method} ${path}`);
    await route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ error: "Unexpected API request in worker edit fixture" }),
    });
  });

  await authenticate(page, "/workers");
  const hiringBoard = page.getByRole("main", { name: "Hiring Board" });
  const localWorker = hiringBoard.getByRole("row", { name: "local-worker worker row" });
  await localWorker.getByRole("button", { name: "Edit local-worker" }).click();

  const editor = page.getByRole("region", { name: "Edit local-worker configuration" });
  await expect(editor.getByLabel("Team ID")).toHaveValue("dept_qa");
  await expect(editor.getByLabel("Adapter entrypoint")).toHaveValue("LocalWorkerAgent");
  await expect(editor.getByLabel("Adapter module")).toHaveValue("workers.local_worker");
  await editor.getByLabel("Team ID").fill("dept_eng");
  await editor.getByLabel("Adapter entrypoint").fill("UpdatedWorkerAgent");
  await editor.getByLabel("Adapter module").fill("workers.updated_worker");
  await editor.getByRole("button", { name: "Save configuration" }).click();

  await expect(localWorker.locator("td").nth(4)).toHaveText("dept_eng");
  await expect(page.getByText("UpdatedWorkerAgent", { exact: true })).toBeVisible();
  await expect(page.getByText("workers.updated_worker", { exact: true })).toBeVisible();
  expect(submittedBody).toEqual({
    team_id: "dept_eng",
    adapter_entrypoint: "UpdatedWorkerAgent",
    adapter_module: "workers.updated_worker",
  });
  expect(unexpectedApiRequests).toEqual([]);
});

test("worker configuration validation failures stay visible and leave the row unchanged", async ({
  page,
}) => {
  const worker = {
    id: LOCAL_WORKER_RECORD_ID,
    worker_id: "local-worker",
    name: "Local worker",
    status: "ACTIVE",
    evaluation_status: "approved",
    team_id: "dept_qa",
    adapter_entrypoint: "LocalWorkerAgent",
    adapter_module: "workers.local_worker",
    sandbox_profile: "trusted",
  };
  let putAttempts = 0;
  const submittedBodies: Record<string, unknown>[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();

    if (path === "/api/workers" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([worker]) });
      return;
    }
    if (path === "/api/runtimes" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ runtimes: [] }) });
      return;
    }
    if (path === `/api/workers/${LOCAL_WORKER_RECORD_ID}` && method === "PUT") {
      submittedBodies.push(request.postDataJSON() as Record<string, unknown>);
      putAttempts += 1;
      const status = putAttempts === 1 ? 409 : 422;
      const detail = putAttempts === 1
        ? "Team assignment conflicts with current worker grants"
        : "Adapter module is not valid for this worker";
      await route.fulfill({
        status,
        contentType: "application/json",
        body: JSON.stringify({ detail }),
      });
      return;
    }

    await route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ error: "Unexpected API request in worker edit fixture" }),
    });
  });

  await authenticate(page, "/workers");
  const hiringBoard = page.getByRole("main", { name: "Hiring Board" });
  const localWorker = hiringBoard.getByRole("row", { name: "local-worker worker row" });
  await localWorker.getByRole("button", { name: "Edit local-worker" }).click();

  const editor = page.getByRole("region", { name: "Edit local-worker configuration" });
  const teamCell = localWorker.locator("td").nth(4);
  await editor.getByLabel("Team ID").fill("dept_eng");
  await editor.getByRole("button", { name: "Save configuration" }).click();
  await expect(editor.getByRole("alert")).toContainText("Team assignment conflicts with current worker grants");
  await expect(teamCell).toHaveText("dept_qa");
  await expect(page.getByText("LocalWorkerAgent", { exact: true })).toBeVisible();
  await expect(page.getByText("UpdatedWorkerAgent", { exact: true })).toHaveCount(0);

  await editor.getByLabel("Adapter module").fill("invalid.module");
  await editor.getByRole("button", { name: "Save configuration" }).click();
  await expect(editor.getByRole("alert")).toContainText("Adapter module is not valid for this worker");
  await expect(teamCell).toHaveText("dept_qa");
  await expect(page.getByText("workers.local_worker", { exact: true })).toBeVisible();
  await expect(page.getByText("Configuration saved", { exact: true })).toHaveCount(0);
  expect(putAttempts).toBe(2);
  expect(submittedBodies).toEqual([
    {
      team_id: "dept_eng",
      adapter_entrypoint: "LocalWorkerAgent",
      adapter_module: "workers.local_worker",
    },
    {
      team_id: "dept_eng",
      adapter_entrypoint: "LocalWorkerAgent",
      adapter_module: "invalid.module",
    },
  ]);
});
