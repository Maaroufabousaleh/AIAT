import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const CATALOGUE = {
  entries: [
    {
      model_id: "local-coder-v1",
      provider_id: "local-provider",
      description: "Local deterministic model",
      profile_state: "profile_pending",
      max_context_tokens: 8192,
      capabilities: { tool_calling: true, structured_output: true, streaming: true },
    },
  ],
};

const PROFILE = {
  profile_id: "alpha-coding",
  purpose: "Governed coding tasks",
  status: "draft",
  approved_provider_ids: ["local-provider"],
  required_capabilities: [],
  fallback_profile_ids: [],
  versions: [],
};

const APPROVED_PROFILE = {
  profile_id: "approved-coding",
  purpose: "Existing approved coding route",
  status: "approved",
  approved_provider_ids: ["local-provider"],
  versions: [{ id: "10000000-0000-4000-8000-000000000002", version: "2.1", provider_id: "local-provider", exact_model_id: "local-coder-v1", status: "approved" }],
};

async function mockReads(page: import("@playwright/test").Page) {
  await page.route("**/api/governance/model-profiles/catalogue", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(CATALOGUE) });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([APPROVED_PROFILE]) });
  });
}

test("operator creates a draft profile and version, then previews resolution without dispatch", async ({ page }) => {
  await mockReads(page);
  const externalRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (!new Set(["127.0.0.1", "localhost"]).has(url.hostname)) externalRequests.push(url.origin);
  });

  let profileBody: Record<string, unknown> | null = null;
  await page.route("**/api/model-profiles", async (route) => {
    if (route.request().method() !== "POST") return route.fulfill({ status: 405 });
    profileBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify(PROFILE) });
  });

  let versionBody: Record<string, unknown> | null = null;
  await page.route("**/api/model-profiles/alpha-coding/versions", async (route) => {
    versionBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ id: "10000000-0000-4000-8000-000000000003", version: "1.0.0", provider_id: "local-provider", exact_model_id: "local-coder-v1", status: "draft", capabilities: ["tool_calling", "structured_output", "streaming"] }),
    });
  });

  let previewBody: Record<string, unknown> | null = null;
  await page.route("**/api/model-profiles/resolve-preview", async (route) => {
    previewBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ authorized: true, snapshot: { resolved_profile_id: "approved-coding", resolved_profile_version: "2.1", provider_id: "local-provider", exact_model_id: "local-coder-v1", cost_estimate_usd: 0.001 } }),
    });
  });

  await authenticate(page, "/models/profiles");
  const main = page.getByRole("main");
  await page.getByLabel("Profile ID").fill("alpha-coding");
  await page.getByLabel("Purpose").fill("Governed coding tasks");
  await page.getByRole("button", { name: "Create draft profile and version" }).click();
  await expect(main.getByRole("status")).toContainText("Draft version 1.0.0 saved");
  expect(profileBody).toMatchObject({ profile_id: "alpha-coding", status: "draft", approved_provider_ids: ["local-provider"] });
  expect(versionBody).toMatchObject({ version: "1.0.0", provider_id: "local-provider", exact_model_id: "local-coder-v1", status: "draft" });
  expect(versionBody).not.toHaveProperty("provider_settings");

  await page.getByLabel("Requested profile").selectOption("approved-coding");
  await page.getByRole("button", { name: "Preview resolution" }).click();
  await expect(page.getByRole("region", { name: "Resolution preview result" })).toContainText("Dry-run resolved");
  await expect(page.getByRole("region", { name: "Resolution preview result" })).toContainText("$0.001000");
  expect(previewBody).toMatchObject({ task_type: "coding", requested_profile_id: "approved-coding" });
  await page.getByLabel("Requested profile").selectOption("alpha-coding");
  await expect(page.getByRole("region", { name: "Resolution preview result" })).toHaveCount(0);
  expect(externalRequests).toEqual([]);
});

test("model profile setup displays conflict and validation status codes", async ({ page }) => {
  await mockReads(page);
  await page.route("**/api/model-profiles", async (route) => {
    await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: "Model Profile already exists" }) });
  });
  await authenticate(page, "/models/profiles");
  await page.getByLabel("Profile ID").fill("duplicate-profile");
  await page.getByLabel("Purpose").fill("Test profile");
  await page.getByRole("button", { name: "Create draft profile and version" }).click();
  const main = page.getByRole("main");
  await expect(main.getByRole("alert")).toContainText("409: Model Profile already exists");

  await page.unroute("**/api/model-profiles");
  await page.route("**/api/model-profiles", async (route) => {
    await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({ ...PROFILE, profile_id: "invalid-provider" }) });
  });
  await page.route("**/api/model-profiles/invalid-provider/versions", async (route) => {
    await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "provider is not approved by this profile" }) });
  });
  await page.getByLabel("Profile ID").fill("invalid-provider");
  await page.getByRole("button", { name: "Create draft profile and version" }).click();
  await expect(main.getByRole("alert")).toContainText("422: provider is not approved");
  await expect(main.getByRole("status")).toContainText("Draft profile invalid-provider was saved");
});

test("profile setup discards an in-flight preview when its task input changes", async ({ page }) => {
  await mockReads(page);
  let releasePreview!: () => void;
  let markPreviewStarted!: () => void;
  const previewStarted = new Promise<void>((resolve) => { markPreviewStarted = resolve; });
  const holdPreview = new Promise<void>((resolve) => { releasePreview = resolve; });
  await page.route("**/api/model-profiles/resolve-preview", async (route) => {
    markPreviewStarted();
    await holdPreview;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authorized: true,
        snapshot: {
          resolved_profile_id: "approved-coding",
          resolved_profile_version: "2.1",
          provider_id: "local-provider",
          exact_model_id: "local-coder-v1",
          cost_estimate_usd: 0.001,
        },
      }),
    });
  });

  await authenticate(page, "/models/profiles");
  await page.getByRole("button", { name: "Preview resolution" }).click();
  await previewStarted;
  await page.getByLabel("Task type").fill("research");
  releasePreview();

  await expect(page.getByRole("region", { name: "Resolution preview result" })).toHaveCount(0);
});
