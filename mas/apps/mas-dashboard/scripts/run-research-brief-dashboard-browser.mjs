import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { createHash } from "node:crypto";
import { cp, lstat, mkdir, mkdtemp, readFile, readdir, realpath, rm, symlink } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SignJWT } from "jose";
import { chromium } from "playwright";

const dashboardRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiUrl = process.env.ORCHESTRATOR_URL ?? "";
const projectId = process.argv[2] ?? "";
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const syntheticApiKey = "synthetic-research-brief-operator-key";
const syntheticJwtSecret = "synthetic-research-brief-dashboard-session-secret";
const reportPath = "reports/research-brief.md";
const timeoutMs = 20_000;

function validateDestination() {
  let parsed;
  try {
    parsed = new URL(apiUrl);
  } catch {
    throw new Error("invalid_test_api_destination");
  }
  if (
    parsed.protocol !== "http:" ||
    parsed.hostname !== "127.0.0.1" ||
    !parsed.port ||
    Number(parsed.port) < 32768 ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    !uuidPattern.test(projectId)
  ) {
    throw new Error("destination_must_be_ephemeral_loopback_with_project_uuid");
  }
  return parsed.origin;
}

const apiOrigin = validateDestination();

async function copyTree(source, destination) {
  const info = await lstat(source);
  if (info.isSymbolicLink()) throw new Error("source_symlink_rejected");
  if (info.isDirectory()) {
    await mkdir(destination, { recursive: true });
    for (const entry of await readdir(source, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === ".next" || entry.name === ".tmp" || entry.name === "test-results" || entry.name === "playwright-report") continue;
      if (/^\.env(?:\.|$)/i.test(entry.name)) continue;
      await copyTree(path.join(source, entry.name), path.join(destination, entry.name));
    }
    return;
  }
  if (!info.isFile()) throw new Error("unsupported_source_entry_rejected");
  await mkdir(path.dirname(destination), { recursive: true });
  await cp(source, destination);
}

async function buildCleanDashboardCopy() {
  const temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "aiat-research-brief-dashboard-"));
  const dashboardCopy = path.join(temporaryRoot, "dashboard");
  await mkdir(dashboardCopy, { recursive: true });
  const allowedTopLevel = [
    "app",
    "components",
    "lib",
    "public",
    "next.config.mjs",
    "next-env.d.ts",
    "package.json",
    "package-lock.json",
    "postcss.config.js",
    "proxy.ts",
    "tailwind.config.js",
    "tsconfig.json",
  ];
  for (const name of allowedTopLevel) {
    await copyTree(path.join(dashboardRoot, name), path.join(dashboardCopy, name));
  }
  const originalModules = await realpath(path.join(dashboardRoot, "node_modules"));
  const modulesTarget = path.join(dashboardCopy, "node_modules");
  await symlink(originalModules, modulesTarget, process.platform === "win32" ? "junction" : "dir");
  if ((await realpath(modulesTarget)) !== originalModules) throw new Error("dependency_link_mismatch");

  const forbiddenFiles = [];
  async function verifyCopy(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) {
        if (entry.name !== "node_modules") throw new Error("unexpected_copy_symlink");
        continue;
      }
      if (/^\.env(?:\.|$)/i.test(entry.name) || /\.(?:pem|p12|pfx|key)$/i.test(entry.name)) {
        forbiddenFiles.push(entry.name);
      }
      if (entry.isDirectory()) await verifyCopy(absolute);
    }
  }
  await verifyCopy(dashboardCopy);
  if (forbiddenFiles.length) throw new Error("secret_file_name_in_test_copy");
  return { temporaryRoot, dashboardCopy };
}

async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("test_port_unavailable");
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return address.port;
}

