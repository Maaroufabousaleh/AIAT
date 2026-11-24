import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const CONNECTION_ID = "11111111-1111-4111-8111-111111111111";
const BINDING_ID = "22222222-2222-4222-8222-222222222222";
const CONFLICT_ID = "33333333-3333-4333-8333-333333333333";
const OUTBOX_ID = "44444444-4444-4444-8444-444444444444";
const RUN_ID = "55555555-5555-4555-8555-555555555555";
const LIFECYCLE_PLAN_ID = "66666666-6666-4666-8666-666666666666";
const CANARY_PLAN_ID = "77777777-7777-4777-8777-777777777777";
const DIGEST = "a".repeat(64);

test("PM integrations expose mapping, canary, reconciliation, and conflict disposition details", async ({ page }) => {
  let mainReadCount = 0;
  let conflictResolved = false;
  let resolutionRequest: Record<string, unknown> | null = null;

  await page.route("**/api/projects?limit=1000", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
  });
  await page.route("**/api/integrations/pm**", async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname.endsWith(`/conflicts/${CONFLICT_ID}/resolve`)) {
      resolutionRequest = route.request().postDataJSON() as Record<string, unknown>;
      conflictResolved = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: CONFLICT_ID,
          status: "RESOLVED",
          resolution: resolutionRequest.resolution,
        }),
      });
      return;
    }
    if (pathname.endsWith(`/inbound-canaries/${CANARY_PLAN_ID}`)) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: "PLANNED",
          digest_valid: true,
          accepted_command_count: 0,
          plan: {
            plan_id: CANARY_PLAN_ID,
            connection_id: CONNECTION_ID,
            binding_id: BINDING_ID,
            current_priority: "medium",
            target_priority: "high",
            expires_at: "2026-10-08T10:00:00Z",
            gate_results: { doctor_ready: true, reconciliation_status: "SUCCEEDED" },
            operations: [{ operation: "accept_one_inbound_priority_command" }],
            rollback_operations: [{ operation: "disarm_inbound_canary" }],
          },
        }),
      });
      return;
    }
    if (pathname === "/api/integrations/pm") {
      mainReadCount += 1;
      if (mainReadCount === 2) {
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "fixture unavailable" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          connections: [{
            id: CONNECTION_ID,
            display_name: "Local YouTrack fixture",
            provider_kind: "youtrack",
            base_url: "https://example.invalid",
            status: "SHADOW",
            revision: 4,
          }],
          conflicts: conflictResolved ? [] : [{
            id: CONFLICT_ID,
            status: "OPEN",
            reason: "External issue revision differs from the canonical snapshot",
            object_type: "work_item",
            aiat_object_id: "canonical-issue-001",
            external_id: "YT-17",
            canonical_snapshot: { revision: 3 },
            external_snapshot: { revision: 4 },
          }],
          outbox: [{
            id: OUTBOX_ID,
            status: "PENDING",
            operation: "project.updated",
            aggregate_type: "project",
            aggregate_id: "project-001",
            attempts: 2,
            created_at: "2026-10-07T12:00:00Z",
            next_attempt_at: "2026-10-07T12:05:00Z",
          }],
          runs: [{
            id: RUN_ID,
            connection_id: CONNECTION_ID,
            binding_id: BINDING_ID,
            status: "SUCCEEDED",
            counts: { created: 0, updated: 2, drift: 0, conflicts: 0 },
            created_at: "2026-10-07T11:00:00Z",
            completed_at: "2026-10-07T11:01:00Z",
          }],
          lifecyclePlans: [{
            id: LIFECYCLE_PLAN_ID,
            status: "PLANNED",
            plan_digest: DIGEST,
            plan: {
              plan_id: LIFECYCLE_PLAN_ID,
              target_type: "pm_binding",
              connection_id: CONNECTION_ID,
              binding_id: BINDING_ID,
              gate_results: {
                active_binding_scope: {
                  trusted_actor_mappings: [{
                    mapping_id: "88888888-8888-4888-8888-888888888888",
                    status: "TRUSTED",
                    authorized_scopes: ["issue.priority"],
                  }],
                },
              },
              operations: [],
              rollback_operations: [],
              blockers: [],
              expires_at: "2026-10-08T10:00:00Z",
            },
          }],
          canaryPlans: [{
            plan_id: CANARY_PLAN_ID,
            connection_id: CONNECTION_ID,
            binding_id: BINDING_ID,
            project_id: "project-001",
            external_issue_id: "YT-17",
            status: "PLANNED",
            current_priority: "medium",
            target_priority: "high",
            created_at: "2026-10-07T10:00:00Z",
            expires_at: "2026-10-08T10:00:00Z",
          }],
        }),
      });
      return;
    }
    await route.continue();
  });

  await authenticate(page, "/integrations");

  const integrations = page.getByRole("main", { name: "PM integrations" });
  const connections = integrations.getByRole("region", { name: "Connections" });
  await expect(connections.locator("div.font-medium")).toHaveText("Local YouTrack fixture");
  await expect(integrations.getByRole("region", { name: "Trusted actor mapping snapshot" })).toContainText("issue.priority");
  await expect(integrations.getByRole("article", { name: `Outbox event ${OUTBOX_ID}` })).toContainText("project.updated");
  await expect(integrations.getByRole("article", { name: `Reconciliation run ${RUN_ID}` })).toContainText("updated");

  const conflict = integrations.getByRole("article", { name: `Conflict ${CONFLICT_ID}` });
  await expect(conflict).toContainText("External issue revision differs");
  await conflict.getByRole("textbox", { name: `Disposition note for ${CONFLICT_ID}` }).fill("Canonical owner confirmed the remote update is obsolete.");
  await integrations.getByRole("button", { name: "Refresh" }).click();
  await expect(integrations.getByText("Showing last known integration state")).toBeVisible();
  await expect(conflict.getByRole("button", { name: "Mark resolved" })).toBeDisabled();
  await integrations.getByRole("button", { name: "Retry" }).click();
  await expect(connections.locator("div.font-medium")).toHaveText("Local YouTrack fixture");
  await expect(conflict.getByRole("button", { name: "Mark resolved" })).toBeEnabled();
  await conflict.getByRole("button", { name: "Mark resolved" }).click();
  await expect(integrations.getByRole("status")).toContainText("Conflict disposition recorded as RESOLVED");
  expect(resolutionRequest).toEqual({
    status: "RESOLVED",
    resolution: { operator_note: "Canonical owner confirmed the remote update is obsolete." },
  });
  await expect(integrations.getByText("No open conflicts for this connection filter.")).toBeVisible();

  await integrations.getByRole("combobox", { name: "Recent canary plans" }).selectOption(CANARY_PLAN_ID);
  await integrations.getByRole("button", { name: "Inspect canary" }).click();
  const canary = integrations.getByRole("article", { name: "Canary plan details" });
  await expect(canary).toBeVisible();
  await expect(canary).toContainText("Digest valid: yes");
  await expect(canary).toContainText("medium → high");
});

