import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const ROUTES = [
  ["/identities", "Identities"],
  ["/identity-approvals", "Identity approvals"],
  ["/identity-audit", "Identity audit"],
  ["/auth-sessions", "Auth sessions"],
  ["/external-accounts", "External accounts"],
  ["/mail-domains", "Mail domains"],
  ["/mail-relay", "Mail relay"],
  ["/mailboxes", "Mailboxes"],
  ["/outbound-mail", "Outbound mail"],
] as const;

test.describe("Shared identity resource route matrix", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/identity/**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              id: "identity-matrix-001",
              state: "ACTIVE",
              status: "ACTIVE",
              display_name: "Safe identity fixture",
              worker_id: "worker-matrix-001",
            },
          ],
        }),
      });
    });
  });

  for (const [path, title] of ROUTES) {
    test(`${title} keeps the shared operator surface accessible`, async ({ page }) => {
      await authenticate(page, path);

      const main = page.getByRole("main", { name: title });
      await expect(main).toBeVisible();
      await expect(main).toHaveAttribute("aria-busy", "false");
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
      await expect(
        page.getByRole("region", { name: "Identity resource notice" }),
      ).toBeVisible();
      await expect(
        page.getByRole("region", { name: `${title} records` }),
      ).toBeVisible();
      await expect(page.getByText("identity-matrix-001")).toBeVisible();
      await expect(page.getByText("Safe identity fixture")).toBeVisible();
    });
  }
});
