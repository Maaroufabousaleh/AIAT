import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

test("hiring board runs the live register, evaluate, approval, and status lifecycle", async ({
  page,
}) => {
  const candidateId = `e2e_candidate_${Date.now()}`;

  await authenticate(page, "/workers");
  await expect(
    page.getByRole("heading", { name: "Hiring Board" }),
  ).toBeVisible();

  await page.getByRole("button", { name: /register worker/i }).click();
  await page.getByPlaceholder("my_worker_1").fill(candidateId);
  await page.getByPlaceholder("My Worker Agent").fill("E2E Live Candidate");
  await page
    .getByPlaceholder("What this worker does")
    .fill("Live GitHub evaluation candidate");
  await page.getByPlaceholder("office_chrm").fill("dept_qa");
  await page
    .getByPlaceholder("https://github.com/org/repo")
    .fill("https://github.com/octocat/Hello-World");
  await page.getByPlaceholder("WorkerAgent").fill("adapter.main:E2EWorker");
  await page
    .getByRole("button", { name: /^register worker$/i })
    .last()
    .click();

  const search = page.getByRole("textbox", { name: "Search workers" });
  await search.fill(candidateId);
  let row = page.getByRole("row", { name: new RegExp(candidateId) });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByText("Blocked until approval")).toBeVisible();

  await row.getByRole("button", { name: `Evaluate ${candidateId}` }).click();
  await expect(row.getByText(/approved|conditional|rejected/i)).toBeVisible({
    timeout: 90_000,
  });
  await row.click();
  await expect(page.getByText("Latest Evaluation")).toBeVisible({
    timeout: 90_000,
  });
  await expect(page.getByText("provenance", { exact: true })).toBeVisible();
  await expect(page.getByText("licensing", { exact: true })).toBeVisible();

  await row.getByRole("button", { name: `Activate ${candidateId}` }).click();
  await expect(
    page.getByText(
      "External worker activation is blocked until evaluation is approved",
    ),
  ).toBeVisible();

  const approval = await page.request.post("/api/ceo/messages", {
    data: { message: `approve worker ${candidateId}` },
  });
  expect(approval.ok()).toBeTruthy();
  const approvalBody = await approval.json();
  expect(approvalBody.action?.status).toBe("approved");

  await page.reload();
  await search.fill(candidateId);
  row = page.getByRole("row", { name: new RegExp(candidateId) });
  await expect(row.getByText("approved", { exact: true })).toBeVisible();

  await row.getByRole("button", { name: `Activate ${candidateId}` }).click();
  await expect(row.getByText("Active", { exact: true })).toBeVisible();

  await row.getByRole("button", { name: `Deactivate ${candidateId}` }).click();
  await expect(row.getByText("Inactive", { exact: true })).toBeVisible();

  await row.getByRole("button", { name: `Activate ${candidateId}` }).click();
  await expect(row.getByText("Active", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: `Drain ${candidateId}` }).click();
  await expect(row.getByText("Draining", { exact: true })).toBeVisible();
});
