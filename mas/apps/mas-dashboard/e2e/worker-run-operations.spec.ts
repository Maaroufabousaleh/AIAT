import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

const RUN_ID = "50000000-0000-4000-8000-000000000001";
const CHECKPOINT_ID = "50000000-0000-4000-8000-000000000002";
const MISSING_RUN_ID = "60000000-0000-4000-8000-000000000001";

type CancelMode = "terminal" | "accepted";

function runRecord(state: string, cancelRequestedAt: string | null) {
  return {
    id: RUN_ID,
    worker_id: "review-worker",
    task_type: "code.review",
    state,
    project_id: "50000000-0000-4000-8000-000000000003",
    flow_instance_id: "50000000-0000-4000-8000-000000000004",
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
  };
}

async function mockWorkerRunApi(page: Page, cancelMode: CancelMode = "terminal") {
  const observed = {
    state: "RUNNING",
    cancelRequestedAt: null as string | null,
    cancelPayload: null as Record<string, unknown> | null,
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
      await route.fulfill({ json: runRecord(observed.state, observed.cancelRequestedAt) });
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
      await route.fulfill({ json: [{ id: "artifact-1", name: "review-summary.json", size_bytes: 256 }] });
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
