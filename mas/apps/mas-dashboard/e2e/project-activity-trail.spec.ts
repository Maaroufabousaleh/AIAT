import { expect, test, type Page } from "@playwright/test";

import { authenticate } from "./auth";

const PROJECT_ID = "30000000-0000-4000-8000-000000000001";
const OTHER_PROJECT_ID = "30000000-0000-4000-8000-000000000002";
const RUN_ID = "30000000-0000-4000-8000-000000000101";

const project = {
  id: PROJECT_ID,
  name: "Project activity trail",
  description: "Shows canonical work and evidence in one project workspace",
  state: "ACTIVE",
  created_at: "2026-10-01T10:00:00Z",
  updated_at: "2026-10-02T10:00:00Z",
};

const workspace = {
  next_actions: [{ kind: "review", label: "Review the latest work", severity: "low" }],
  pending_approvals: [],
  recent_activity: [],
  worker_activity: [],
  artifacts: [
    {
      id: 41,
      path: "reports/summary.md",
      agent_id: "worker-alpha",
      size_bytes: 512,
      created_at: "2026-10-02T12:00:00Z",
      metadata: { worker_run_id: RUN_ID },
    },
    {
      id: 42,
      path: "reports/unattributed.md",
      agent_id: "worker-alpha",
      size_bytes: 256,
      created_at: "2026-10-02T12:01:00Z",
      metadata: { worker_run_id: { malformed: true } },
    },
  ],
  logs: [],
  cost_usage: {
    available: true,
    total_cost_usd: 0.25,
    llm_calls: 2,
    tool_calls: 3,
    failed_calls: 0,
    total_tokens: 950,
  },
  repository: null,
};

const projectRun = {
  id: RUN_ID,
  project_id: PROJECT_ID,
  worker_id: "worker-alpha",
  task_type: "IMPLEMENT_FEATURE",
  state: "SUCCEEDED",
  model_resolution_snapshot_id: "snapshot-2026-10-02",
  created_at: "2026-10-02T11:30:00Z",
};

const otherProjectRun = {
  ...projectRun,
  id: "30000000-0000-4000-8000-000000000102",
  project_id: OTHER_PROJECT_ID,
  worker_id: "other-worker",
};

const projectIssue = {
  issue_id: "30000000-0000-4000-8000-000000000201",
  project_id: PROJECT_ID,
  title: "Review generated summary",
  status: "IN_PROGRESS",
  issue_type: "TASK",
  priority: "HIGH",
  assigned_team: "engineering",
  updated_at: "2026-10-02T11:45:00Z",
};

const otherProjectIssue = {
  ...projectIssue,
  issue_id: "30000000-0000-4000-8000-000000000202",
  project_id: OTHER_PROJECT_ID,
  title: "Private work from another project",
};

