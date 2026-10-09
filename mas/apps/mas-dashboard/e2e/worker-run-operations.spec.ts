import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

const RUN_ID = "50000000-0000-4000-8000-000000000001";
const CHECKPOINT_ID = "50000000-0000-4000-8000-000000000002";
const RETRY_RUN_ID = "50000000-0000-4000-8000-000000000007";
const MISSING_RUN_ID = "60000000-0000-4000-8000-000000000001";

type CancelMode = "terminal" | "accepted";

function runRecord(state: string, cancelRequestedAt: string | null, retryRunId: string | null = null, flowBound = false) {
  return {
    id: RUN_ID,
    worker_id: "review-worker",
    task_type: "code.review",
    state,
    project_id: "50000000-0000-4000-8000-000000000003",
    flow_id: flowBound ? "50000000-0000-4000-8000-000000000008" : null,
    flow_instance_id: flowBound ? "50000000-0000-4000-8000-000000000004" : null,
    flow_node_execution_id: flowBound ? "50000000-0000-4000-8000-000000000009" : null,
    adapter_id: "50000000-0000-4000-8000-000000000005",
    attempt_count: 1,
    model_resolution_snapshot_id: "50000000-0000-4000-8000-000000000006",
    created_at: "2026-10-07T12:00:00Z",
    started_at: "2026-10-07T12:01:00Z",
    completed_at: state === "CANCELLED" ? "2026-10-07T12:02:00Z" : null,
    cancel_requested_at: cancelRequestedAt,
    request_json: { task_type: "code.review", task_input: { change: "review fixture" } },
    result_json: null,
    error_json: null,
    retry_of_run_id: null,
    retry_run_id: retryRunId,
    retry_run_state: retryRunId ? "QUEUED" : null,
  };
}

