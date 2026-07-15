import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("CEO chat publishes a directive and receives the live orchestrator response", async ({
  page,
}) => {
  const marker = `e2e-${Date.now()}`;
  const directive = `show company status for live verification ${marker}`;

  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear" }).click();

  const input = page.getByRole("textbox");
  await input.fill(directive);
  await input.press("Enter");

  await expect(page.getByText(directive, { exact: true })).toBeVisible({
    timeout: 1_000,
  });
  await expect(page.getByTestId("ceo-thinking")).toBeVisible({ timeout: 1_000 });
  await expect(
    page.getByText(/AIAT company is (seeded|not seeded)/i),
  ).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTestId("ceo-thinking")).toHaveCount(0);

  await expect(input).toBeEnabled();
});

test("CEO chat keeps worker context through a hiring follow-up", async ({ page }) => {
  const workerName = `e2e_candidate_${Date.now()}`;

  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

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

test("CEO chat binds destructive controls to confirmation and supports cancel", async ({
  page,
}) => {
  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();

  const input = page.getByRole("textbox");
  await input.fill("shutdown system");
  await input.press("Enter");

  await expect(page.getByText("Confirmation required", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByRole("button", { name: "Confirm", exact: true })).toBeVisible();
  const cancel = page.getByRole("button", { name: "Cancel", exact: true });
  await expect(cancel).toBeVisible();
  await cancel.click();

  await expect(
    page.getByText(/Cancelled .*shut down the AIAT control plane/i),
  ).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Confirm", exact: true })).toHaveCount(0);
  await expect(input).toBeEnabled();
});

test("CEO chat reads operational controls without page-by-page navigation", async ({
  page,
}) => {
  await authenticate(page, "/ceo/chat");
  const input = page.getByRole("textbox");
  const commands = [
    ["show system status", /System is `?RUNNING`?/i],
    ["list credentials", /Credential registry \(metadata only\)/i],
    ["list flows", /Flows:.*Active instances:/i],
    ["list dead letters", /Dead-letter queue:/i],
  ] as const;

  for (const [command, expected] of commands) {
    await input.fill(command);
    await input.press("Enter");
    await expect(page.getByText(expected).last()).toBeVisible({ timeout: 15_000 });
    await expect(input).toBeEnabled();
  }
});