async function mockProjectReads(
  page: Page,
  options: { failWorkerRuns?: boolean; empty?: boolean } = {},
) {
  const requests: string[] = [];
  const mutations: string[] = [];

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    requests.push(`${request.method()} ${path}${url.search}`);

    if (request.method() !== "GET") {
      mutations.push(`${request.method()} ${path}`);
      await route.fulfill({ status: 405, contentType: "application/json", body: JSON.stringify({ error: "Read-only fixture" }) });
      return;
    }

    if (path === `/api/projects/${PROJECT_ID}`) {
      await route.fulfill({ json: project });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/state-history` || path === `/api/projects/${PROJECT_ID}/decisions` || path === `/api/projects/${PROJECT_ID}/transition`) {
      await route.fulfill({ json: [] });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/workspace`) {
      await route.fulfill({ json: options.empty ? { ...workspace, artifacts: [], cost_usage: { available: false, reason: "No usage has been recorded for this project." } } : workspace });
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/repository`) {
      await route.fulfill({ json: { configured: false, workspace: null } });
      return;
    }
    if (path === `/api/governance/runs`) {
      expect(url.searchParams.get("project_id")).toBe(PROJECT_ID);
      const limit = Number(url.searchParams.get("limit"));
      expect(Number.isInteger(limit) && limit >= 1 && limit <= 100).toBe(true);
      expect(url.searchParams.get("offset")).toBe("0");
      if (options.failWorkerRuns && requests.filter((item) => item.startsWith("GET /api/governance/runs")).length === 1) {
        await route.fulfill({ status: 503, json: { error: "run read unavailable" } });
      } else {
        await route.fulfill({ json: options.empty ? [] : [projectRun, otherProjectRun] });
      }
      return;
    }
    if (path === `/api/projects/${PROJECT_ID}/issues`) {
      const limit = Number(url.searchParams.get("limit"));
      expect(Number.isInteger(limit) && limit >= 1 && limit <= 100).toBe(true);
      await route.fulfill({ json: options.empty ? [] : [projectIssue, otherProjectIssue] });
      return;
    }

    await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "Unexpected project read" }) });
  });

  return { requests, mutations };
}

test("project workspace follows scoped worker runs, issues, artifacts, and cost", async ({ page }) => {
  const { requests, mutations } = await mockProjectReads(page);

  await authenticate(page, `/projects/${PROJECT_ID}`);

  const detail = page.getByRole("main", { name: "Project detail" });
  await expect(detail.getByRole("heading", { name: project.name })).toBeVisible();
  await expect(detail.getByRole("heading", { name: "Worker Runs" })).toBeVisible();
  await expect(detail.getByText("IMPLEMENT_FEATURE")).toBeVisible();
  await expect(detail.getByText("SUCCEEDED", { exact: true })).toBeVisible();
  await expect(detail.getByText("Private work from another project")).toHaveCount(0);
  await expect(detail.getByRole("heading", { name: "Project Issues" })).toBeVisible();
  await expect(detail.getByText("Review generated summary")).toBeVisible();

  const runLink = detail.getByRole("link", { name: `Open WorkerRun ${RUN_ID}` });
  await expect(runLink).toHaveAttribute("href", `/workers/runs/${RUN_ID}`);
  await detail.getByRole("tab", { name: "Resources" }).click();
  await expect(detail.getByText("reports/summary.md")).toBeVisible();
  const artifactEvidenceLink = detail.getByRole("link", {
    name: "Inspect evidence for artifact reports/summary.md",
  });
  await expect(artifactEvidenceLink).toHaveAttribute("href", "/evidence/artifact/41");
  const artifactRunLink = detail.getByRole("link", {
    name: `Open producing WorkerRun ${RUN_ID}`,
  });
  await expect(artifactRunLink).toHaveAttribute("href", `/workers/runs/${RUN_ID}`);
  await expect(detail.getByRole("link", { name: /Open producing WorkerRun/ })).toHaveCount(1);
  await detail.getByRole("tab", { name: "Cost" }).click();
  await expect(detail.getByText("$0.25")).toBeVisible();
  await expect(detail.getByText("950", { exact: true })).toBeVisible();
  await detail.getByRole("tab", { name: "Activity" }).click();
  await runLink.click();
  await expect(page).toHaveURL(new RegExp(`/workers/runs/${RUN_ID}$`));

  expect(requests.some((request) => request.startsWith(`GET /api/governance/runs?`) && request.includes(`project_id=${PROJECT_ID}`))).toBe(true);
  expect(requests.some((request) => request.startsWith(`GET /api/projects/${PROJECT_ID}/issues?`))).toBe(true);
  expect(mutations).toEqual([]);
});

test("project workspace gives useful empty and read-error states", async ({ page }) => {
  await mockProjectReads(page, { failWorkerRuns: true, empty: true });
  await authenticate(page, `/projects/${PROJECT_ID}`);

  const detail = page.getByRole("main", { name: "Project detail" });
  await expect(detail.getByText("Worker runs could not be loaded (HTTP 503).")).toBeVisible();
  await expect(detail.getByRole("button", { name: "Retry project activity" }).first()).toBeVisible();
  await detail.getByRole("button", { name: "Retry project activity" }).first().click();
  await expect(detail.getByText("No worker runs are recorded for this project yet.")).toBeVisible();
  await expect(detail.getByText("No issues are recorded for this project yet.")).toBeVisible();

  await detail.getByRole("tab", { name: "Resources" }).click();
  await expect(detail.getByText("No artifacts registered for this project.")).toBeVisible();
  await detail.getByRole("tab", { name: "Cost" }).click();
  await expect(detail.getByText("No usage has been recorded for this project.")).toBeVisible();
});
