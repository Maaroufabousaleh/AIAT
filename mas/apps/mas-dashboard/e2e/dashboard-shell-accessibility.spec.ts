import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test.describe("Dashboard shell accessibility and mobile navigation", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("skip link moves focus to the main landmark", async ({ page }) => {
    await authenticate(page, "/projects");

    const skipLink = page.getByRole("link", { name: "Skip to main content" });
    await skipLink.focus();
    await expect(skipLink).toBeFocused();
    await skipLink.press("Enter");

    await expect(page.locator("main#dashboard-main")).toBeFocused();
  });

  test("mobile navigation moves focus in and restores it on Escape", async ({ page }) => {
    await authenticate(page, "/projects");

    const menuButton = page.getByRole("button", { name: "Open navigation" });
    await expect(menuButton).toHaveCSS("width", "44px");
    await menuButton.click();

    await expect(
      page.getByRole("banner").getByRole("button", { name: "Close navigation" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: /AIAT MAS operator console/ }),
    ).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Open navigation" })).toBeFocused();
  });

  test("primary navigation exposes company, approval, and model profile workspaces", async ({ page }) => {
    await page.route("**/api/ceo/privileged-actions/pending", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route("**/api/ceo/privileged-actions/audit", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.route((url) => url.pathname === "/api/approvals/project-decisions", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], next_cursor: null }) });
    });
    await authenticate(page, "/approvals");

    const navigation = page.getByRole("navigation", { name: "Primary" });
    await expect(navigation.getByRole("link", { name: "Companies" })).toHaveAttribute("href", "/companies");
    await expect(navigation.getByRole("link", { name: "Approvals", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(navigation.getByRole("link", { name: "Model Resolution Preview" })).toHaveAttribute("href", "/models");
    await expect(navigation.getByRole("link", { name: "Model Profiles" })).toHaveAttribute("href", "/models/profiles");
  });
});
