import { expect, test } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "project-file-context-upload-001";
const PROJECT_FIXTURE = {
  id: PROJECT_ID,
  name: "Project text intake",
  description: "Attach a small text brief as project context",
  state: "ACTIVE",
  created_at: "2026-10-07T00:00:00Z",
  updated_at: "2026-10-07T00:00:00Z",
};

const WORKSPACE_FIXTURE = {
  next_actions: [],
  pending_approvals: [],
  recent_activity: [],
  worker_activity: [],
  artifacts: [],
  logs: [],
  cost_usage: { available: false, reason: "fixture has no usage data" },
  repository: null,
};

test("project context accepts bounded UTF-8 text files and rejects oversized files", async ({
  page,
}) => {
  let contextItems: Array<Record<string, unknown>> = [];
  let savedPayload: Record<string, unknown> | null = null;
  let createCalls = 0;

  await page.route("**/api/**", async (route) => {
    const { pathname } = new URL(route.request().url());
    if (pathname === `/api/projects/${PROJECT_ID}`) {
      await route.fulfill({ json: PROJECT_FIXTURE });
    } else if (
      pathname === `/api/projects/${PROJECT_ID}/state-history` ||
      pathname === `/api/projects/${PROJECT_ID}/decisions`
    ) {
      await route.fulfill({ json: [] });
    } else if (pathname === `/api/projects/${PROJECT_ID}/transition`) {
      await route.fulfill({ json: [] });
    } else if (pathname === `/api/projects/${PROJECT_ID}/workspace`) {
      await route.fulfill({ json: WORKSPACE_FIXTURE });
    } else if (pathname === `/api/projects/${PROJECT_ID}/repository`) {
      await route.fulfill({ json: { workspace: null } });
    } else if (pathname === `/api/projects/${PROJECT_ID}/context`) {
      if (route.request().method() === "POST") {
        createCalls += 1;
        savedPayload = route.request().postDataJSON();
        const item = {
          ...(savedPayload ?? {}),
          id: "context-file-001",
          project_id: PROJECT_ID,
          created_at: "2026-10-07T00:00:00Z",
        };
        contextItems = [item];
        await route.fulfill({ status: 201, json: item });
      } else {
        await route.fulfill({ json: contextItems });
      }
    } else {
      await route.fulfill({ json: [] });
    }
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  await expect(page.getByRole("heading", { name: PROJECT_FIXTURE.name })).toBeVisible();
  await page.getByRole("tab", { name: "Context", exact: true }).click();
  await page.getByRole("button", { name: "Add Item" }).click();

  const fileInput = page.getByLabel("Choose a project text file");
  await fileInput.setInputFiles({
    name: "brief.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("A bounded operator supplied project brief.", "utf8"),
  });
  await expect(page.getByText("Selected brief.md", { exact: false })).toBeVisible();

  const submit = page.getByRole("button", { name: "Add Item" }).last();
  await submit.click();
  await expect(page.getByText("brief.md", { exact: true })).toBeVisible();

  expect(createCalls).toBe(1);
  expect(savedPayload).toMatchObject({
    item_type: "FILE",
    name: "brief.md",
    mime_type: "text/markdown",
    size_bytes: Buffer.byteLength("A bounded operator supplied project brief."),
    content_text: "A bounded operator supplied project brief.",
    metadata: {
      upload_mode: "inline_text_context",
      source_name: "brief.md",
      sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
    },
  });

  await page.getByRole("button", { name: "Add Item" }).first().click();
  await page.getByLabel("Choose a project text file").setInputFiles({
    name: "too-large.txt",
    mimeType: "text/plain",
    buffer: Buffer.alloc(100_001, "x"),
  });
  await page.getByRole("button", { name: "Add Item" }).last().click();

  await expect(page.getByRole("main", { name: "Project detail" }).getByRole("alert")).toHaveText(
    "Text files must be 100 KB or smaller.",
  );
  expect(createCalls).toBe(1);
});