async function mockWorkerRunApi(page: Page, cancelMode: CancelMode = "terminal", initialState = "RUNNING", flowBound = false) {
  const observed = {
    state: initialState,
    flowBound,
    cancelRequestedAt: null as string | null,
    cancelPayload: null as Record<string, unknown> | null,
    retryPayload: null as Record<string, unknown> | null,
    retryRunId: null as string | null,
    retryConflict: false,
    denyRunReads: false,
    denyActions: false,
    unexpected: [] as string[],
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;

    if (path === "/api/governance/model-profiles" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if ((path === "/api/projects" || path === "/api/workers") && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/governance/model-profiles/catalogue" && request.method() === "GET") {
      await route.fulfill({
        json: {
          schema_version: "worker-run-e2e-v1",
          registry_model_count: 0,
          profile_count: 0,
          profile_version_count: 0,
          covered_profile_version_count: 0,
          profile_pending_model_count: 0,
          entries: [],
        },
      });
      return;
    }
    if (path === "/api/governance/stewards" && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === "/api/governance/runs" && request.method() === "GET") {
      await route.fulfill({
        json: [{
          id: RUN_ID,
          worker_id: "review-worker",
          task_type: "code.review",
          state: observed.state,
          model_resolution_snapshot_id: "50000000-0000-4000-8000-000000000006",
        }],
      });
      return;
    }
    if (path === "/api/governance/runs/" + MISSING_RUN_ID && request.method() === "GET") {
      await route.fulfill({ status: 404, json: { detail: "Worker Run not found." } });
      return;
    }
    if (path.startsWith("/api/governance/runs/" + MISSING_RUN_ID + "/") && request.method() === "GET") {
      await route.fulfill({ json: [] });
      return;
    }
    if (path.startsWith("/api/governance/runs/" + RUN_ID) && request.method() === "GET" && observed.denyRunReads) {
      await route.fulfill({ status: 403, json: { detail: "Worker Run access was revoked." } });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID && request.method() === "GET") {
      await route.fulfill({ json: runRecord(observed.state, observed.cancelRequestedAt, observed.retryRunId, observed.flowBound) });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/events" && request.method() === "GET") {
      await route.fulfill({ json: [{ id: "event-1", event_type: "progress", created_at: "2026-10-07T12:01:10Z", payload: { phase: "review" } }] });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/transitions" && request.method() === "GET") {
      await route.fulfill({ json: [{ id: "transition-1", from_state: "QUEUED", to_state: "RUNNING", actor: "worker-host", reason: "claimed" }] });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/artifacts" && request.method() === "GET") {
      await route.fulfill({ json: [{
        id: "artifact-1",
        run_id: RUN_ID,
        artifact_id: 42,
        kind: "file",
        uri: "workspace/artifacts/review-summary.json",
        sha256: "a".repeat(64),
        size_bytes: 256,
        metadata: {
          artifact_name: "review-summary.json",
          artifact_kind: "file",
          mime_type: "application/json",
          worker_run_id: RUN_ID,
        },
      }] });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/usage" && request.method() === "GET") {
      await route.fulfill({ json: [{ id: "usage-1", exact_model_id: "fixture/review-model", total_tokens: 42, cost_usd: 0.02 }] });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/checkpoints" && request.method() === "GET") {
      await route.fulfill({ json: [{ id: CHECKPOINT_ID, sequence: 3, resumable: true, created_at: "2026-10-07T12:01:30Z" }] });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/pause" && request.method() === "POST") {
      await route.fulfill({
        status: 409,
        json: { detail: { code: "CHECKPOINT_RESTART_ONLY", message: "The certified adapter supports restart from a safe point, not in-place pause." } },
      });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/resume" && request.method() === "POST") {
      await route.fulfill({
        status: 409,
        json: { detail: { code: "CHECKPOINT_RESTART_ONLY", message: "The certified adapter supports restart from a safe point, not in-place resume." } },
      });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/cancel" && request.method() === "POST") {
      observed.cancelPayload = request.postDataJSON() as Record<string, unknown>;
      if (observed.denyActions) {
        await route.fulfill({ status: 403, json: { detail: "Worker Run action access was revoked." } });
        return;
      }
      if (cancelMode === "terminal") {
        observed.state = "CANCELLED";
      } else {
        observed.cancelRequestedAt = "2026-10-07T12:02:00Z";
      }
      await route.fulfill({ json: runRecord(observed.state, observed.cancelRequestedAt) });
      return;
    }
    if (path === "/api/governance/runs/" + RUN_ID + "/retry" && request.method() === "POST") {
      observed.retryPayload = request.postDataJSON() as Record<string, unknown>;
      if (observed.denyActions) {
        await route.fulfill({ status: 403, json: { detail: "Worker Run action access was revoked." } });
        return;
      }
      if (observed.retryConflict) {
        observed.retryRunId = RETRY_RUN_ID;
        await route.fulfill({
          status: 409,
          json: { detail: { code: "RUN_RETRY_ALREADY_CREATED", message: "This Worker Run already has a canonical retry. Open that run to continue recovery." } },
        });
        return;
      }
      observed.retryRunId = RETRY_RUN_ID;
      await route.fulfill({
        status: 202,
        json: { run_id: RETRY_RUN_ID, state: "QUEUED", retry_of_run_id: RUN_ID, dispatch_mode: "queued" },
      });
      return;
    }

    observed.unexpected.push(request.method() + " " + path);
    await route.fulfill({ status: 501, json: { detail: "Unexpected API request in Worker Run fixture." } });
  });

  return observed;
}

