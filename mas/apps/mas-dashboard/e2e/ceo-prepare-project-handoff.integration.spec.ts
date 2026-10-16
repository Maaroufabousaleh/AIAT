import { randomUUID } from "node:crypto";
import { expect, test } from "@playwright/test";
import { authenticate } from "./auth";

type JsonRecord = Record<string, unknown>;

async function readJson(response: { json(): Promise<unknown> }): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

test("CEO prepare-only handoff opens an INIT project without starting work", async ({ page }) => {
  test.setTimeout(120_000);
  expect(process.env.AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH).not.toBe("true");

  await authenticate(page, "/ceo/chat");
  const api = page.request;
  const contractResponse = await api.get("http://127.0.0.1:8000/openapi.json");
  let supportsPrepareOnly = false;
  if (contractResponse.ok()) {
    const contract = asRecord(await readJson(contractResponse));
    const components = asRecord(contract.components);
    const schemas = asRecord(components.schemas);
    const requestSchema = asRecord(schemas.OperatorToCeoRequest);
    const properties = asRecord(requestSchema.properties);
    supportsPrepareOnly = Object.hasOwn(properties, "project_start_mode");
  }
  test.skip(
    !supportsPrepareOnly,
    "The running orchestrator does not expose project_start_mode; refusing to submit because it may start CEO work.",
  );

  const chat = page.getByRole("main", { name: "CEO Command Center chat" });
  const suffix = randomUUID().slice(0, 8);
  const projectName = `CEO prepare-only handoff ${suffix}`;
  const instruction = `Create a project named "${projectName}" for a small local checklist app. Save a short project brief.`;
  let projectId: string | undefined;
  let accepted = false;

  try {
    await chat.getByRole("radio", { name: "Prepare project only" }).check();
    const input = chat.getByRole("textbox", { name: "Message to CEO" });
    await input.fill(instruction);

    const prepareModeRequestPromise = page.waitForRequest((request) => {
      return new URL(request.url()).pathname === "/api/ceo/messages" && request.method() === "POST";
    });
    const acceptedResponsePromise = page.waitForResponse((response) => {
      const request = response.request();
      return new URL(response.url()).pathname === "/api/ceo/messages" && request.method() === "POST";
    });
    await input.press("Enter");

    const prepareModeRequest = await prepareModeRequestPromise;
    expect(prepareModeRequest.postDataJSON()).toMatchObject({ project_start_mode: "prepare_only" });
    const acceptedResponse = await acceptedResponsePromise;
    const acceptedBody = asRecord(await readJson(acceptedResponse));
    expect(acceptedResponse.status(), JSON.stringify(acceptedBody)).toBe(200);
    expect(acceptedBody).toMatchObject({ ok: true, status: "accepted" });
    accepted = true;

    await expect(chat.getByText(instruction, { exact: true })).toBeVisible();
    const submittedEntry = chat.getByRole("article").filter({ hasText: instruction });
    await expect(submittedEntry.getByTestId("ceo-project-mode")).toHaveText("Prepare only");

    await expect.poll(async () => {
      const listResponse = await api.get("/api/projects?limit=1000");
      if (!listResponse.ok()) return null;
      const rows = await readJson(listResponse);
      if (!Array.isArray(rows)) return null;
      const matches = rows
        .map(asRecord)
        .filter((row) => row.name === projectName && typeof row.id === "string");
      if (matches.length !== 1) return null;
      projectId = String(matches[0].id);
      return projectId;
    }, { timeout: 45_000 }).toMatch(/^[0-9a-f-]{36}$/i);

    expect(projectId).toMatch(/^[0-9a-f-]{36}$/i);

    const receiptLink = chat.getByRole("link", { name: `Open project evidence ${projectId}` });
    await expect(receiptLink).toBeVisible();
    await expect(receiptLink).toHaveAttribute("href", `/projects/${projectId}`);
    await receiptLink.click();
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}$`));

    const projectDetail = page.getByRole("main", { name: "Project detail" });
    await expect(projectDetail.getByRole("heading", { name: projectName })).toBeVisible();
    await expect(projectDetail.getByRole("status", { name: "Project state: INIT" })).toBeVisible();
    await expect(projectDetail.getByRole("button", { name: "Start CEO work" })).toBeVisible();

    const projectResponse = await api.get(`/api/projects/${projectId}`);
    const project = asRecord(await readJson(projectResponse));
    expect(projectResponse.status(), JSON.stringify(project)).toBe(200);
    expect(project).toMatchObject({ id: projectId, name: projectName, state: "INIT" });

    const contextResponse = await api.get(`/api/projects/${projectId}/context`);
    const context = await readJson(contextResponse);
    expect(contextResponse.status(), JSON.stringify(context)).toBe(200);
    expect(Array.isArray(context)).toBe(true);
    expect((context as unknown[]).map(asRecord)).toEqual(expect.arrayContaining([
      expect.objectContaining({
        item_type: "TEXT",
        name: "CEO project brief",
        content_text: instruction,
      }),
    ]));

    const workerRunsResponse = await api.get(`/api/governance/runs?project_id=${projectId}&limit=100`);
    const workerRuns = await readJson(workerRunsResponse);
    expect(workerRunsResponse.status(), JSON.stringify(workerRuns)).toBe(200);
    expect(workerRuns).toEqual([]);
    test.info().annotations.push({
      type: "prepare_only_worker_runs",
      description: `project=${projectId}; count=0`,
    });
  } finally {
    if (!projectId && accepted) {
      const deadline = Date.now() + 15_000;
      while (!projectId && Date.now() < deadline) {
        const listResponse = await api.get("/api/projects?limit=1000");
        if (listResponse.ok()) {
          const rows = await readJson(listResponse);
          if (Array.isArray(rows)) {
            const matches = rows
              .map(asRecord)
              .filter((row) => row.name === projectName && typeof row.id === "string");
            if (matches.length === 1) projectId = String(matches[0].id);
          }
        }
        if (!projectId) await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }

    if (projectId) {
      const projectResponse = await api.get(`/api/projects/${projectId}`);
      if (projectResponse.status() === 404) {
        test.info().annotations.push({
          type: "prepare_only_cleanup",
          description: `exact disposable project ${projectId} was already absent; GET=404`,
        });
      } else {
        const project = asRecord(await readJson(projectResponse));
        if (!projectResponse.ok() || project.name !== projectName || project.state !== "INIT") {
          throw new Error(`Refusing cleanup: project ${projectId} did not match the disposable INIT project`);
        }
        const workerRunsResponse = await api.get(`/api/governance/runs?project_id=${projectId}&limit=100`);
        const workerRuns = await readJson(workerRunsResponse);
        if (!workerRunsResponse.ok() || !Array.isArray(workerRuns) || workerRuns.length !== 0) {
          throw new Error(`Refusing cleanup: project ${projectId} has worker runs or run state is unavailable`);
        }
        const deleteResponse = await api.delete(`/api/projects/${projectId}`);
        if (deleteResponse.status() !== 200) {
          throw new Error(`Cleanup of exact disposable project returned HTTP ${deleteResponse.status()}`);
        }
        const verifyDeleted = await api.get(`/api/projects/${projectId}`);
        if (verifyDeleted.status() !== 404) {
          throw new Error(`Cleanup verification for exact disposable project returned HTTP ${verifyDeleted.status()}`);
        }
        test.info().annotations.push({
          type: "prepare_only_cleanup",
          description: `deleted exact project ${projectId}; state=INIT; worker runs=0; follow-up GET=404`,
        });
      }
    } else if (accepted) {
      throw new Error(`CEO accepted ${projectName}, but exact project cleanup could not recover its ID`);
    }
  }
});
