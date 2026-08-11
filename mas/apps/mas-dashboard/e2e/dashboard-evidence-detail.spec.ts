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
});
