import { expect, test, type Page, type BrowserContext } from "@playwright/test";
import { SignJWT } from "jose";

type Worker = {
  id: string;
  worker_id: string;
  name: string;
  status: string;
  evaluation_status?: string;
  source_repo?: string;
  sandbox_profile?: string;
  transport_mode?: string;
  adapter_entrypoint?: string;
  team_id?: string;
  version?: string;
};

type Evaluation = {
  id: string;
  verdict: string;
  risk_tier: string;
  overall_score: number;
  recommended_status: string;
  requires_human_approval: boolean;
  checks: Record<string, { passed: boolean; score?: number; status?: string; details: string }>;
};

async function authenticate(context: BrowserContext, baseURL?: string): Promise<void> {
  const url = new URL(baseURL ?? "http://127.0.0.1:4000");
  const secret = new TextEncoder().encode(
    process.env.JWT_SECRET ?? "bX0wVUKd4M214L8laNitaXJWdBgoCavZ9o0Xr/MhLnw="
  );
  const token = await new SignJWT({ sub: "e2e", role: "operator" })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime("1h")
    .sign(secret);

  await context.addCookies([
    {
      name: "mas_session",
      value: token,
      url: url.origin,
      httpOnly: false,
      sameSite: "Lax",
      secure: url.protocol === "https:",
    },
  ]);
}

async function mockHiringBoardApi(page: Page) {
  const workers: Worker[] = [
    {
      id: "00000000-0000-4000-a000-0000000000e1",
      worker_id: "baseline_worker",
      name: "Baseline Worker",
      status: "ACTIVE",
      evaluation_status: "approved",
      source_repo: "local",
      sandbox_profile: "restricted",
      transport_mode: "process",
      adapter_entrypoint: "WorkerAgent",
      team_id: "dept_qa",
      version: "1.0.0",
    },
  ];
  const evaluations = new Map<string, Evaluation[]>();

  await page.route("**/api/workers", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const body = request.postDataJSON() as Record<string, string>;
      const worker: Worker = {
        id: "00000000-0000-4000-a000-0000000000e2",
        worker_id: body.worker_id,
        name: body.name,
        status: "INACTIVE",
        evaluation_status: "pending",
        source_repo: body.source_repo,
        sandbox_profile: body.sandbox_profile,
        transport_mode: body.transport_mode,
        adapter_entrypoint: body.adapter_entrypoint,
        team_id: body.team_id,
        version: "0.1.0",
      };
      workers.push(worker);
      await route.fulfill({ status: 201, json: worker });
      return;
    }
    await route.fulfill({ json: workers });
  });

  await page.route("**/api/workers/*/evaluations**", async (route) => {
    const workerId = route.request().url().match(/\/api\/workers\/([^/]+)\/evaluations/)?.[1] ?? "";
    await route.fulfill({ json: evaluations.get(workerId) ?? [] });
  });

  await page.route("**/api/workers/*/evaluate", async (route) => {
    const workerId = route.request().url().match(/\/api\/workers\/([^/]+)\/evaluate/)?.[1] ?? "";
    const worker = workers.find((item) => item.id === workerId);
    if (worker) worker.evaluation_status = "conditional";
    const report: Evaluation = {
      id: "00000000-0000-4000-a000-000000000201",
      verdict: "CONDITIONAL",
      risk_tier: "medium",
      overall_score: 82,
      recommended_status: "PENDING_APPROVAL",
      requires_human_approval: true,
      checks: {
        provenance: { passed: true, score: 100, details: "GitHub repository metadata resolved" },
        trufflehog: {
          passed: true,
          score: 100,
          status: "SKIPPED_TOOL_UNAVAILABLE",
          details: "trufflehog binary unavailable",
        },
        semgrep: {
          passed: true,
          score: 100,
          status: "SKIPPED_TOOL_UNAVAILABLE",
          details: "semgrep binary unavailable",
        },
        sandbox_profile: { passed: true, score: 100, details: "Sandbox profile 'restricted' is valid" },
        approval: { passed: true, score: 100, details: "External workers require approval before activation" },
      },
    };
    evaluations.set(workerId, [report]);
    await route.fulfill({ json: report });
  });

  await page.route("**/api/workers/*/status", async (route) => {
    const workerId = route.request().url().match(/\/api\/workers\/([^/]+)\/status/)?.[1] ?? "";
    const worker = workers.find((item) => item.id === workerId);
    const body = route.request().postDataJSON() as { action: string; new_status?: string };
    if (!worker) {
      await route.fulfill({ status: 404, json: { error: "Worker not found" } });
      return;
    }
    if (body.action === "ACTIVATE" && worker.source_repo && worker.evaluation_status !== "approved") {
      await route.fulfill({
        status: 409,
        json: { error: "External worker activation is blocked until evaluation is approved" },
      });
      return;
    }
    if (body.action === "ACTIVATE") worker.status = "ACTIVE";
    if (body.action === "DEACTIVATE") worker.status = "INACTIVE";
    if (body.action === "DRAIN") worker.status = "DRAINING";
    await route.fulfill({ json: worker });
  });

  return { workers };
}

test("hiring board registers, evaluates, blocks, approves, deactivates, and drains candidates", async ({
  page,
  context,
  baseURL,
}) => {
  await authenticate(context, baseURL);
  const state = await mockHiringBoardApi(page);
  const candidateId = `e2e_candidate_${Date.now()}`;

  await page.goto("/workers");
  await expect(page.getByRole("heading", { name: "Hiring Board" })).toBeVisible();

  await page.getByRole("button", { name: /register worker/i }).click();
  await page.getByPlaceholder("my_worker_1").fill(candidateId);
  await page.getByPlaceholder("My Worker Agent").fill("E2E Candidate");
  await page.getByPlaceholder("What this worker does").fill("Candidate from GitHub URL");
  await page.getByPlaceholder("dept_production").fill("dept_qa");
  await page.getByPlaceholder("https://github.com/org/repo").fill("https://github.com/example/e2e-worker");
  await page.getByPlaceholder("WorkerAgent").fill("adapter.main:E2EWorker");
  await page.getByRole("button", { name: /^register worker$/i }).last().click();

  await page.getByPlaceholder("Search workers...").fill(candidateId);
  const row = page.getByRole("row", { name: new RegExp(candidateId) });
  await expect(row).toBeVisible();
  await row.click();
  await expect(page.getByText("Blocked until approval")).toBeVisible();

  await row.getByTitle("Evaluate").click();
  await expect(row.getByText("conditional")).toBeVisible();
  await row.click();
  await expect(page.getByText("Latest Evaluation")).toBeVisible();
  await expect(page.getByText("SKIPPED_TOOL_UNAVAILABLE").first()).toBeVisible();
  await expect(page.getByText("PENDING_APPROVAL")).toBeVisible();

  await row.getByTitle("Activate").click();
  await expect(page.getByText("External worker activation is blocked until evaluation is approved")).toBeVisible();

  const candidate = state.workers.find((worker) => worker.worker_id === candidateId);
  expect(candidate).toBeTruthy();
  candidate!.evaluation_status = "approved";
  await row.getByTitle("Activate").click();
  await expect(row.getByText("Active")).toBeVisible();

  await row.getByTitle("Deactivate").click();
  await expect(row.getByText("Inactive")).toBeVisible();

  await row.getByTitle("Activate").click();
  await expect(row.getByText("Active")).toBeVisible();
  await row.getByTitle("Drain").click();
  await expect(row.getByText("Draining")).toBeVisible();
});
