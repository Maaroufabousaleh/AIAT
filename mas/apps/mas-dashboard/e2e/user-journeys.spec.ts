import { expect, test, type Page } from "@playwright/test";
import { authenticate } from "./auth";

/**
 * General user-journey coverage (live stack, no mocks).
 *
 * These tests exercise the product the way an operator uses it:
 * create a project from the Projects page, then drive a project purely
 * by talking to the CEO. All fixtures use `aiat_e2e_project_*` names so
 * global setup/teardown can reclaim them, and each test deletes its own
 * project when it finishes.
 *
 * Run against the dev host with:
 *   PLAYWRIGHT_BASE_URL=http://127.0.0.1:14000 \
 *   JWT_SECRET=$(grep -E '^JWT_SECRET=' ../../.env | cut -d= -f2-) \
 *   npx playwright test e2e/user-journeys.spec.ts
 */

test.describe.configure({ mode: "serial" });

function journeyName(tag: string): string {
  return `aiat_e2e_project_journey_${tag}_${Date.now()}`;
}
async function deleteProjectInPage(page: Page, projectId: string): Promise<void> {
  await page.evaluate(async (id) => {
    const res = await fetch(`/api/projects/${id}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      throw new Error(`cleanup DELETE failed: HTTP ${res.status}`);
    }
  }, projectId);
}

test("operator creates a prepared project from the Projects page and opens its workspace", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const name = journeyName("ui");
  const goal = "Journey probe: small local checklist app for operator verification.";

  await authenticate(page, "/projects");
  const main = page.getByRole("main", { name: "Projects" }).or(page.locator(".dashboard-page"));
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await page.getByRole("button", { name: "New Project" }).first().click();

  const dialog = page.getByRole("heading", { name: "New Project" });
  await expect(dialog).toBeVisible();
  await page.getByPlaceholder("my-project").fill(name);
  await page.getByPlaceholder("What should the agents build?").fill(goal);
  // Prepare only: no feasibility dispatch, no provider spend.
  await page.getByLabel("Start CEO feasibility after creation").uncheck();
  const companySelect = page.getByLabel("Project company");
  await expect(companySelect).toBeEnabled({ timeout: 15_000 });
  const selectedCompany = await companySelect.inputValue();
  expect(selectedCompany).not.toBe("");
  await page.getByRole("button", { name: "Create", exact: true }).click();

  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible({ timeout: 30_000 });

  const detailLink = row.getByRole("link", { name: `Open ${name}` });
  const href = await detailLink.getAttribute("href");
  expect(href).toMatch(/^\/projects\//);
  const projectId = href!.split("/projects/")[1].split(/[?#]/)[0];

  try {
    await detailLink.click();
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}`));
    await expect(page.getByText(name).first()).toBeVisible({ timeout: 15_000 });
    // Prepared project: INIT state, workspace tabs available, no runs started.
    await expect(page.getByText(/INIT/).first()).toBeVisible({ timeout: 15_000 });
  } finally {
    await deleteProjectInPage(page, projectId);
  }

  await page.goto("/projects");
  await expect(page.getByRole("row", { name: new RegExp(name) })).toHaveCount(0, { timeout: 15_000 });
});

test("operator creates and inspects a project purely by talking to the CEO", async ({
  page,
}) => {
  test.setTimeout(180_000);
  expect(process.env.AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH).not.toBe("true");
  const name = journeyName("ceo");

  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 15_000 });

  // Prepare-only mode: the CEO saves the brief and starts no work.
  await page.getByLabel("Prepare project only").check();
  const input = page.getByLabel("Message to CEO");
  await input.fill(
    `Create a project named "${name}" for a small local checklist app. Save a short project brief.`,
  );
  await input.press("Enter");

  await expect(page.getByText(`Create a project named "${name}"`, { exact: false }).first()).toBeVisible({
    timeout: 5_000,
  });
  // CEO acknowledges the created project; allow time for live orchestration.
  await expect(page.getByText(new RegExp(name)).last()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId("ceo-thinking")).toHaveCount(0, { timeout: 90_000 });

  const evidenceLink = page.getByTestId("ceo-evidence-link").last();
  await expect(evidenceLink).toBeVisible({ timeout: 10_000 });
  const href = await evidenceLink.getAttribute("href");
  expect(href).toMatch(/^\/projects\//);
  const projectId = href!.split("/projects/")[1].split(/[?#]/)[0];

  try {
    await evidenceLink.click();
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}`));
    await expect(page.getByText(name).first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/INIT/).first()).toBeVisible({ timeout: 15_000 });

    // Follow-up through talk: ask the CEO about the project it just made.
    await page.goto("/ceo/chat");
    await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 15_000 });
    const followUp = page.getByLabel("Message to CEO");
    await followUp.fill(`What is the current state of project "${name}"?`);
    await followUp.press("Enter");
    await expect(page.locator("article", { hasText: name }).filter({ hasText: /INIT/i }).last()).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("ceo-thinking")).toHaveCount(0, { timeout: 90_000 });
  } finally {
    await deleteProjectInPage(page, projectId);
  }
});

test("operator gets company status from the CEO quick command", async ({ page }) => {
  test.setTimeout(120_000);
  await authenticate(page, "/ceo/chat");
  await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 15_000 });

  const input = page.getByLabel("Message to CEO");
  await expect(input).toBeEnabled();
  await input.fill("Show me the live company status and tell me what needs attention.");
  await input.press("Enter");
  await expect(page.getByText(/AIAT company is (seeded|not seeded)/i).last()).toBeVisible({
    timeout: 90_000,
  });
  await expect(page.getByTestId("ceo-thinking")).toHaveCount(0, { timeout: 90_000 });
  await expect(input).toBeEnabled();
});
