import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test.describe("Dashboard theme preference", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => window.localStorage.removeItem("aiat-theme"));
    await authenticate(page, "/projects");
    await page.waitForLoadState("networkidle");
  });

  test("persists explicit light and dark palettes", async ({ page }) => {
    const preference = page.getByRole("combobox", { name: "Theme preference" });

    await preference.selectOption("light");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await expect(preference).toHaveValue("light");

    await preference.selectOption("dark");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(preference).toHaveValue("dark");
  });

  test("system mode follows the operating-system preference", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    const preference = page.getByRole("combobox", { name: "Theme preference" });

    await preference.selectOption("system");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    await page.emulateMedia({ colorScheme: "dark" });
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });
});
