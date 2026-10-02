import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const OPERATOR_ROUTES = [
  "/",
  "/ceo",
  "/ceo/chat",
  "/projects",
  "/flows",
  "/flows/new",
  "/workers",
  "/governance",
  "/tools",
  "/credentials",
  "/identities",
  "/identity-approvals",
  "/identity-audit",
  "/auth-sessions",
  "/metrics",
  // These two routes leave the Next.js dashboard for their standalone
  // provider UIs, which do not share the dashboard's theme provider.
  "/logs",
  "/streams",
  "/dlq",
  "/system",
  "/system-viz",
];

test("light and dark palettes apply across the operator routes", async ({ page }) => {
  await authenticate(page, "/projects");

  const observed: Record<string, Record<string, string>> = {};
  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => window.localStorage.setItem("aiat-theme", value), theme);
    await page.reload();

    for (const route of OPERATOR_ROUTES) {
      await page.goto(route);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.locator("main").first()).toBeVisible();
      observed[route] ??= {};
      observed[route][theme] = await page.locator("html").evaluate((element) => {
        const styles = window.getComputedStyle(element);
        return ["--aiat-bg", "--aiat-surface", "--aiat-text"]
          .map((token) => styles.getPropertyValue(token).trim())
          .join("|");
      });
    }
  }

  for (const route of OPERATOR_ROUTES) {
    expect(observed[route].light, `${route} must change palette tokens`).not.toBe(
      observed[route].dark,
    );
  }
});
