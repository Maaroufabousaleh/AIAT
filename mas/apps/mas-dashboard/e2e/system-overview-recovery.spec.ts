import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

const expectedState = process.env.EXPECTED_OVERVIEW_STATE;

test.describe("System Overview source recovery baseline", () => {
  test.skip(
    !expectedState,
    "Run against an explicit deterministic overview fixture with EXPECTED_OVERVIEW_STATE",
  );

  test("identifies partial/offline sources and exposes a bounded retry", async ({
    page,
  }) => {
    await authenticate(page);

    const denied = expectedState === "denied";
    const status = page.getByRole("region", {
      name: denied ? "Overview access status" : "Overview data status",
    });
    await expect(status).toBeVisible();
    await expect(
      status.getByRole("heading", {
        name: denied
          ? "Overview access denied"
          : expectedState === "offline"
            ? "Overview data unavailable"
            : "Overview data is partial",
      }),
    ).toBeVisible();
    await expect(status.getByRole("status")).toBeVisible();
    if (denied) {
      await expect(status.getByRole("button", { name: "Retry overview data" })).toHaveCount(0);
      await expect(status).toContainText("Access was denied for");
    } else {
      await expect(
        status.getByRole("button", { name: "Retry overview data" }),
      ).toHaveCSS("min-height", "44px");
    }
    if (expectedState === "offline") {
      const unavailable = page.getByText("Company overview unavailable");
      await expect(unavailable).toBeVisible();
      await expect(unavailable.locator("..").locator("svg")).toHaveAttribute(
        "aria-hidden",
        "true",
      );
    }
  });
});
