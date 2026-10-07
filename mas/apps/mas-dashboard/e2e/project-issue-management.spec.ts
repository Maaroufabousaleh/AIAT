import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "30000000-0000-4000-8000-000000000301";
const ISSUE_ID = "30000000-0000-4000-8000-000000000302";
const OTHER_ISSUE_ID = "30000000-0000-4000-8000-000000000303";

const project = {
  id: PROJECT_ID,
  name: "Project issue management",
  description: "A scoped fixture for the issue lifecycle UI.",
  state: "ACTIVE",
  created_at: "2026-10-07T10:00:00Z",
  updated_at: "2026-10-07T10:00:00Z",
};

const workspace = {
  next_actions: [],
  pending_approvals: [],
  recent_activity: [],
  worker_activity: [],
  artifacts: [],
  logs: [],
  cost_usage: { available: false, reason: "No usage has been recorded." },
  repository: null,
};

test("operator creates, edits, recovers a stale issue revision, and comments in one project", async ({ page }) => {
  let issue: Record<string, unknown> = {
    issue_id: ISSUE_ID,
    project_id: PROJECT_ID,
    title: "Review the local report",
    description: "Check the generated result.",
    status: "backlog",
    issue_type: "task",
    priority: "medium",
    assigned_team: null,
    assigned_agent: null,
    revision: 1,
    updated_at: "2026-10-07T10:05:00Z",
  };
  const otherIssue = {
    issue_id: OTHER_ISSUE_ID,
    project_id: "30000000-0000-4000-8000-000000000399",
    title: "Another project's private issue",
    status: "backlog",
    issue_type: "task",
    priority: "low",
  };
  const comments: Array<Record<string, unknown>> = [];
  const mutations: Array<{ method: string; path: string; body: Record<string, unknown> }> = [];
  let createdIssueCount = 0;
  let updateCount = 0;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();

    if (method === "GET") {
      if (path === `/api/projects/${PROJECT_ID}`) return route.fulfill({ json: project });
      if ([
        `/api/projects/${PROJECT_ID}/state-history`,
        `/api/projects/${PROJECT_ID}/decisions`,
        `/api/projects/${PROJECT_ID}/transition`,
      ].includes(path)) return route.fulfill({ json: [] });
      if (path === `/api/projects/${PROJECT_ID}/workspace`) return route.fulfill({ json: workspace });
      if (path === `/api/projects/${PROJECT_ID}/repository`) return route.fulfill({ json: { configured: false, workspace: null } });
      if (path === "/api/governance/runs") return route.fulfill({ json: [] });
      if (path === `/api/projects/${PROJECT_ID}/issues`) return route.fulfill({ json: [issue, otherIssue] });
      if (path.startsWith(`/api/projects/${PROJECT_ID}/issues/`) && path.endsWith(String(issue.issue_id))) {
        return route.fulfill({ json: { issue, comments, links: [] } });
      }
      return route.fulfill({ status: 404, json: { error: "Unexpected project API read" } });
    }

    const body = request.postDataJSON() as Record<string, unknown>;
    mutations.push({ method, path, body });

    if (method === "POST" && path === `/api/projects/${PROJECT_ID}/issues`) {
      expect(body.actor_id).toBeUndefined();
      expect(body.title).toBe("Prepare implementation notes");
      createdIssueCount += 1;
      issue = {
        ...issue,
        issue_id: "30000000-0000-4000-8000-000000000304",
        title: String(body.title),
        description: body.description,
        issue_type: body.issue_type,
        priority: body.priority,
        revision: 1,
      };
      return route.fulfill({ status: 201, json: { issue, projections: [] } });
    }

    if (method === "PATCH" && path === `/api/projects/${PROJECT_ID}/issues/${issue.issue_id}`) {
      expect(body.actor_id).toBeUndefined();
      updateCount += 1;
      if (updateCount === 1) {
        issue = { ...issue, title: "Concurrent operator update", revision: 2 };
        return route.fulfill({ status: 409, json: { detail: "issue changed since it was read" } });
      }
      expect(body.expected_revision).toBe(2);
      issue = { ...issue, ...body, revision: 3, updated_at: "2026-10-07T10:20:00Z" };
      return route.fulfill({ status: 200, json: { issue, projections: [] } });
    }

    if (method === "POST" && path === `/api/projects/${PROJECT_ID}/issues/${issue.issue_id}/comments`) {
      expect(body.actor_id).toBeUndefined();
      expect(body.body).toBe("Implementation finished; tests are green.");
      const comment = {
        comment_id: "30000000-0000-4000-8000-000000000305",
        body: String(body.body),
        actor_id: "operator",
        created_at: "2026-10-07T10:30:00Z",
      };
      comments.push(comment);
      return route.fulfill({ status: 201, json: { comment, issue_id: issue.issue_id, projections: [] } });
    }

    return route.fulfill({ status: 405, json: { error: "Unexpected project API mutation" } });
  });

  await authenticate(page, `/projects/${PROJECT_ID}`);
  const detail = page.getByRole("main", { name: "Project detail" });
  await expect(detail.getByRole("heading", { name: project.name })).toBeVisible();
  await expect(detail.getByText("Another project's private issue")).toHaveCount(0);

  await detail.getByRole("button", { name: "New issue" }).click();
  const createForm = detail.getByRole("form", { name: "Create project issue" });
  await createForm.getByLabel("Title").fill("Prepare implementation notes");
  await createForm.getByLabel("Description").fill("Record the bounded software task.");
  await createForm.getByRole("button", { name: "Create issue" }).click();
  await expect(detail.getByRole("status").filter({ hasText: "Issue created." })).toBeVisible();
  expect(createdIssueCount).toBe(1);

  const issueRow = detail.getByRole("list", { name: "Project issues" }).getByRole("listitem").filter({ hasText: "Prepare implementation notes" });
  await issueRow.getByRole("button", { name: "Manage issue" }).click();
  const dialog = page.getByRole("dialog", { name: "Manage project issue" });
  const updateForm = dialog.getByRole("form", { name: "Update project issue" });
  await updateForm.getByLabel("Title").fill("My first edit");
  await updateForm.getByLabel("Status").selectOption("done");
  await updateForm.getByRole("button", { name: "Save issue" }).click();
  await expect(dialog.getByRole("status")).toContainText("changed since you opened it");
  await expect(updateForm.getByLabel("Title")).toHaveValue("Concurrent operator update");

  await updateForm.getByLabel("Title").fill("Implementation notes reviewed");
  await updateForm.getByRole("button", { name: "Save issue" }).click();
  await expect(dialog.getByRole("status").filter({ hasText: "Issue updated." })).toBeVisible();
  await expect(dialog.getByText("revision 3")).toBeVisible();
  expect(updateCount).toBe(2);

  await dialog.getByLabel("Add a comment").fill("Implementation finished; tests are green.");
  await dialog.getByRole("button", { name: "Add comment" }).click();
  await expect(dialog.getByText("Implementation finished; tests are green.")).toBeVisible();
  expect(comments).toHaveLength(1);
  expect(mutations.map((entry) => entry.method)).toEqual(["POST", "PATCH", "PATCH", "POST"]);
  expect(mutations[1]?.body.expected_revision).toBe(1);
  expect(mutations[2]?.body.expected_revision).toBe(2);
  expect(mutations.every((entry) => entry.path.startsWith(`/api/projects/${PROJECT_ID}/issues`))).toBe(true);
});
