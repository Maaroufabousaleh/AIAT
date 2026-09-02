import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const FLOW_FIXTURE = {
  id: "flow-editor-recovery",
  name: "Recovery Flow",
  description: "A flow used to verify editor recovery states.",
  version: 1,
  created_by: "e2e",
  is_active: true,
  created_at: "2026-08-11T00:00:00Z",
  updated_at: "2026-08-11T00:00:00Z",
  definition_json: {
    schema_version: "aiat.flow-node-schemas.v1.0",
    nodes: [
      {
        id: "start",
        type: "start",
        label: "Start",
        config: {},
        position: { x: 80, y: 80 },
      },
      {
        id: "end",
        type: "end",
        label: "End",
        config: {},
        position: { x: 280, y: 80 },
      },
    ],
    edges: [{ id: "edge-start-end", source: "start", target: "end" }],
  },
};

test("flow editor exposes first-load and stale refresh recovery", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: "[]",
    });
  });
  await page.route("**/api/flows/flow-editor-recovery", async (route) => {
    requestCount += 1;
    if (requestCount === 1 || requestCount === 3) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ error: "flow fixture unavailable" }),
      });
      return;
    }
    const payload =
      requestCount >= 4
        ? { ...FLOW_FIXTURE, name: "Recovered Flow", version: 2 }
        : FLOW_FIXTURE;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });

  await authenticate(page, "/flows/flow-editor-recovery");
  await expect(
    page.getByRole("heading", { name: "Flow unavailable" }),
  ).toBeVisible();
  await expect(page.getByText("flow fixture unavailable")).toBeVisible();

  await page.getByTestId("flow-load-retry").click();
  await expect(page.getByTestId("flow-name-input")).toHaveValue(
    "Recovery Flow",
  );
  await expect(page.locator(".react-flow__node")).toHaveCount(2);

  await page.getByRole("button", { name: "Refresh flow" }).click();
  await expect(page.getByTestId("flow-editor-stale")).toBeVisible();
  await expect(page.getByTestId("flow-name-input")).toHaveValue(
    "Recovery Flow",
  );

  await page
    .getByTestId("flow-editor-stale")
    .getByRole("button", { name: "Retry" })
    .click();
  await expect(page.getByTestId("flow-editor-stale")).toHaveCount(0);
  await expect(page.getByTestId("flow-name-input")).toHaveValue(
    "Recovered Flow",
  );
  await expect(page.locator(".react-flow__node")).toHaveCount(2);

  await expect(page.getByRole("main", { name: "Flow editor" })).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "Flow node palette" }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "Flow canvas" })).toBeVisible();
  for (const control of [
    page.getByRole("link", { name: "Back to flows" }),
    page.getByRole("textbox", { name: "Flow name" }),
    page.getByRole("button", { name: "Refresh flow" }),
    page.getByRole("button", { name: "Undo last change" }),
    page.getByRole("button", { name: "Redo last undone change" }),
    page.getByRole("checkbox", { name: "Mark flow as active" }),
    page.getByRole("button", { name: "Save", exact: true }),
    page.getByRole("button", { name: "Save As New Version" }),
    page.getByRole("button", { name: "Add Task node" }),
  ]) {
    await expect(control).toHaveCSS("min-height", "44px");
  }

  await page.locator(".react-flow__node").first().click();
  const nodeConfiguration = page.getByRole("complementary", {
    name: "Node configuration",
  });
  await expect(nodeConfiguration).toBeVisible();
  await expect(
    nodeConfiguration.getByRole("button", { name: "Close node config panel" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    nodeConfiguration.getByRole("textbox", { name: "Node label" }),
  ).toHaveCSS("min-height", "44px");
  await expect(
    nodeConfiguration.getByRole("button", { name: "Delete Node" }),
  ).toHaveCSS("min-height", "44px");
  await nodeConfiguration
    .getByRole("button", { name: "Close node config panel" })
    .click();
  await expect(nodeConfiguration).toHaveCount(0);
  expect(requestCount).toBe(4);
});

test("flow editor fails closed when the initial flow read is denied", async ({
  page,
}) => {
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flows/flow-editor-recovery", async (route) => {
    await route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "flow editor access denied" }),
    });
  });

  await authenticate(page, "/flows/flow-editor-recovery");
  await expect(
    page.getByRole("heading", { level: 1, name: "Flow editor access denied" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Flow editor access status" }),
  ).toContainText("cannot be read while authorization is unavailable");
  await expect(page.getByRole("link", { name: "Back to flows" })).toBeVisible();
  await expect(page.getByTestId("flow-load-retry")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh flow" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add Task node" })).toHaveCount(0);
});

test("flow editor preserves the last canvas but hides controls after read denial", async ({
  page,
}) => {
  let denyNextRead = false;
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flows/flow-editor-recovery", async (route) => {
    if (denyNextRead && route.request().method() === "GET") {
      denyNextRead = false;
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: "flow editor authorization expired" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FLOW_FIXTURE),
    });
  });

  await authenticate(page, "/flows/flow-editor-recovery");
  await expect(page.getByTestId("flow-name-input")).toHaveValue("Recovery Flow");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);

  denyNextRead = true;
  await page.getByRole("button", { name: "Refresh flow" }).click();
  await expect(
    page.getByRole("region", { name: "Flow editor access status" }),
  ).toContainText("last successfully loaded flow remains visible");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await expect(page.getByTestId("flow-name-input")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh flow" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save As New Version" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add Task node" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Undo last change" })).toHaveCount(0);
  await expect(page.getByRole("checkbox", { name: "Mark flow as active" })).toHaveCount(0);
});

test("flow editor enters the same denied state when saving loses authorization", async ({
  page,
}) => {
  await page.route("**/api/workers", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/governance/model-profiles", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/api/flows/flow-editor-recovery", async (route) => {
    if (route.request().method() === "PUT") {
      await route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ error: "flow editor save denied" }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(FLOW_FIXTURE),
    });
  });

  await authenticate(page, "/flows/flow-editor-recovery");
  await expect(page.getByTestId("flow-name-input")).toHaveValue("Recovery Flow");
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(
    page.getByRole("region", { name: "Flow editor access status" }),
  ).toContainText("last successfully loaded flow remains visible");
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save As New Version" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add Task node" })).toHaveCount(0);
});
