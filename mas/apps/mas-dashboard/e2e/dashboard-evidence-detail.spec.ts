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
    await authenticate(page, "/evidence/tool/tool-123");

    await expect(page.getByTestId("ceo-evidence-record")).toContainText("tool-123");
    await expect(page.getByTestId("ceo-evidence-detail")).toHaveCount(0);
    await expect(page.getByTestId("ceo-evidence-canonical-link")).toBeVisible();
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
});
