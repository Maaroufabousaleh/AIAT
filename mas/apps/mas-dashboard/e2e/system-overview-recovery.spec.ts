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

    const status = page.getByRole("region", { name: "Overview data status" });
    await expect(status).toBeVisible();
    await expect(
      status.getByRole("heading", {
        name: expectedState === "offline" ? "Overview data unavailable" : "Overview data is partial",
      }),
    ).toBeVisible();
    await expect(status.getByRole("status")).toBeVisible();
    await expect(
      status.getByRole("button", { name: "Retry overview data" }),
    ).toHaveCSS("min-height", "44px");
  });
});