test("operator opens a Worker Run, inspects durable evidence, and sees governed action results", async ({ page }) => {
  const observed = await mockWorkerRunApi(page);
  await authenticate(page, "/governance");

  const runLink = page.getByRole("link", { name: "Open Worker Run " + RUN_ID });
  await expect(runLink).toBeVisible();
  await runLink.click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText(RUN_ID);
  await expect(runPage.getByTestId("run-identity")).toContainText("RUNNING");
  await expect(runPage.getByTestId("run-identity")).toContainText("review-worker");
  await expect(runPage.getByTestId("run-events")).toContainText("progress");
  await expect(runPage.getByTestId("run-transitions")).toContainText("QUEUED");
  await expect(runPage.getByTestId("run-artifacts")).toContainText("review-summary.json");
  await expect(
    runPage.getByTestId("run-artifacts").getByRole("listitem").first().getByText("review-summary.json", { exact: true }),
  ).toBeVisible();
  await expect(runPage.getByTestId("run-usage")).toContainText("fixture/review-model");
  await expect(runPage.getByTestId("run-checkpoints")).toContainText("resumable");

  await runPage.getByRole("button", { name: "Pause Worker Run" }).click();
  await expect(runPage.getByRole("alert")).toContainText("supports restart from a safe point, not in-place pause");

  await runPage.getByRole("button", { name: "Refresh" }).click();
  await expect(runPage.getByTestId("run-identity")).toContainText("RUNNING");

  observed.state = "PAUSED";
  await runPage.getByRole("button", { name: "Refresh" }).click();
  await expect(runPage.getByTestId("run-identity")).toContainText("PAUSED");
  await runPage.getByRole("button", { name: "Resume Worker Run" }).click();
  await expect(runPage.getByRole("alert")).toContainText("supports restart from a safe point, not in-place resume");

  page.once("dialog", (dialog) => dialog.accept());
  await runPage.getByRole("button", { name: "Cancel Worker Run" }).click();
  await expect(runPage.getByTestId("run-identity")).toContainText("CANCELLED");
  await expect(runPage.getByRole("status")).toContainText("Cancellation confirmed");
  expect(observed.cancelPayload).toMatchObject({
    reason: "Operator requested cancellation",
    force: false,
  });
  expect(observed.cancelPayload).not.toHaveProperty("requested_by");
  await expect(runPage.getByRole("button", { name: "Cancel Worker Run" })).toHaveCount(0);
  expect(observed.unexpected).toEqual([]);
});

test("accepted cancellation stays explicitly unconfirmed while the runtime is still running", async ({ page }) => {
  await mockWorkerRunApi(page, "accepted");
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText("RUNNING");
  page.once("dialog", (dialog) => dialog.accept());
  await runPage.getByRole("button", { name: "Cancel Worker Run" }).click();

  await expect(runPage.getByTestId("run-identity")).toContainText("RUNNING");
  await expect(runPage.getByRole("status")).toContainText("Cancellation was accepted, but runtime termination is not confirmed");
});

test("opening a second missing Worker Run never shows the previous run's records", async ({ page }) => {
  await mockWorkerRunApi(page);
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const firstRunPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(firstRunPage.getByTestId("run-identity")).toContainText(RUN_ID);
  await expect(firstRunPage.getByTestId("run-events")).toContainText("progress");

  await page.goto("/workers/runs/" + MISSING_RUN_ID);

  const secondRunPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(secondRunPage.getByRole("alert")).toContainText("Worker Run unavailable");
  await expect(secondRunPage.getByTestId("run-identity")).toHaveCount(0);
  await expect(secondRunPage.getByTestId("run-events")).toHaveCount(0);
  await expect(secondRunPage).not.toContainText(RUN_ID);
  await expect(secondRunPage).not.toContainText("review-worker");
});

test("access revocation clears previously loaded Worker Run evidence", async ({ page }) => {
  const observed = await mockWorkerRunApi(page);
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText(RUN_ID);
  await expect(runPage.getByTestId("run-artifacts")).toContainText("review-summary.json");

  observed.denyRunReads = true;
  await runPage.getByRole("button", { name: "Refresh" }).click();

  await expect(runPage.getByRole("region", { name: "Worker Run access status" })).toContainText("Run data and operator actions are hidden");
  await expect(runPage.getByTestId("run-identity")).toHaveCount(0);
  await expect(runPage.getByTestId("run-artifacts")).toHaveCount(0);
  await expect(runPage).not.toContainText(RUN_ID);
  await expect(runPage).not.toContainText("review-summary.json");
  await expect(runPage.getByRole("button", { name: "Cancel Worker Run" })).toHaveCount(0);
});

