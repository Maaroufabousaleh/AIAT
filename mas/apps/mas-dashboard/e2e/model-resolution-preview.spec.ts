import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROFILE = {
  profile_id: "local-coding",
  purpose: "Local coding preview",
  status: "approved",
  versions: [
    {
      version: "v1",
      provider_id: "local-provider",
      exact_model_id: "local-code-model",
      status: "approved",
      capabilities: ["tool_calling", "streaming"],
      cost_per_1k_input_usd: 0.001,
      cost_per_1k_output_usd: 0.002,
      max_cost_usd: 0.5,
      max_tokens_per_request: 8192,
    },
  ],
};

async function mockModelReadApis(page: import("@playwright/test").Page) {
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([PROFILE]) });
  });
  await page.route("**/api/executive/reconciliation", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        projects: { usage: { total_cost_usd: 1.25, llm_calls: 9 } },
        budgets: { configured_count: 1, available_usd: 12.5, used_usd: 7.5, overages: [], reservation_reconciliation: { anomaly_count: 0 } },
      }),
    });
  });
}

test("model page previews a governed profile and estimated cost without dispatch", async ({ page }) => {
  await mockModelReadApis(page);
  let previewBody: Record<string, unknown> | null = null;
  await page.route("**/api/governance/model-profiles/resolve-preview", async (route) => {
    previewBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authorized: true,
        snapshot: {
          resolved_profile_id: "local-coding",
          resolved_profile_version: "v1",
          provider_id: "local-provider",
          exact_model_id: "local-code-model",
          cost_estimate_usd: 0.004,
          required_capabilities: ["tool_calling"],
          fallback_chain: ["local-coding:v1"],
        },
      }),
    });
  });

  await authenticate(page, "/models");
  await expect(page.getByRole("heading", { name: "Model Profiles", exact: true })).toBeVisible();
  await expect(page.getByText("Recorded project usage")).toBeVisible();
  await expect(page.getByText("$1.25")).toBeVisible();
  await expect(page.getByText("local-coding", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/Preview estimate, not dispatch authorization/)).toBeVisible();
  await page.getByRole("button", { name: "Preview resolution" }).click();
  await expect(page.getByRole("region", { name: "Resolution estimate result" })).toBeVisible();
  await expect(page.getByText("$0.004")).toBeVisible();
  expect(previewBody).toMatchObject({ task_type: "coding", task_required_capabilities: ["tool_calling"] });
  expect(previewBody).not.toHaveProperty("layers");
});

test("model page shows backend rejection reasons as actionable preview blockers", async ({ page }) => {
  await mockModelReadApis(page);
  await page.route("**/api/governance/model-profiles/resolve-preview", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        authorized: false,
        error: { code: "NO_COMPLIANT_MODEL", message: "No approved Model Profile satisfies the effective policy and capability requirements" },
        rejected_candidates: [
          { profile_id: "local-coding", version: "v1", exact_model_id: "local-code-model", reasons: ["estimated cost exceeds budget", "missing capability: tool_calling"] },
        ],
      }),
    });
  });

  await authenticate(page, "/models");
  await page.getByRole("button", { name: "Preview resolution" }).click();
  const blockers = page.getByRole("region", { name: "Resolution blockers" });
  await expect(blockers).toBeVisible();
  await expect(blockers.getByText(/NO_COMPLIANT_MODEL/)).toBeVisible();
  await expect(blockers.getByText("estimated cost exceeds budget")).toBeVisible();
  await expect(blockers.getByText("missing capability: tool_calling")).toBeVisible();
});