function childEnvironment() {
  const systemRoot = process.env.SYSTEMROOT ?? "C:\\Windows";
  const systemPath = path.join(systemRoot, "System32");
  const environment = {
    PATH: `${path.dirname(process.execPath)}${path.delimiter}${systemPath}`,
    SYSTEMROOT: systemRoot,
    WINDIR: process.env.WINDIR ?? systemRoot,
    TEMP: process.env.TEMP ?? os.tmpdir(),
    TMP: process.env.TMP ?? os.tmpdir(),
    NODE_ENV: "development",
    NEXT_TELEMETRY_DISABLED: "1",
    ORCHESTRATOR_URL: apiUrl,
    AIAT_OPERATOR_API_KEY: syntheticApiKey,
    JWT_SECRET: syntheticJwtSecret,
  };
  return environment;
}

function nextExitCategory(text) {
  if (/Cannot find module|ERR_MODULE_NOT_FOUND/i.test(text)) return "next_dependency_missing";
  if (/SWC|next-swc/i.test(text)) return "next_native_compiler_unavailable";
  if (/EADDRINUSE|address already in use/i.test(text)) return "next_test_port_unavailable";
  if (/next\.config|Failed to load/i.test(text)) return "next_config_load_failed";
  return "next_server_initialization_failed";
}

async function waitForNext(child, baseUrl, getOutput) {
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(nextExitCategory(getOutput()));
    try {
      const response = await fetch(`${baseUrl}/login`, { signal: AbortSignal.timeout(1_500) });
      if (response.status < 500) return;
    } catch {
      // A short bounded retry is needed while the local dev server binds.
    }
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error("next_server_ready_timeout");
}

async function readJsonInPage(page, url) {
  return page.evaluate(async (target) => {
    const response = await fetch(target, { cache: "no-store" });
    const body = await response.json().catch(() => null);
    return { status: response.status, body };
  }, url);
}

async function waitForRun(page, taskType) {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    const response = await readJsonInPage(
      page,
      `/api/governance/runs?project_id=${encodeURIComponent(projectId)}&limit=50&offset=0`,
    );
    if (response.status !== 200 || !Array.isArray(response.body)) throw new Error("worker_run_list_unavailable");
    const run = response.body.find((candidate) => candidate.task_type === taskType);
    if (run && ["SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT"].includes(String(run.state).toUpperCase())) {
      if (run.state !== "SUCCEEDED") throw new Error(`synthetic_${taskType.replaceAll(".", "_")}_did_not_succeed`);
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`synthetic_${taskType.replaceAll(".", "_")}_timeout`);
}

async function refreshFlowRun(page, nodeId) {
  const applyButton = page.getByTestId(`apply-worker-run-${nodeId}`);
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    if (await applyButton.isVisible().catch(() => false)) return;
    const refresh = page.getByRole("button", { name: "Refresh run status" });
    await refresh.click({ timeout: 5_000 });
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`ui_did_not_refresh_${nodeId}_terminal_run`);
}

