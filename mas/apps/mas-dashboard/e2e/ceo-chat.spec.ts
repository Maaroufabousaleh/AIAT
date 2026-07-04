import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("CEO chat publishes a directive and receives the live orchestrator response", async ({
  page,
}) => {
  const marker = `e2e-${Date.now()}`;
  const directive = `show company status for live verification ${marker}`;

  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("stream:exec_ceo connected")).toBeVisible();

  const input = page.getByRole("textbox");
  await input.fill(directive);
  await input.press("Enter");

  await expect(page.getByText(directive, { exact: true })).toBeVisible();
  await expect(
    page.getByText(/AIAT company is (seeded|not seeded)/i),
  ).toBeVisible({
    timeout: 30_000,
  });

  const outboundFilter = page.getByRole("button", { name: /OUTBOUND\s+\d+/i });
  await outboundFilter.click();
  await expect(page.getByText(directive, { exact: true })).toBeVisible();
  await expect(
    page.getByText(/AIAT company is (seeded|not seeded)/i),
  ).toHaveCount(0);
});