test("action access revocation clears previously loaded Worker Run evidence", async ({ page }) => {
  const observed = await mockWorkerRunApi(page);
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText(RUN_ID);
  await expect(runPage.getByTestId("run-artifacts")).toContainText("review-summary.json");

  observed.denyActions = true;
  page.once("dialog", (dialog) => dialog.accept());
  await runPage.getByRole("button", { name: "Cancel Worker Run" }).click();

  await expect(runPage.getByRole("region", { name: "Worker Run access status" })).toContainText("Run data and operator actions are hidden");
  await expect(runPage.getByTestId("run-identity")).toHaveCount(0);
  await expect(runPage.getByTestId("run-artifacts")).toHaveCount(0);
  await expect(runPage).not.toContainText(RUN_ID);
  await expect(runPage).not.toContainText("review-summary.json");
  await expect(runPage.getByRole("button", { name: "Cancel Worker Run" })).toHaveCount(0);
});

test("operator retries a failed run through a new linked Worker Run", async ({ page }) => {
  const observed = await mockWorkerRunApi(page, "terminal", "FAILED");
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText("FAILED");
  await expect(runPage.getByRole("button", { name: "Retry Worker Run" })).toBeVisible();
  await expect(runPage.getByRole("button", { name: "Cancel Worker Run" })).toHaveCount(0);

  page.once("dialog", (dialog) => dialog.accept());
  await runPage.getByRole("button", { name: "Retry Worker Run" }).click();

  await expect(runPage.getByRole("status")).toContainText("Retry created as QUEUED");
  const retryLink = runPage.getByRole("link", { name: RETRY_RUN_ID });
  await expect(retryLink).toBeVisible();
  await expect(retryLink).toHaveAttribute("href", "/workers/runs/" + RETRY_RUN_ID);
  await expect(runPage.getByTestId("run-identity")).toContainText("FAILED");
  await expect(runPage.getByRole("button", { name: "Retry Worker Run" })).toHaveCount(0);
  expect(observed.retryPayload).toMatchObject({ reason: "Operator requested retry" });
  expect(observed.retryPayload?.idempotency_key).toMatch(/^[0-9a-f-]{36}$/i);
  expect(observed.unexpected).toEqual([]);
});

test("flow-bound failed runs direct operators to flow controls instead of manual retry", async ({ page }) => {
  const observed = await mockWorkerRunApi(page, "terminal", "FAILED", true);
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  await expect(runPage.getByTestId("run-identity")).toContainText("FAILED");
  await expect(runPage.getByTestId("run-actions")).toContainText("Retry it from the flow controls");
  await expect(runPage.getByRole("button", { name: "Retry Worker Run" })).toHaveCount(0);
  await expect(runPage.getByLabel("Retry reason")).toHaveCount(0);
  expect(observed.unexpected).toEqual([]);
});

test("retry errors are visible and successful or cancelled runs cannot be retried", async ({ page }) => {
  const observed = await mockWorkerRunApi(page, "terminal", "TIMED_OUT");
  observed.retryConflict = true;
  await authenticate(page, "/governance");
  await page.getByRole("link", { name: "Open Worker Run " + RUN_ID }).click();

  const runPage = page.getByRole("main", { name: "Worker Run detail" });
  page.once("dialog", (dialog) => dialog.accept());
  await runPage.getByRole("button", { name: "Retry Worker Run" }).click();
  await expect(runPage.getByRole("alert")).toContainText("already has a canonical retry");
  await expect(runPage.getByRole("link", { name: RETRY_RUN_ID })).toHaveAttribute("href", "/workers/runs/" + RETRY_RUN_ID);
  await expect(runPage.getByRole("button", { name: "Retry Worker Run" })).toHaveCount(0);

  for (const state of ["SUCCEEDED", "CANCELLED"]) {
    observed.state = state;
    await runPage.getByRole("button", { name: "Refresh" }).click();
    await expect(runPage.getByTestId("run-identity")).toContainText(state);
    await expect(runPage.getByRole("button", { name: "Retry Worker Run" })).toHaveCount(0);
    await expect(runPage.getByRole("button", { name: "Cancel Worker Run" })).toHaveCount(0);
  }
});
