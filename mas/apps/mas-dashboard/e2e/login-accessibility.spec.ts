import { expect, test } from "@playwright/test";

test.describe("Login accessibility baseline", () => {
  test("exposes the operator sign-in landmark, status, and keyboard controls", async ({
    page,
  }) => {
    await page.goto("/login");

    const signIn = page.getByRole("main", { name: "AIAT MAS sign-in" });
    await expect(signIn).toBeVisible();
    await expect(signIn).toHaveAttribute("aria-busy", "false");
    await expect(
      page.getByRole("region", { name: "Operator sign-in" }),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByRole("status")).toHaveText("Ready to sign in");

    await expect(page.getByLabel("Username")).toHaveAttribute(
      "autocomplete",
      "username",
    );
    await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
    await expect(
      page.getByRole("button", { name: "Show password" }).first(),
    ).toHaveCSS("min-height", "44px");
    await expect(page.getByRole("button", { name: "Sign in" })).toHaveCSS(
      "min-height",
      "44px",
    );

    await page.getByRole("button", { name: "Show password" }).first().click();
    await expect(page.getByLabel("Password", { exact: true })).toHaveAttribute(
      "type",
      "text",
    );
    await expect(page.getByRole("button", { name: "Hide password" }).first()).toBeVisible();
  });
});
