import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

async function mockHealthyCatalogues(page: Page) {
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flow-templates", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ templates: [] }),
    });
  });
}

test("new flow hides all editing controls when a catalogue read is denied", async ({
  page,
}) => {
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "worker catalogue access denied" }),
    });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flow-templates", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ templates: [] }),
    });
  });

  await authenticate(page, "/flows/new");

  const access = page.getByRole("region", { name: "Flow creation access status" });
  await expect(access).toBeVisible();
  await expect(access.getByRole("heading", { name: "Flow creation access denied" })).toBeVisible();
  await expect(access).toContainText("governed worker or Model Profile catalogue");
  await expect(page.getByRole("link", { name: "Back to flows list" })).toBeVisible();
  await expect(page.getByTestId("flow-save-button")).toHaveCount(0);
  await expect(page.getByTestId("flow-dry-run-button")).toHaveCount(0);
  await expect(page.getByTestId("add-node-task")).toHaveCount(0);
  await expect(page.getByTestId("flow-name-input")).toHaveAttribute("readonly", "");
});

test("new flow enters the denied state when creation loses authorization", async ({
  page,
}) => {
  await mockHealthyCatalogues(page);
  await page.route("**/api/flows", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "flow creation access denied" }),
    });
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("flow-name-input").fill("Denied flow");
  await page.getByTestId("add-node-start").click();
  await page.getByTestId("add-node-end").click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page.getByTestId("flow-save-button").click();

  const access = page.getByRole("region", { name: "Flow creation access status" });
  await expect(access).toContainText("flow creation boundary denied access");
  await expect(page.getByTestId("flow-save-button")).toHaveCount(0);
  await expect(page.getByTestId("flow-dry-run-button")).toHaveCount(0);
  await expect(page.getByTestId("add-node-task")).toHaveCount(0);
  await expect(page.getByTestId("flow-name-input")).toHaveAttribute("readonly", "");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
});

test("new flow enters the denied state when readiness validation loses authorization", async ({
  page,
}) => {
  await mockHealthyCatalogues(page);
  await page.route("**/api/flows/dry-run", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ error: "flow validation authorization expired" }),
    });
  });

  await authenticate(page, "/flows/new");
  await page.getByTestId("add-node-start").click();
  await page.getByTestId("flow-dry-run-button").click();

  const access = page.getByRole("region", { name: "Flow creation access status" });
  await expect(access).toContainText("flow readiness validation boundary denied access");
  await expect(page.getByTestId("flow-dry-run-button")).toHaveCount(0);
  await expect(page.getByTestId("flow-save-button")).toHaveCount(0);
  await expect(page.getByTestId("add-node-task")).toHaveCount(0);
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
});
