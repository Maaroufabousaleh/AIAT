import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test.describe("CEO evidence detail", () => {
  test("renders bounded scalar detail without exposing payload fields", async ({ page }) => {
    await page.route("**/api/evidence/project/demo-project", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "project",
          id: "demo-project",
          source: "control-plane",
          record: {
            id: "demo-project",
            name: "Demo project",
            state: "ACTIVE",
            revision: 4,
          },
        }),
      });
    });
    await authenticate(page, "/evidence/project/demo-project");

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("Demo project");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("ACTIVE");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("payload");
  });

  test("keeps unsupported kinds as identity-only citations", async ({ page }) => {
    await authenticate(page, "/evidence/company/company-123");

    await expect(page.getByTestId("ceo-evidence-record")).toContainText("company-123");
    await expect(page.getByTestId("ceo-evidence-detail")).toHaveCount(0);
    await expect(page.getByTestId("ceo-evidence-canonical-link")).toBeVisible();
  });

  test("renders artifact scalars without nested metadata", async ({ page }) => {
    await page.route("**/api/evidence/artifact/42", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "artifact",
          id: "42",
          source: "control-plane",
          record: {
            id: 42,
            agent_id: "requirements_writer",
            path: "project-1/requirements.md",
            sha256: "a".repeat(64),
            size_bytes: 128,
          },
        }),
      });
    });

    await authenticate(page, "/evidence/artifact/42");

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("requirements_writer");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("requirements.md");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("metadata");
    await expect(page.getByTestId("ceo-evidence-canonical-link")).toHaveAttribute(
      "href",
      "/projects?evidence_kind=artifact&evidence_id=42",
    );
  });

  test("renders usage scalars without pricing or resource payloads", async ({ page }) => {
    const usageId = "00000000-0000-4000-a000-000000000099";
    await page.route(`**/api/evidence/usage/${usageId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "usage",
          id: usageId,
          source: "control-plane",
          record: {
            id: usageId,
            event_type: "llm",
            model: "local-model",
            prompt_tokens: 10,
            completion_tokens: 5,
            cost_usd: "0.01250000",
            duration_ms: "42.500",
          },
        }),
      });
    });

    await authenticate(page, `/evidence/usage/${usageId}`);

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("local-model");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("0.01250000");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("pricing snapshot");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("resource json");
  });

  test("projects the bounded trace summary without rendering trace items", async ({ page }) => {
    await page.route("**/api/evidence/trace/trace-001", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "trace",
          id: "trace-001",
          source: "control-plane",
          record: {
            trace_id: "trace-001",
            status: "observed",
            item_count: 2,
            generated_at: "2026-08-11T12:00:00Z",
          },
        }),
      });
    });

    await authenticate(page, "/evidence/trace/trace-001");

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("observed");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("item count");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("items");
    await expect(page.getByTestId("ceo-evidence-canonical-link")).toHaveAttribute("href", "/logs?trace_id=trace-001");
  });

  test("renders model catalogue scalars without nested bindings", async ({ page }) => {
    await page.route("**/api/evidence/model/model-001", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "model",
          id: "model-001",
          source: "control-plane",
          record: {
            model_id: "model-001",
            provider_id: "local-provider",
            profile_state: "approved_profile_present",
            max_context_tokens: 8192,
          },
        }),
      });
    });

    await authenticate(page, "/evidence/model/model-001");

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("approved_profile_present");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("8192");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("profile bindings");
  });

  test("renders integration connection scalars without configuration", async ({ page }) => {
    await page.route("**/api/evidence/integration/connection-001", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "integration",
          id: "connection-001",
          source: "control-plane",
          record: {
            id: "connection-001",
            provider_kind: "github",
            display_name: "Source control",
            status: "ACTIVE",
          },
        }),
      });
    });

    await authenticate(page, "/evidence/integration/connection-001");

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("Source control");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("ACTIVE");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("configuration");
  });

  test("retains the last safe detail while a refresh is temporarily unavailable", async ({ page }) => {
    let requestCount = 0;
    await page.route("**/api/evidence/model/model-down", async (route) => {
      if (requestCount++ === 0) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            schema_version: "aiat.evidence-detail.v1",
            kind: "model",
            id: "model-down",
            source: "control-plane",
            record: {
              model_id: "model-down",
              profile_state: "approved_profile_present",
            },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: "evidence detail is temporarily unavailable",
          detail_supported: true,
        }),
      });
    });

    await authenticate(page, "/evidence/model/model-down");

    await expect(page.getByTestId("ceo-evidence-record")).toContainText("model-down");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("approved_profile_present");
    await page.getByTestId("ceo-evidence-retry").click();
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("temporarily unavailable");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("last successful scalar projection");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("approved_profile_present");
    await expect(page.getByTestId("ceo-evidence-canonical-link")).toHaveAttribute(
      "href",
      "/governance?evidence_kind=model&evidence_id=model-down",
    );
  });

  test("renders tool catalogue scalars without schemas or credential requirements", async ({ page }) => {
    await page.route("**/api/evidence/tool/tool-001", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          schema_version: "aiat.evidence-detail.v1",
          kind: "tool",
          id: "tool-001",
          source: "control-plane",
          record: {
            tool_name: "time_now",
            tool_group: "system",
            schema_status: "declared",
            risk_tier: "low",
            available: true,
          },
        }),
      });
    });

    await authenticate(page, "/evidence/tool/tool-001");

    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("time_now");
    await expect(page.getByTestId("ceo-evidence-detail")).toContainText("declared");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("credential requirements");
    await expect(page.getByTestId("ceo-evidence-detail")).not.toContainText("input schema");
  });
});
