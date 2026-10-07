import { expect, test, type Page } from "@playwright/test";
import { authenticate } from "./auth";

const CONNECTION_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_CONNECTION_ID = "22222222-2222-4222-8222-222222222222";
const PROJECT_ID = "33333333-3333-4333-8333-333333333333";
const OTHER_PROJECT_ID = "44444444-4444-4444-8444-444444444444";
const BINDING_ID = "55555555-5555-4555-8555-555555555555";
const OTHER_CONNECTION_BINDING_ID = "66666666-6666-4666-8666-666666666666";
const OTHER_PROJECT_BINDING_ID = "77777777-7777-4777-8777-777777777777";

type BindingResult = { status: number; body: unknown };
type BindingSource = BindingResult | (() => BindingResult);

async function mockIntegrationReads(page: Page, bindings: BindingSource) {
  let currentBindings = bindings;
  const mutatingRequests: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if ((url.pathname.startsWith("/api/integrations/pm") || url.pathname === "/api/projects")
      && request.method() !== "GET") {
      mutatingRequests.push(`${request.method()} ${url.pathname}`);
    }
  });

  await page.route("**/api/projects?limit=1000", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify([{ id: PROJECT_ID, name: "Local project", state: "ACTIVE" }]),
  }));

  await page.route("**/api/integrations/pm**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === `/api/integrations/pm/projects/${PROJECT_ID}/bindings`) {
      const result = typeof currentBindings === "function" ? currentBindings() : currentBindings;
      return route.fulfill({
        status: result.status,
        contentType: "application/json",
        body: JSON.stringify(result.body),
      });
    }
    if (url.pathname === "/api/integrations/pm") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          connections: [
            { id: CONNECTION_ID, display_name: "Selected provider", provider_kind: "youtrack", status: "READ_ONLY" },
            { id: OTHER_CONNECTION_ID, display_name: "Other provider", provider_kind: "github", status: "SHADOW" },
          ],
          conflicts: [],
          outbox: [],
          runs: [],
          lifecyclePlans: [],
        }),
      });
    }
    return route.fulfill({ status: 404, body: "Not mocked" });
  });

  await authenticate(page, "/integrations");
  const integrations = page.getByRole("main", { name: "PM integrations" });
  await integrations.getByLabel("Project to provision").selectOption(PROJECT_ID);
  await integrations.getByLabel("Project provider connection").selectOption(CONNECTION_ID);
  return {
    integrations,
    mutatingRequests,
    setBindingSource(source: BindingSource) { currentBindings = source; },
  };
}

test("integration discovery shows only selected project and connection mappings with shared DLQ context", async ({ page }) => {
  const { integrations, mutatingRequests } = await mockIntegrationReads(page, {
    status: 200,
    body: [
      {
        id: BINDING_ID,
        project_id: PROJECT_ID,
        connection_id: CONNECTION_ID,
        external_project_key: "OPS",
        external_repository: "aiat/operations",
        mapping_profile: "dedicated_project",
        direction: "BIDIRECTIONAL",
        status: "READ_ONLY",
        provisioning_state: "VERIFIED",
        activation_blockers: ["Human review is required"],
      },
      {
        id: OTHER_CONNECTION_BINDING_ID,
        project_id: PROJECT_ID,
        connection_id: OTHER_CONNECTION_ID,
        external_project_key: "OTHER-CONNECTION",
        mapping_profile: "umbrella_issues",
        direction: "INBOUND",
        status: "SHADOW",
        provisioning_state: "PROJECTED",
      },
      {
        id: OTHER_PROJECT_BINDING_ID,
        project_id: OTHER_PROJECT_ID,
        connection_id: CONNECTION_ID,
        external_project_key: "OTHER-PROJECT",
        mapping_profile: "dedicated_project",
        direction: "OUTBOUND",
        status: "ACTIVE",
        provisioning_state: "VERIFIED",
      },
    ],
  });

  const mappings = integrations.locator('[aria-label="Project provider bindings"]');
  const selectedBinding = mappings.getByRole("article", { name: `Project provider binding ${BINDING_ID}` });
  await expect(selectedBinding).toContainText("OPS");
  await expect(selectedBinding).toContainText("aiat/operations");
  await expect(selectedBinding).toContainText("dedicated_project");
  await expect(selectedBinding).toContainText("BIDIRECTIONAL");
  await expect(selectedBinding).toContainText("Human review is required");
  await expect(mappings).not.toContainText(OTHER_CONNECTION_BINDING_ID);
  await expect(mappings).not.toContainText(OTHER_PROJECT_BINDING_ID);
  await expect(mappings.getByRole("link", { name: "Open project" })).toHaveAttribute("href", `/projects/${PROJECT_ID}`);

  await expect(integrations.getByText(/no connection-scoped FAILED or DEAD_LETTER collection\/detail route/i)).toBeVisible();
  await expect(integrations.getByRole("link", { name: "Open shared dead-letter review" })).toHaveAttribute("href", "/dlq");
  await expect(integrations.getByText(/shared queue is not attributed to the selected provider connection/i)).toBeVisible();
  expect(mutatingRequests).toEqual([]);
});

test("integration discovery explains an empty mapping list", async ({ page }) => {
  const { integrations } = await mockIntegrationReads(page, { status: 200, body: [] });

  await expect(integrations.locator('[aria-label="Project provider bindings"]'))
    .toContainText("No provider mappings are recorded for this project and connection.");
});

test("integration discovery provides a retry for mapping read errors", async ({ page }) => {
  const { integrations, setBindingSource } = await mockIntegrationReads(page, { status: 503, body: { detail: "unavailable" } });

  const mappings = integrations.locator('[aria-label="Project provider bindings"]');
  await expect(mappings.getByRole("alert")).toContainText("HTTP 503");
  setBindingSource({ status: 200, body: [] });
  await mappings.getByRole("button", { name: "Retry binding read" }).click();
  await expect(mappings).toContainText("No provider mappings are recorded for this project and connection.");
});
