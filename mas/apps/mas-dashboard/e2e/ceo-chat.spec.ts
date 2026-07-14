import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("CEO chat publishes a directive and receives the live orchestrator response", async ({
  page,
}) => {
  const marker = `e2e-${Date.now()}`;
  const directive = `show company status for live verification ${marker}`;

  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("stream:exec_ceo connected")).toBeVisible();
  await page.getByRole("button", { name: "Clear" }).click();

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

test("CEO chat keeps worker context through a hiring follow-up", async ({ page }) => {
  const workerName = `e2e_candidate_${Date.now()}`;

  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("stream:exec_ceo connected")).toBeVisible();

  const input = page.getByRole("textbox");
  await input.fill(
    `hire a worker named ${workerName} from https://github.com/example/${workerName} as a software engineer`,
  );
  await input.press("Enter");
  await expect(
    page.getByText(new RegExp(`opened a Hiring Board ticket for .*${workerName}`, "i")),
  ).toBeVisible({ timeout: 30_000 });
  await expect(input).toBeEnabled();

  await input.fill("why production dep?");
  await input.press("Enter");
  await expect(
    page.getByText(new RegExp(`${workerName}.*routed to.*dept_production`, "i")),
  ).toBeVisible({ timeout: 30_000 });
  await expect(input).toBeEnabled();

  await input.fill("reclassify it to QA");
  await input.press("Enter");
  await expect(
    page.getByText(
      new RegExp(
        `reclassified.*${workerName}.*dept_qa.*reset evaluation status to.*pending`,
        "i",
      ),
    ),
  ).toBeVisible({ timeout: 30_000 });
});
