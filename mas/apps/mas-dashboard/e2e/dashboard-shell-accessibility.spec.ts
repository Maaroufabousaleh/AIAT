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
});