async function main() {
  let temporaryRoot;
  let server;
  let browser;
  let currentStage = "clean_copy";
  try {
    const copy = await buildCleanDashboardCopy();
    temporaryRoot = copy.temporaryRoot;
    const port = await reservePort();
    const baseUrl = `http://localhost:${port}`;
    const nextEntry = path.join(copy.dashboardCopy, "node_modules", "next", "dist", "bin", "next");
    let serverOutput = "";
    server = spawn(
      process.execPath,
      [nextEntry, "dev", "--webpack", "--hostname", "localhost", "--port", String(port)],
      { cwd: copy.dashboardCopy, env: childEnvironment(), stdio: ["ignore", "pipe", "pipe"] },
    );
    const captureStartupOutput = (chunk) => {
      serverOutput = (serverOutput + String(chunk)).slice(-12_000);
    };
    server.stdout?.on("data", captureStartupOutput);
    server.stderr?.on("data", captureStartupOutput);
    currentStage = "next_server_start";
    await waitForNext(server, baseUrl, () => serverOutput);

    currentStage = "browser_start";
    const chromePath = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
    const launchOptions = { headless: true };
    try {
      await lstat(chromePath);
      launchOptions.executablePath = chromePath;
    } catch {
      // Let Playwright use its pinned local browser when system Chrome is absent.
    }
    browser = await chromium.launch(launchOptions);
    const context = await browser.newContext({ acceptDownloads: true });
    const blockedOrigins = [];
    await context.route("**/*", async (route) => {
      const target = new URL(route.request().url());
      const allowed = target.origin === baseUrl || target.origin === apiOrigin;
      if (!allowed) {
        blockedOrigins.push(target.origin);
        await route.abort("blockedbyclient");
        return;
      }
      await route.continue();
    });
    const token = await new SignJWT({ sub: "dashboard-operator", role: "operator" })
      .setProtectedHeader({ alg: "HS256" })
      .setIssuedAt()
      .setExpirationTime("10m")
      .sign(new TextEncoder().encode(syntheticJwtSecret));
    await context.addCookies([
      { name: "mas_session", value: token, url: baseUrl, httpOnly: false, sameSite: "Lax" },
    ]);
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutMs);

    currentStage = "load_project";
    const projectUrl = `${baseUrl}/projects/${projectId}`;
    await page.goto(projectUrl, { waitUntil: "domcontentloaded" });
    await page.getByTestId("project-tab-flow").waitFor({ state: "visible" });
    await page.getByTestId("project-tab-flow").click();

    currentStage = "start_research_flow";
    await page.getByTestId("flow-start-button").waitFor({ state: "visible" });
    await page.getByTestId("flow-start-button").click();
    await page.getByTestId("current-node-label").waitFor({ state: "visible" });
    assert.equal((await page.getByTestId("current-node-label").innerText()).trim(), "Start");
    await page.getByTestId("complete-node-button").click();

    currentStage = "research_run";
    await page.getByTestId("active-worker-run-research").waitFor({ state: "visible" });
    const researchRun = await waitForRun(page, "research.collect");
    await refreshFlowRun(page, "research");
    await page.getByTestId("apply-worker-run-research").click();

    currentStage = "operator_review_wait";
    try {
      await page.getByTestId("approval-approve-button").waitFor({ state: "visible" });
    } catch {
      const instanceResponse = await readJsonInPage(page, `/api/projects/${projectId}/flow-instance`);
      if (instanceResponse.status !== 200 || !instanceResponse.body?.id) {
        throw new Error("operator_review_instance_unavailable");
      }
      const [executionResponse, decisionResponse] = await Promise.all([
        readJsonInPage(page, `/api/flows/instances/${instanceResponse.body.id}/executions`),
        readJsonInPage(page, `/api/projects/${projectId}/decisions`),
      ]);
      const executions = Array.isArray(executionResponse.body) ? executionResponse.body : [];
      const decisionBody = decisionResponse.body;
      const decisions = Array.isArray(decisionBody)
        ? decisionBody
        : Array.isArray(decisionBody?.decisions)
          ? decisionBody.decisions
          : [];
      const activeApproval = executions.find(
        (execution) => execution.node_id === "review" && execution.node_type === "approval" && execution.status === "RUNNING",
      );
      if (!activeApproval) throw new Error("operator_review_execution_not_active");
      const pendingGate = decisions.find(
        (decision) => decision.status === "PENDING" && String(decision.flow_node_execution_id) === String(activeApproval.id),
      );
      if (!pendingGate) throw new Error("operator_review_gate_not_pending");
      throw new Error("operator_review_gate_not_rendered");
    }
    currentStage = "operator_review_click";
    const approvalResponsePromise = page.waitForResponse(
      (response) => response.url() === `${baseUrl}/api/projects/${projectId}/decisions` && response.request().method() === "POST",
    );
    await page.getByTestId("approval-approve-button").click();
    const approvalResponse = await approvalResponsePromise;
    if (!approvalResponse.ok()) throw new Error(`operator_review_http_${approvalResponse.status()}`);

    currentStage = "report_run_active_wait";
    try {
      await page.getByTestId("active-worker-run-report").waitFor({ state: "visible" });
    } catch {
      const instanceResponse = await readJsonInPage(page, `/api/projects/${projectId}/flow-instance`);
      const executionsResponse = instanceResponse.body?.id
        ? await readJsonInPage(page, `/api/flows/instances/${instanceResponse.body.id}/executions`)
        : { status: 0, body: [] };
      const runResponse = await readJsonInPage(
        page,
        `/api/governance/runs?project_id=${encodeURIComponent(projectId)}&limit=50&offset=0`,
      );
      const activeReport = Array.isArray(instanceResponse.body?.active_node_ids)
        && instanceResponse.body.active_node_ids.includes("report");
      const reportExecution = Array.isArray(executionsResponse.body)
        && executionsResponse.body.some((row) => row.node_id === "report" && row.status === "RUNNING");
      const reportRun = Array.isArray(runResponse.body)
        && runResponse.body.some((row) => row.task_type === "research.report");
      throw new Error(
        `report_activation_${activeReport ? "active" : "inactive"}_${reportExecution ? "execution" : "no_execution"}_${reportRun ? "run" : "no_run"}`,
      );
    }
    currentStage = "report_run_wait";
    const reportRun = await waitForRun(page, "research.report");
    currentStage = "report_run_refresh";
    await refreshFlowRun(page, "report");
    currentStage = "report_run_apply_enabled_check";
    const applyReportButton = page.getByTestId("apply-worker-run-report");
    if (!(await applyReportButton.isEnabled())) throw new Error("report_apply_button_disabled");
    await applyReportButton.scrollIntoViewIfNeeded();
    currentStage = "report_run_apply_hit_test";
    const reportButtonHit = await applyReportButton.evaluate((button) => {
      const rect = button.getBoundingClientRect();
      const target = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      const targetTestId = target?.closest("[data-testid]")?.getAttribute("data-testid") ?? "";
      return {
        visible: rect.width > 0 && rect.height > 0,
        receivesPointer: target === button || button.contains(target),
        targetTestId,
      };
    });
    if (!reportButtonHit.visible) throw new Error("report_apply_button_not_visible");
    if (!reportButtonHit.receivesPointer) {
      const safeTarget = /^[a-z0-9_-]+$/.test(reportButtonHit.targetTestId)
        ? reportButtonHit.targetTestId
        : "other";
      throw new Error(`report_apply_intercepted_${safeTarget}`);
    }
    currentStage = "report_run_apply_click";
    await applyReportButton.click({ timeout: 5_000 });
    currentStage = "open_workspace_tab";
    await page.getByTestId("project-tab-workspace").click();
    currentStage = "open_resource_subtab_wait";
    const resourcesTab = page.getByRole("tab", { name: "Resources" });
    await resourcesTab.waitFor({ state: "visible" });
    currentStage = "open_resource_subtab_click";
    await resourcesTab.click();
    await page.waitForFunction(
      () => document.querySelector("#workspace-tab-resources")?.getAttribute("aria-selected") === "true",
    );
    await page.locator("#workspace-panel-resources").waitFor({ state: "visible" });

    currentStage = "artifact_metadata";
    const workspaceResponse = await readJsonInPage(page, `/api/projects/${projectId}/workspace`);
    assert.equal(workspaceResponse.status, 200);
    const reportArtifact = workspaceResponse.body.artifacts.find(
      (artifact) => artifact.path === reportPath && String(artifact.metadata?.worker_run_id) === reportRun.id,
    );
    assert.ok(reportArtifact, "the canonical workspace did not expose the report artifact");
    const attempt = reportArtifact.metadata.worker_run_attempt;
    assert.ok(Number.isSafeInteger(attempt) && attempt > 0, "artifact attempt binding is missing");
    assert.equal(
      reportArtifact.metadata.blob_key,
      `${projectId}/worker-runs/${reportRun.id}/attempt-${attempt}/${reportPath}`,
    );
    const downloadableArtifactChecks = {
      project: reportArtifact.metadata.project_id?.toLowerCase() === projectId.toLowerCase(),
      run: uuidPattern.test(String(reportArtifact.metadata.worker_run_id ?? "")),
      bucket: typeof reportArtifact.metadata.blob_bucket === "string" && reportArtifact.metadata.blob_bucket.length > 0,
      key: typeof reportArtifact.metadata.blob_key === "string" && reportArtifact.metadata.blob_key.length > 0,
      digest: reportArtifact.metadata.blob_sha256?.toLowerCase() === reportArtifact.sha256?.toLowerCase(),
      size: Number.isSafeInteger(reportArtifact.size_bytes) && reportArtifact.size_bytes >= 0,
      blob_size: reportArtifact.metadata.blob_size_bytes === reportArtifact.size_bytes,
    };
    const unavailableDownloadField = Object.entries(downloadableArtifactChecks).find(([, valid]) => !valid)?.[0];
    if (unavailableDownloadField) throw new Error(`artifact_download_metadata_${unavailableDownloadField}`);

    currentStage = "artifact_inspection";
    await page.getByRole("link", { name: `Inspect evidence for artifact ${reportPath}` }).click();
    await page.getByTestId("ceo-evidence-record").waitFor({ state: "visible" });
    const evidenceDetail = await readJsonInPage(page, `/api/evidence/artifact/${reportArtifact.id}`);
    assert.equal(evidenceDetail.status, 200);
    assert.equal(evidenceDetail.body.record.sha256, reportArtifact.sha256);
    assert.equal(evidenceDetail.body.record.path, reportPath);
    await page.getByText(reportArtifact.sha256, { exact: true }).waitFor({ state: "visible" });
    const citationText = await page.getByTestId("ceo-evidence-record").innerText();
    if (!citationText.includes(reportArtifact.sha256)) throw new Error("artifact_inspection_sha_missing");
    if (!citationText.includes(reportPath)) throw new Error("artifact_inspection_path_missing");
    await page.goto(projectUrl, { waitUntil: "domcontentloaded" });
    const resourcesTabAfterReturn = page.getByRole("tab", { name: "Resources" });
    await resourcesTabAfterReturn.waitFor({ state: "visible" });
    await resourcesTabAfterReturn.click();
    await page.waitForFunction(
      () => document.querySelector("#workspace-tab-resources")?.getAttribute("aria-selected") === "true",
    );

    currentStage = "artifact_download_button_ready";
    const artifactDownload = page.getByTestId(`download-project-artifact-${reportArtifact.id}`);
    await artifactDownload.waitFor({ state: "visible" });
    if (!(await artifactDownload.isEnabled())) throw new Error("artifact_download_button_disabled");
    await artifactDownload.scrollIntoViewIfNeeded();
    currentStage = "artifact_download_click";
    const downloadEvent = page.waitForEvent("download", { timeout: 10_000 }).catch(() => null);
    await artifactDownload.click({ timeout: 5_000 });
    currentStage = "artifact_download_wait_event";
    const download = await downloadEvent;
    if (!download) {
      const downloadError = page.getByTestId("project-artifact-download-error");
      const errorVisible = await downloadError.isVisible().catch(() => false);
      if (errorVisible) {
        const message = (await downloadError.innerText()).toLowerCase();
        const category = message.includes("checksum")
          ? "checksum"
          : message.includes("size")
            ? "size"
            : message.includes("metadata")
              ? "metadata"
              : "request";
        throw new Error(`artifact_download_rejected_${category}`);
      }
      throw new Error("artifact_download_event_missing");
    }
    const downloadPath = await download.path();
    assert.ok(downloadPath);
    const downloadedBytes = await readFile(downloadPath);
    const downloadedSha = createHash("sha256").update(downloadedBytes).digest("hex");
    assert.equal(downloadedBytes.byteLength, reportArtifact.size_bytes);
    assert.equal(downloadedSha, reportArtifact.sha256);

    currentStage = "artifact_tamper_rejection";
    const tamperedBytes = Buffer.from(downloadedBytes);
    tamperedBytes[0] = tamperedBytes[0] === 0x23 ? 0x24 : 0x23;
    await page.route(
      `**/api/projects/${projectId}/artifacts/${reportArtifact.id}/download`,
      (route) => route.fulfill({
        status: 200,
        headers: {
          "Content-Type": "application/octet-stream",
          "Content-Disposition": 'attachment; filename="research-brief.md"',
          "X-AIAT-Artifact-SHA256": reportArtifact.sha256,
          "X-AIAT-Artifact-Size": String(reportArtifact.size_bytes),
        },
        body: tamperedBytes,
      }),
    );
    await artifactDownload.click();
    await page.getByTestId("project-artifact-download-error").waitFor({ state: "visible" });
    const integrityError = await page.getByTestId("project-artifact-download-error").innerText();
    assert.match(integrityError, /checksum/i);
    await page.unroute(`**/api/projects/${projectId}/artifacts/${reportArtifact.id}/download`);

    currentStage = "missing_document_gate";
    await page.getByTestId("project-tab-workflow").click();
    await page.getByRole("button", { name: "all sprints done", exact: true }).click();
    await page.getByRole("button", { name: "retrospective done", exact: true }).click();
    await page.getByRole("button", { name: "kpi saved", exact: true }).click();
    await page.getByTestId("project-evidence-incomplete-detail").waitFor({ state: "visible" });
    const missingNames = await page.getByTestId("project-evidence-incomplete-detail").innerText();
    if (!/documents[\s_]exist/.test(missingNames)) throw new Error("missing_document_gate_documents_check_missing");
    if (!/document[\s_]bodies[\s_]retrievable/.test(missingNames)) {
      throw new Error("missing_document_gate_bodies_check_missing");
    }

    currentStage = "register_project_document";
    await page.getByTestId("project-tab-workspace").click();
    await page.getByRole("tab", { name: "Resources" }).click();
    await page.getByTestId(`register-research-brief-evidence-${reportArtifact.id}`).click();
    await page.getByTestId("research-brief-evidence-registration-success").waitFor({ state: "visible" });
    const documentsResponse = await readJsonInPage(page, `/api/projects/${projectId}/documents`);
    assert.equal(documentsResponse.status, 200);
    const projectDocuments = documentsResponse.body?.documents;
    if (!Array.isArray(projectDocuments)) throw new Error("project_documents_response_invalid");
    const reportDocument = projectDocuments.find(
      (document) => document.doc_type === "RESEARCH_BRIEF" && document.blob_key === reportArtifact.metadata.blob_key,
    );
    assert.ok(reportDocument, "verified artifact was not registered as the project document");
    assert.equal(reportDocument.blob_sha256, reportArtifact.sha256);
    assert.equal(reportDocument.project_id, projectId);

    currentStage = "registered_document_download";
    await page.getByTestId("project-tab-context").click();
    const reportDocumentDownload = page.getByRole("button", {
      name: "Download RESEARCH_BRIEF v1",
    });
    await reportDocumentDownload.waitFor({ state: "visible" });
    const documentDownloadEvent = page.waitForEvent("download", { timeout: 10_000 }).catch(() => null);
    await reportDocumentDownload.click({ timeout: 5_000 });
    const documentDownload = await documentDownloadEvent;
    if (!documentDownload) throw new Error("registered_document_download_missing");
    const documentDownloadPath = await documentDownload.path();
    assert.ok(documentDownloadPath);
    const documentBytes = await readFile(documentDownloadPath);
    const documentSha = createHash("sha256").update(documentBytes).digest("hex");
    assert.equal(documentSha, reportArtifact.sha256);

    currentStage = "complete_evidence_gated_project";
    await page.getByTestId("project-tab-evidence").click();
    await page.getByRole("heading", { name: "Completion Evidence" }).waitFor({ state: "visible" });
    await page.getByText("100% complete").waitFor({ state: "visible" });
    const evidenceResponse = await readJsonInPage(page, `/api/projects/${projectId}/evidence`);
    if (evidenceResponse.status !== 200) throw new Error("completed_project_evidence_unavailable");
    if (evidenceResponse.body.status !== "complete") {
      throw new Error("completed_project_evidence_not_complete");
    }
    await page.getByTestId("project-tab-workflow").click();
    await page.getByRole("button", { name: "kpi saved", exact: true }).click();
    await page.getByText("COMPLETED", { exact: true }).first().waitFor({ state: "visible" });
    const finalProject = await readJsonInPage(page, `/api/projects/${projectId}`);
    if (finalProject.status !== 200) throw new Error("completed_project_readback_unavailable");
    if (finalProject.body.state !== "COMPLETED") throw new Error("project_completion_not_persisted");
    if (blockedOrigins.length > 0) throw new Error("external_origin_blocked");

    process.stdout.write(`${JSON.stringify({
      browser: "system-chrome-or-local-playwright",
      project_id: projectId,
      flow_status: "COMPLETED",
      project_status: finalProject.body.state,
      research_run_id: researchRun.id,
      report_run_id: reportRun.id,
      artifact_id: reportArtifact.id,
      artifact_attempt: attempt,
      artifact_sha256: downloadedSha,
      artifact_bytes: downloadedBytes.byteLength,
      document_id: reportDocument.id,
      document_download_sha256: documentSha,
      evidence_status: evidenceResponse.body.status,
      assertions: [
        "real_next_bff_to_fastapi",
        "synthetic_research_and_report_runs",
        "operator_review_gate",
        "attempt_scoped_artifact_ownership",
        "artifact_inspection_and_download_hash",
        "tampered_download_rejected",
        "missing_document_blocks_project_completion",
        "verified_artifact_registered_as_project_document",
        "registered_document_download_checksum",
        "evidence_gated_project_completion",
        "external_origins_blocked",
      ],
    })}\n`);
  } catch (error) {
    const stage = currentStage;
    const browserMessage = error instanceof Error ? error.message.toLowerCase() : "";
    const browserFailure = browserMessage.includes("intercepts pointer events")
      ? "browser_pointer_intercept"
      : browserMessage.includes("not stable")
        ? "browser_element_unstable"
        : browserMessage.includes("not enabled")
          ? "browser_element_disabled"
          : browserMessage.includes("not visible")
            ? "browser_element_hidden"
            : browserMessage.includes("outside of the viewport")
              ? "browser_element_outside_viewport"
              : null;
    const errorCode = error && typeof error === "object" && "code" in error
      ? `fs_${String(error.code).toLowerCase().replace(/[^a-z0-9_]/g, "_")}`
      : browserFailure
        ? browserFailure
      : error instanceof Error && error.name === "TimeoutError"
        ? "browser_action_timeout"
      : error instanceof Error && /^[a-z0-9_]+$/.test(error.message)
        ? error.message
        : "unexpected_error";
    process.stderr.write(`Research Brief dashboard browser test failed at ${stage}: ${errorCode}\n`);
    process.exitCode = 1;
  } finally {
    await browser?.close().catch(() => {});
    if (server && server.exitCode === null) {
      server.kill("SIGTERM");
      await Promise.race([
        new Promise((resolve) => server.once("exit", resolve)),
        new Promise((resolve) => setTimeout(resolve, 5_000)),
      ]);
      if (server.exitCode === null) {
        server.kill("SIGKILL");
        await Promise.race([
          new Promise((resolve) => server.once("exit", resolve)),
          new Promise((resolve) => setTimeout(resolve, 5_000)),
        ]);
      }
    }
    if (temporaryRoot) {
      const safeTemp = path.resolve(os.tmpdir());
      const target = path.resolve(temporaryRoot);
      if (target.startsWith(`${safeTemp}${path.sep}`) && path.basename(target).startsWith("aiat-research-brief-dashboard-")) {
        await rm(target, { recursive: true, force: true, maxRetries: 8, retryDelay: 250 });
      }
    }
  }
}

await main();
