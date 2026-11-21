import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("logs transport failure surfaces feedback with retry", async ({ page }) => {
  await page.route("**/api/logs/**", async (route) => {
    await route.abort("connectionrefused");
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/logs");
  await page.getByRole("button", { name: "Load", exact: true }).click();
  await expect(page.getByText("The log stream closed without sending any lines.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("analytics launchers stay in-dashboard with open and copy actions", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/analytics/litellm");
  await expect(page.getByRole("heading", { name: "LiteLLM Analytics" })).toBeVisible();
  await expect(page.getByRole("region", { name: "LiteLLM dashboard status" })).toBeVisible();
  const open = page.getByRole("link", { name: "Open LiteLLM dashboard" });
  await expect(open).toBeVisible();
  await expect(open).toHaveAttribute("href", /localhost:4001|127\.0\.0\.1/);
  await expect(page.getByRole("button", { name: "Copy LiteLLM dashboard URL" })).toBeVisible();
  // No iframe: upstream dashboards prohibit framing, so the page links out.
  await expect(page.locator("iframe")).toHaveCount(0);
  expect(errors).toEqual([]);

  await authenticate(page, "/analytics/omniroute");
  await expect(page.getByRole("heading", { name: "OmniRoute Analytics" })).toBeVisible();
  await expect(page.getByRole("region", { name: "OmniRoute dashboard status" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open OmniRoute dashboard" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("evidence kind index renders with owning link", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await authenticate(page, "/evidence/project");
  await expect(page.getByRole("heading", { name: "Evidence" })).toBeVisible();
  await expect(page.getByTestId("evidence-kind-record")).toContainText("project");
  await expect(page.getByTestId("evidence-kind-home-link")).toHaveAttribute("href", "/projects");
  expect(errors).toEqual([]);

  await authenticate(page, "/evidence/nope");
  await expect(page.getByText("Unsupported evidence kind")).toBeVisible();
  expect(errors).toEqual([]);
});
