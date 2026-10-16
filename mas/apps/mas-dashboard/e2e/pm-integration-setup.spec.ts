import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const PROJECT_ID = "a1111111-1111-4111-8111-111111111111";
const CONNECTION_ID = "b2222222-2222-4222-8222-222222222222";
const BINDING_ID = "c3333333-3333-4333-8333-333333333333";
const PROVISIONING_PLAN_ID = "d4444444-4444-4444-8444-444444444444";
const LIFECYCLE_PLAN_ID = "e5555555-5555-4555-8555-555555555555";
const PROVISIONING_DIGEST = "a".repeat(64);
const LIFECYCLE_DIGEST = "b".repeat(64);

test("operator creates a PM connection, binds a project, and applies an approved lifecycle plan", async ({ page }) => {
  let connectionStatus = "DISABLED";
  let connectionCreated = false;
  let binding: Record<string, unknown> | null = null;
  let lifecyclePlanStatus = "PLANNED";
  let connectionRequest: Record<string, unknown> | null = null;
  let provisioningPlanRequest: Record<string, unknown> | null = null;
  let provisioningApplyRequest: Record<string, unknown> | null = null;
  let lifecyclePlanRequest: Record<string, unknown> | null = null;
  let lifecycleApprovalRequest: Record<string, unknown> | null = null;
  let lifecycleApplyRequest: Record<string, unknown> | null = null;
  const externalRequests: string[] = [];
  const appOrigin = new URL(process.env.PLAYWRIGHT_BASE_URL ?? "http://127.0.0.1:4000").origin;

  page.on("request", (request) => {
    const origin = new URL(request.url()).origin;
    if (origin !== appOrigin) externalRequests.push(origin);
  });

  await page.route("**/api/projects**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/projects") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{ id: PROJECT_ID, name: "PM setup fixture", state: "ACTIVE" }]),
    });
  });

  await page.route("**/api/integrations/pm**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/integrations/pm") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          connections: connectionCreated ? [{
            id: CONNECTION_ID,
            display_name: "Local YouTrack fixture",
            provider_kind: "youtrack",
            base_url: "https://youtrack.example.invalid",
            status: connectionStatus,
            revision: connectionStatus === "DISABLED" ? 1 : 2,
          }] : [],
          conflicts: [],
          outbox: [],
          runs: [],
          lifecyclePlans: lifecyclePlanRequest ? [{
            id: LIFECYCLE_PLAN_ID,
            status: lifecyclePlanStatus,
            plan_digest: LIFECYCLE_DIGEST,
            plan: {
              plan_id: LIFECYCLE_PLAN_ID,
              target_type: "pm_connection",
              connection_id: CONNECTION_ID,
              binding_id: null,
              desired_connection_status: "SHADOW",
              desired_binding_status: null,
              operations: [{ operation: "set_connection_status", from: "DISABLED", to: "SHADOW" }],
              rollback_operations: [],
              blockers: [],
              expires_at: "2026-10-08T12:00:00Z",
            },
          }] : [],
        }),
      });
      return;
    }

    if (path === "/api/integrations/pm/connections" && request.method() === "POST") {
      connectionRequest = request.postDataJSON() as Record<string, unknown>;
      connectionCreated = true;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: CONNECTION_ID,
          display_name: "Local YouTrack fixture",
          provider_kind: "youtrack",
          base_url: "https://youtrack.example.invalid",
          status: connectionStatus,
          revision: 1,
        }),
      });
      return;
    }

    if (path === `/api/integrations/pm/projects/${PROJECT_ID}/bindings` && request.method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(binding ? [binding] : []),
      });
      return;
    }

    if (path === `/api/integrations/pm/projects/${PROJECT_ID}/provisioning/plan` && request.method() === "POST") {
      provisioningPlanRequest = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          project_id: PROJECT_ID,
          connection_id: CONNECTION_ID,
          plan: {
            plan_id: PROVISIONING_PLAN_ID,
            project_id: PROJECT_ID,
            connection_id: CONNECTION_ID,
            provider_kind: "youtrack",
            mapping_profile: "dedicated_project",
            external_project_id: "AIAT-LOCAL",
            external_project_key: "AIAT-LOCAL",
            actions: [{ action: "create", resource: "project:AIAT-LOCAL", destructive: false, manual: false }],
            blockers: [],
            manual_actions: [],
          },
          plan_digest: PROVISIONING_DIGEST,
          blockers: [],
          manual_actions: [],
        }),
      });
      return;
    }

    if (path === `/api/integrations/pm/projects/${PROJECT_ID}/provisioning/apply` && request.method() === "POST") {
      provisioningApplyRequest = request.postDataJSON() as Record<string, unknown>;
      binding = {
        id: BINDING_ID,
        project_id: PROJECT_ID,
        connection_id: CONNECTION_ID,
        external_project_id: "AIAT-LOCAL",
        external_project_key: "AIAT-LOCAL",
        mapping_profile: "dedicated_project",
        status: "DISABLED",
        provisioning_state: "PROVISIONED",
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          project_id: PROJECT_ID,
          connection_id: CONNECTION_ID,
          plan_digest: PROVISIONING_DIGEST,
          status: "DISABLED",
          binding,
          blockers: [],
        }),
      });
      return;
    }

    if (path === "/api/integrations/pm/lifecycle-plans" && request.method() === "POST") {
      lifecyclePlanRequest = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: LIFECYCLE_PLAN_ID,
          status: lifecyclePlanStatus,
          plan_digest: LIFECYCLE_DIGEST,
          plan: {
            plan_id: LIFECYCLE_PLAN_ID,
            target_type: "pm_connection",
            connection_id: CONNECTION_ID,
            desired_connection_status: "SHADOW",
            operations: [{ operation: "set_connection_status", from: "DISABLED", to: "SHADOW" }],
            rollback_operations: [],
            blockers: [],
            expires_at: "2026-10-08T12:00:00Z",
          },
        }),
      });
      return;
    }

    if (path === `/api/integrations/pm/lifecycle-plans/${LIFECYCLE_PLAN_ID}/approve` && request.method() === "POST") {
      lifecycleApprovalRequest = request.postDataJSON() as Record<string, unknown>;
      lifecyclePlanStatus = "APPROVED";
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: lifecyclePlanStatus }) });
      return;
    }

    if (path === `/api/integrations/pm/lifecycle-plans/${LIFECYCLE_PLAN_ID}/apply` && request.method() === "POST") {
      lifecycleApplyRequest = request.postDataJSON() as Record<string, unknown>;
      lifecyclePlanStatus = "APPLIED";
      connectionStatus = "SHADOW";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: lifecyclePlanStatus, application: { status: connectionStatus } }),
      });
      return;
    }

    externalRequests.push(`${path} ${request.method()}`);
    await route.fulfill({
      status: 501,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Unmocked PM API request in this test" }),
    });
  });

  page.on("dialog", async (dialog) => { await dialog.accept(); });
  await authenticate(page, "/integrations");

  const main = page.getByRole("main", { name: "PM integrations" });
  await expect(main.getByRole("heading", { name: "Create provider connection" })).toBeVisible();
  await main.getByLabel("Connection display name").fill("Local YouTrack fixture");
  await main.getByLabel("Provider base URL").fill("https://youtrack.example.invalid");
  await main.getByLabel("Credential reference").fill("youtrack-e2e-credential-ref");
  await main.getByRole("button", { name: "Create connection" }).click();
  await expect(main.getByRole("status")).toContainText("created in DISABLED state");

  await main.getByLabel("Project provider connection").selectOption(CONNECTION_ID);
  await main.getByRole("button", { name: "Generate project provisioning plan" }).click();
  const provisioningPlan = main.getByRole("article", { name: "Project provisioning plan" });
  await expect(provisioningPlan).toContainText(PROVISIONING_DIGEST);
  await provisioningPlan.getByLabel("I reviewed this exact project provisioning digest").check();
  await provisioningPlan.getByRole("button", { name: "Apply reviewed plan and create binding" }).click();
  await expect(main.getByRole("status")).toContainText("Project provisioned and bound in DISABLED state");
  await expect(main.getByLabel("Project provider bindings")).toContainText("AIAT-LOCAL");

  await main.getByLabel("Lifecycle target").selectOption("pm_connection");
  await main.getByLabel("Desired lifecycle state").selectOption("SHADOW");
  await main.getByRole("button", { name: "Generate SHADOW plan" }).click();
  const lifecyclePlan = main.getByRole("article", { name: `Lifecycle plan ${LIFECYCLE_PLAN_ID}` });
  await expect(lifecyclePlan).toContainText(LIFECYCLE_DIGEST);
  const lifecycleReview = lifecyclePlan.getByLabel("I reviewed this exact digest");
  await expect(lifecycleReview).not.toBeChecked();
  await lifecycleReview.check();
  await lifecyclePlan.getByRole("button", { name: "Approve" }).click();
  await expect(lifecyclePlan.getByText("APPROVED")).toBeVisible();
  await lifecyclePlan.getByRole("button", { name: "Apply exact plan" }).click();
  await expect(main.getByRole("status")).toContainText("Lifecycle plan applied: SHADOW");
  await expect(main.getByRole("region", { name: "Connections" })).toContainText("SHADOW");

  expect(connectionRequest).toMatchObject({
    provider_kind: "youtrack",
    display_name: "Local YouTrack fixture",
    base_url: "https://youtrack.example.invalid",
    credential_ref: "youtrack-e2e-credential-ref",
    capability_profile: "pm",
    config: {},
  });
  expect(provisioningPlanRequest).toMatchObject({ connection_id: CONNECTION_ID, mapping_profile: "dedicated_project" });
  expect(provisioningApplyRequest).toMatchObject({ plan_digest: PROVISIONING_DIGEST, confirm: true });
  expect(lifecyclePlanRequest).toMatchObject({
    target_type: "pm_connection",
    connection_id: CONNECTION_ID,
    desired_connection_status: "SHADOW",
    ttl_seconds: 3600,
  });
  expect(lifecycleApprovalRequest).toMatchObject({ plan_digest: LIFECYCLE_DIGEST });
  expect(lifecycleApplyRequest).toEqual({ plan_digest: LIFECYCLE_DIGEST, confirm: true });
  expect(externalRequests).toEqual([]);
});