test("PM inbound canaries show an empty state and retain UUID inspection", async ({ page }) => {
  await page.route("**/api/projects?limit=1000", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
  });
  await page.route((url) => new URL(url).pathname === "/api/integrations/pm", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connections: [], conflicts: [], outbox: [], runs: [], lifecyclePlans: [], canaryPlans: [],
      }),
    });
  });
  await page.route((url) => new URL(url).pathname === `/api/integrations/pm/inbound-canaries/${CANARY_PLAN_ID}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "PLANNED",
        digest_valid: true,
        accepted_command_count: 0,
        plan: {
          plan_id: CANARY_PLAN_ID,
          connection_id: CONNECTION_ID,
          binding_id: BINDING_ID,
          current_priority: "medium",
          target_priority: "high",
          expires_at: "2026-10-08T10:00:00Z",
          gate_results: { doctor_ready: true },
          operations: [],
          rollback_operations: [],
        },
      }),
    });
  });

  await authenticate(page, "/integrations");

  const integrations = page.getByRole("main", { name: "PM integrations" });
  await expect(integrations.getByText("No inbound canary plans are recorded for the current connection filter.")).toBeVisible();
  await expect(integrations.getByRole("combobox", { name: "Recent canary plans" })).toBeDisabled();
  await integrations.getByRole("textbox", { name: "Canary plan UUID" }).fill(CANARY_PLAN_ID);
  await integrations.getByRole("button", { name: "Inspect canary" }).click();
  await expect(integrations.getByRole("article", { name: "Canary plan details" })).toContainText("Digest valid: yes");
});

test("PM integrations expose connection diagnostics, reconcile trigger, and governed canary actions", async ({ page }) => {
  const CANARY_DIGEST = "d".repeat(64);
  const INBOX_ID = "99999999-9999-4999-8999-999999999999";
  let canaryStatus = "PLANNED";
  let reconcileRequest: Record<string, unknown> | null = null;
  let approveRequest: Record<string, unknown> | null = null;
  let replayRequest: Record<string, unknown> | null = null;
  let healthHits = 0;

  await page.route("**/api/projects?limit=1000", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
  });

  await page.route("**/api/integrations/pm**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (pathname === "/api/integrations/pm") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          connections: [{
            id: CONNECTION_ID,
            display_name: "Local YouTrack fixture",
            provider_kind: "youtrack",
            base_url: "https://example.invalid",
            status: "SHADOW",
            revision: 4,
          }],
          conflicts: [],
          outbox: [],
          runs: [],
          lifecyclePlans: [],
          canaryPlans: [{
            plan_id: CANARY_PLAN_ID,
            connection_id: CONNECTION_ID,
            binding_id: BINDING_ID,
            project_id: "project-001",
            external_issue_id: "YT-17",
            status: canaryStatus,
            current_priority: "medium",
            target_priority: "high",
            created_at: "2026-10-07T10:00:00Z",
            expires_at: "2026-10-08T10:00:00Z",
          }],
        }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/connections/${CONNECTION_ID}/health`) {
      healthHits += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ok: true, connection_id: CONNECTION_ID, provider: "youtrack" }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/connections/${CONNECTION_ID}/capabilities`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ connection_id: CONNECTION_ID, work_items: true }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/connections/${CONNECTION_ID}/doctor`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ready: true, connection_id: CONNECTION_ID, checks: [{ name: "health_and_credentials", ok: true }] }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/connections/${CONNECTION_ID}/reconcile` && request.method() === "POST") {
      reconcileRequest = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ id: RUN_ID, connection_id: CONNECTION_ID, status: "AUDITED" }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/inbound-canaries/${CANARY_PLAN_ID}` && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          status: canaryStatus,
          digest: CANARY_DIGEST,
          digest_valid: true,
          accepted_command_count: 0,
          plan: {
            plan_id: CANARY_PLAN_ID,
            connection_id: CONNECTION_ID,
            binding_id: BINDING_ID,
            current_priority: "medium",
            target_priority: "high",
            expires_at: "2026-10-08T10:00:00Z",
            gate_results: { doctor_ready: true },
            operations: [],
            rollback_operations: [],
          },
        }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/inbound-canaries/${CANARY_PLAN_ID}/approve` && request.method() === "POST") {
      approveRequest = request.postDataJSON() as Record<string, unknown>;
      canaryStatus = "APPROVED";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: canaryStatus }),
      });
      return;
    }

    if (pathname === `/api/integrations/pm/inbound-canaries/${CANARY_PLAN_ID}/replay-verified-event` && request.method() === "POST") {
      replayRequest = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "accepted", plan_id: CANARY_PLAN_ID, inbox_id: INBOX_ID }),
      });
      return;
    }

    await route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Unmocked PM API request in this test" }),
    });
  });

  page.on("dialog", async (dialog) => { await dialog.accept(); });
  await authenticate(page, "/integrations");

  const integrations = page.getByRole("main", { name: "PM integrations" });
  await integrations.getByLabel("Lifecycle connection").selectOption(CONNECTION_ID);

  await integrations.getByText("Connection diagnostics: health, capabilities, doctor").click();
  await integrations.getByRole("button", { name: "Check health" }).click();
  await expect(integrations.getByRole("article", { name: "Connection health result" })).toContainText("Health: ok");
  await integrations.getByRole("button", { name: "Check capabilities" }).click();
  await expect(integrations.getByRole("article", { name: "Connection capabilities result" })).toContainText("work_items");
  await integrations.getByRole("button", { name: "Check doctor" }).click();
  await expect(integrations.getByRole("article", { name: "Connection doctor result" })).toContainText("Doctor: ready");
  expect(healthHits).toBe(1);

  await integrations.getByRole("button", { name: "Trigger reconciliation" }).click();
  await expect(integrations.getByText(`Recorded ${RUN_ID}`)).toBeVisible();
  expect(reconcileRequest).toEqual({ mode: "audit" });

  await integrations.getByRole("combobox", { name: "Recent canary plans" }).selectOption(CANARY_PLAN_ID);
  await integrations.getByRole("button", { name: "Inspect canary" }).click();
  const canary = integrations.getByRole("article", { name: "Canary plan details" });
  await expect(canary).toContainText(`Digest: ${CANARY_DIGEST}`);
  const review = canary.getByLabel("I reviewed this exact canary digest");
  await expect(review).not.toBeChecked();
  await review.check();
  await canary.getByRole("button", { name: "Canary approve" }).click();
  await expect(integrations.getByText("Canary approve recorded.")).toBeVisible();
  expect(approveRequest).toEqual({ digest: CANARY_DIGEST, confirm: true });

  await canary.getByLabel("I reviewed this exact canary digest").check();
  await canary.getByLabel("Canary replay inbox ID").fill(INBOX_ID);
  await canary.getByLabel("Canary replay reason").fill("Operator replay of a verified delivery");
  await canary.getByRole("button", { name: "Replay verified event" }).click();
  await expect(integrations.getByText("Canary replay recorded.")).toBeVisible();
  expect(replayRequest).toEqual({ digest: CANARY_DIGEST, confirm: true, inbox_id: INBOX_ID, reason: "Operator replay of a verified delivery" });
});
