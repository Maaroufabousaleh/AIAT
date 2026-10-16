import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const versionId = "10000000-0000-4000-8000-000000000001";

function draftProfile() {
  return {
    profile_id: "coding",
    purpose: "Governed coding tasks",
    status: "draft",
    approved_provider_ids: ["local-fixture"],
    required_capabilities: [],
    fallback_profile_ids: [],
    versions: [{
      id: versionId,
      version: "v1",
      provider_id: "local-fixture",
      exact_model_id: "fixture/no-dispatch",
      status: "draft",
      capabilities: ["tool_calling"],
    }],
  };
}

async function mockCatalogue(page: import("@playwright/test").Page, readProfile: () => object) {
  await page.route("**/api/governance/model-profiles/catalogue", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ entries: [] }) });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([readProfile()]) });
  });
}

test("operator approves one exact draft version and sees its audit receipt", async ({ page }) => {
  let profile = draftProfile();
  await mockCatalogue(page, () => profile);
  let decisionBody: Record<string, unknown> | null = null;
  const externalRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!new Set(["127.0.0.1", "localhost"]).has(url.hostname)) externalRequests.push(url.origin);
  });
  await page.route(`**/api/model-profiles/coding/versions/${versionId}/activate`, async (route) => {
    decisionBody = route.request().postDataJSON() as Record<string, unknown>;
    profile = {
      ...profile,
      status: "approved",
      versions: profile.versions.map((version) => ({ ...version, status: "approved" })),
    };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        profile_id: "coding",
        profile_status: "approved",
        version_id: versionId,
        version: "v1",
        version_status: "approved",
        decision: "APPROVED",
        already_approved: false,
        approval_record: {
          id: "approval-1",
          decided_by: "operator",
          reason: "Reviewed local model selection",
          created_at: "2026-10-07T12:00:00+00:00",
          evidence: { version_snapshot_sha256: "a".repeat(64) },
        },
      }),
    });
  });

  await authenticate(page, "/models/profiles");
  await page.getByLabel("Approval reason for coding v1").fill("  Reviewed local model selection  ");
  await page.getByRole("button", { name: "Approve and activate exact version" }).click();

  const receipt = page.getByRole("listitem", { name: "Model profile coding" }).getByRole("status");
  await expect(receipt).toContainText("Exact version approved and activated");
  await expect(receipt).toContainText("operator");
  await expect(receipt).toContainText("approval-1");
  await expect(receipt).toContainText("a".repeat(64));
  await expect(page.getByText("Version: approved")).toBeVisible();
  expect(decisionBody).toEqual({ reason: "Reviewed local model selection" });
  expect(externalRequests).toEqual([]);
});

test("model profile approval leaves a recoverable error when the version changed", async ({ page }) => {
  const profile = draftProfile();
  await mockCatalogue(page, () => profile);
  await page.route(`**/api/model-profiles/coding/versions/${versionId}/activate`, async (route) => {
    await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "model profile version is already approved" }) });
  });

  await authenticate(page, "/models/profiles");
  await page.getByLabel("Approval reason for coding v1").fill("Reviewed exact version");
  await page.getByRole("button", { name: "Approve and activate exact version" }).click();

  await expect(page.getByRole("listitem", { name: "Model profile coding" }).getByRole("alert"))
    .toContainText("409: model profile version is already approved");
  await expect(page.getByRole("button", { name: "Approve and activate exact version" })).toBeEnabled();
});
