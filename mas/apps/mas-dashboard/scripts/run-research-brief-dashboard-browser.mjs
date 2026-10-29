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
let projectId = process.argv[2] ?? "";
const createAssets = process.argv[3] === "--create-assets";
const workerRunOperatorMode = process.argv[3] === "--worker-run-operator-actions";
const researchWorkerId = "50000000-0000-4000-8000-000000000504";
const reportWorkerId = "50000000-0000-4000-8000-000000000505";
const modelProfileId = "research-approved-fixture";
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const syntheticApiKey = "synthetic-research-brief-operator-key";
const syntheticJwtSecret = "synthetic-research-brief-dashboard-session-secret";
const reportPath = "reports/research-brief.md";
const timeoutMs = 20_000;
let flowNodeIds = { start: "start", research: "research", review: "review", report: "report", end: "end" };

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
    (!createAssets && !uuidPattern.test(projectId))
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

async function apiRequest(pathname, { method = "GET", body } = {}) {
  const target = new URL(pathname.replace(/^\//, ""), `${apiOrigin}/`);
  if (target.origin !== apiOrigin) throw new Error("api_destination_escape_rejected");
  const response = await fetch(target, {
    method,
    headers: {
      "x-api-key": syntheticApiKey,
      ...(body === undefined ? {} : { "content-type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(10_000),
  });
  return { status: response.status, body: await response.json().catch(() => null) };
}

async function createCompanyThroughDashboard(page, baseUrl, setStage = () => {}) {
  const name = "Hermetic Research Brief";
  const slug = "hermetic-research-brief";
  setStage("ui_company_page");
  const companyListResponse = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/companies` && response.request().method() === "GET",
  );
  await page.goto(`${baseUrl}/companies`, { waitUntil: "domcontentloaded" });
  if (!(await companyListResponse).ok()) throw new Error("company_list_load_rejected");
  setStage("ui_company_open_form");
  await page.getByRole("button", { name: /New company/ }).click();
  setStage("ui_company_form_ready");
  await page.getByRole("heading", { name: "Create company", exact: true }).waitFor({ state: "visible" });
  setStage("ui_company_fill_name");
  await page.locator('input[placeholder="AIAT Research"]').fill(name);
  setStage("ui_company_fill_slug");
  await page.locator('input[placeholder="aiat-research"]').fill(slug);
  setStage("ui_company_fill_description");
  await page.locator('textarea[placeholder="What this company is responsible for"]').fill("Synthetic UI/API journey fixture company");
  const createResponsePromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/companies` && response.request().method() === "POST",
  );
  setStage("ui_company_submit");
  const companyForm = page.locator("form").filter({ has: page.locator('input[placeholder="AIAT Research"]') });
  await companyForm.getByRole("button", { name: "Create company", exact: true }).click();
  const createResponse = await createResponsePromise;
  setStage("ui_company_created_readback");
  if (createResponse.status() !== 201) throw new Error("company_create_rejected");
  const created = await createResponse.json();
  const companyId = String(created?.id ?? "");
  if (!uuidPattern.test(companyId)) throw new Error("company_identity_not_returned");
  const manifest = {
    schema_version: "1",
    slug,
    name,
    description: "Synthetic UI/API journey fixture company",
    ceo_worker_id: "ceo",
    timezone: "UTC",
    model_policy: {
      allowed_profile_ids: [modelProfileId],
      default_profile_id: modelProfileId,
      require_exact_profile: true,
      allow_gateway_failover: false,
    },
    evidence_policy: {
      require_checksums: true,
      require_approval_records: true,
      default_policy: {
        policy_id: "research_delivery",
        version: "1.0",
        requirements: {
          requires_flow_terminal: true,
          requires_artifacts: true,
          requires_documents: true,
          required_document_types: ["RESEARCH_BRIEF"],
          requires_document_bodies: true,
        },
      },
    },
    departments: [
      { id: "exec_ceo", name: "CEO Office", chief_worker_id: "ceo", worker_ids: ["ceo"] },
      { id: "research", name: "Research", chief_worker_id: null, worker_ids: [researchWorkerId, reportWorkerId] },
    ],
    worker_assignments: [
      {
        worker_id: "ceo", department_id: "exec_ceo", status: "ACTIVE", tool_grants: [],
        permission_grants: ["company.manage", "approval.decide"], budget: {}, approval_required: false, metadata: {},
      },
      {
        worker_id: researchWorkerId, department_id: "research", status: "ACTIVE", tool_grants: [],
        permission_grants: [], model_profile_id: modelProfileId, budget: {}, approval_required: false, metadata: {},
      },
      {
        worker_id: reportWorkerId, department_id: "research", status: "ACTIVE", tool_grants: ["file_write"],
        permission_grants: ["project.write"], model_profile_id: modelProfileId, budget: {}, approval_required: false, metadata: {},
      },
    ],
    budgets: {},
    policy: {},
  };
  return { companyId, slug, name, manifest };
}

async function applyCompanyAssignmentsThroughDashboard(page, baseUrl, companyId, manifest, setStage = () => {}) {
  setStage("ui_manifest_page");
  const companyListResponse = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/companies` && response.request().method() === "GET",
  );
  await page.goto(`${baseUrl}/companies`, { waitUntil: "domcontentloaded" });
  if (!(await companyListResponse).ok()) throw new Error("company_list_load_rejected");
  setStage("ui_manifest_company_ready");
  await page.getByText("Hermetic Research Brief", { exact: true }).first().waitFor({ state: "visible" });
  const advancedManifest = page.locator("#company-manifest-json");
  const details = page.locator("details").filter({ has: advancedManifest });
  setStage("ui_manifest_open_editor");
  if (!(await advancedManifest.isVisible())) await details.locator("summary").click();
  await advancedManifest.waitFor({ state: "visible" });
  setStage("ui_manifest_fill_editor");
  await advancedManifest.fill(JSON.stringify(manifest, null, 2));
  setStage("ui_manifest_validate");
  const validatePromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/companies/${companyId}/manifest/validate` && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Validate manifest" }).click();
  const validation = await validatePromise;
  setStage("ui_manifest_validation_response");
  if (!validation.ok()) throw new Error("company_manifest_validation_rejected");
  setStage("ui_manifest_apply");
  const applyPromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/companies/${companyId}/manifest/apply` && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Apply validated manifest" }).click();
  const applied = await applyPromise;
  setStage("ui_manifest_apply_response");
  if (!applied.ok()) throw new Error("company_manifest_apply_rejected");
  setStage("ui_manifest_readback");
  const readback = await readJsonInPage(page, `/api/companies/${companyId}`);
  const assignments = readback.body?.assignments ?? [];
  if (readback.status !== 200 || !assignments.some((row) => row.worker_id === researchWorkerId && row.status === "ACTIVE") || !assignments.some((row) => row.worker_id === reportWorkerId && row.status === "ACTIVE")) {
    throw new Error("active_company_worker_assignments_not_persisted");
  }
}

async function selectCompanyEvidencePolicyForProjectThroughDashboard(
  page,
  companyId,
  projectId,
  setStage = () => {},
) {
  setStage("ui_company_manifest_policy_readback");
  const history = await readJsonInPage(page, `/api/companies/${companyId}/manifest/history`);
  const activeManifest = Array.isArray(history.body)
    ? history.body.find((row) => row.status === "ACTIVE") ?? history.body[0]
    : null;
  const selection = activeManifest?.manifest_json?.evidence_policy?.default_policy;
  if (
    history.status !== 200 ||
    !selection ||
    typeof selection.policy_id !== "string" ||
    typeof selection.version !== "string" ||
    !selection.requirements ||
    typeof selection.requirements !== "object" ||
    Array.isArray(selection.requirements)
  ) {
    throw new Error("active_company_evidence_policy_unavailable");
  }

  // The Research template selects the built-in `research` policy, which has
  // higher precedence than a company default. Explicitly select the persisted
  // company policy at project scope for this delivery contract.
  setStage("ui_project_select_company_evidence_policy");
  const result = await page.evaluate(async ({ projectId, policy }) => {
    const response = await fetch(`/api/projects/${projectId}/evidence-policy`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        policy_id: policy.policy_id,
        policy_version: policy.version,
        requirements: policy.requirements,
        scope: "project",
      }),
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, { projectId, policy: selection });
  if (result.status !== 200) throw new Error("project_evidence_policy_selection_rejected");

  setStage("ui_project_evidence_policy_readback");
  const projectRead = await readJsonInPage(page, `/api/projects/${projectId}`);
  const persisted = projectRead.body?.config?.evidence_policy;
  if (
    projectRead.status !== 200 ||
    persisted?.policy_id !== selection.policy_id ||
    persisted?.version !== selection.version ||
    JSON.stringify(persisted?.requirements) !== JSON.stringify(selection.requirements)
  ) {
    throw new Error("project_evidence_policy_selection_not_persisted");
  }
}

async function createBoundResearchFlowThroughDashboard(page, baseUrl, setStage = () => {}) {
  const visibleTestId = (testId) => page.locator(`[data-testid="${testId}"]:visible`);
  setStage("ui_flow_page");
  const workersResponsePromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/workers` && response.request().method() === "GET",
  );
  const profilesResponsePromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/governance/model-profiles` && response.request().method() === "GET",
  );
  await page.goto(`${baseUrl}/flows/new`, { waitUntil: "domcontentloaded" });
  const [workersResponse, profilesResponse] = await Promise.all([
    workersResponsePromise,
    profilesResponsePromise,
  ]);
  setStage("ui_flow_catalogues_loaded");
  if (!workersResponse.ok() || !profilesResponse.ok()) throw new Error("governed_flow_catalogue_load_rejected");
  setStage("ui_flow_template_ready");
  await page.getByTestId("template-research").waitFor({ state: "visible" });
  setStage("ui_flow_apply_template");
  await page.getByTestId("template-research").click();

  async function bindTask(label, workerId, taskType, input, { toolGrants = "", permissions = "" } = {}) {
    const taskStage = taskType.replace(/[^a-z0-9]+/g, "_");
    setStage(`ui_flow_find_${taskStage}_node`);
    const taskNode = page.locator(".react-flow__node").filter({ hasText: label }).first();
    await taskNode.waitFor({ state: "visible" });
    setStage(`ui_flow_select_${taskStage}_node`);
    await taskNode.click();
    setStage(`ui_flow_bind_${taskStage}_worker`);
    const workerSelect = visibleTestId("task-worker-select");
    const workerSelectCount = await workerSelect.count();
    if (workerSelectCount !== 1) throw new Error(`worker_select_count_${workerSelectCount}`);
    const workerOptionPresent = await workerSelect.evaluate(
      (element, id) => Array.from(element.options).some((option) => option.value === id),
      workerId,
    );
    if (!workerOptionPresent) throw new Error("configured_worker_not_in_catalogue");
    await workerSelect.selectOption(workerId);
    setStage(`ui_flow_bind_${taskStage}_profile`);
    await visibleTestId("task-model-profile-select").selectOption(modelProfileId);
    setStage(`ui_flow_bind_${taskStage}_type`);
    await visibleTestId("task-type-input").fill(taskType);
    setStage(`ui_flow_bind_${taskStage}_input`);
    if (input === undefined) {
      await visibleTestId("task-input-clear").click();
    } else {
      await visibleTestId("task-input-json").fill(JSON.stringify(input, null, 2));
    }
    setStage(`ui_flow_bind_${taskStage}_tools`);
    await visibleTestId("task-tool-grants-input").fill(toolGrants);
    setStage(`ui_flow_bind_${taskStage}_permissions`);
    await visibleTestId("task-permissions-input").fill(permissions);
  }

  await bindTask("Research", researchWorkerId, "research.collect", {
    query: "AIAT Research Brief project creation and governed execution",
  });
  await bindTask("Write report", reportWorkerId, "research.report", undefined, {
    toolGrants: "file_write",
    permissions: "project.write",
  });
  setStage("ui_flow_name");
  await page.getByLabel("Flow name").fill("Hermetic Research Brief Journey");
  const dryRunPromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/flows/dry-run` && response.request().method() === "POST",
  );
  await page.getByTestId("flow-dry-run-button").click();
  const dryRunResponse = await dryRunPromise;
  setStage("ui_flow_dry_run_checked");
  if (!dryRunResponse.ok() || (await dryRunResponse.json()).valid !== true) {
    throw new Error("research_flow_readiness_rejected");
  }
  await page.getByLabel("Activate flow on save").check();
  const savePromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/flows` && response.request().method() === "POST",
  );
  await page.getByTestId("flow-save-button").click();
  const saveResponse = await savePromise;
  setStage("ui_flow_save_response");
  if (saveResponse.status() !== 201) throw new Error("research_flow_create_rejected");
  const savedFlow = await saveResponse.json();
  const flowId = String(savedFlow?.id ?? "");
  if (!uuidPattern.test(flowId) || savedFlow.is_active !== true) throw new Error("active_research_flow_not_persisted");
  const flowRead = await readJsonInPage(page, `/api/flows/${flowId}`);
  setStage("ui_flow_readback");
  if (flowRead.status !== 200) throw new Error("saved_flow_readback_unavailable");
  const nodes = flowRead.body?.definition_json?.nodes ?? [];
  const research = nodes.find((node) => node.type === "task" && node.config?.task_type === "research.collect");
  const review = nodes.find((node) => node.type === "approval");
  const report = nodes.find((node) => node.type === "task" && node.config?.task_type === "research.report");
  const start = nodes.find((node) => node.type === "start");
  const end = nodes.find((node) => node.type === "end");
  if (![research, review, report, start, end].every(Boolean)) throw new Error("saved_research_flow_topology_invalid");
  if (research.config.worker_id !== researchWorkerId || report.config.worker_id !== reportWorkerId) {
    throw new Error("saved_research_flow_worker_bindings_invalid");
  }
  if (Object.hasOwn(report.config, "task_input")) {
    throw new Error("saved_report_task_must_use_flow_context_fallback");
  }
  if (research.config.model_profile_id !== modelProfileId || report.config.model_profile_id !== modelProfileId) {
    throw new Error("saved_research_flow_model_bindings_invalid");
  }
  return {
    flowId,
    nodeIds: { start: start.id, research: research.id, review: review.id, report: report.id, end: end.id },
  };
}

async function createProjectThroughDashboard(page, baseUrl, companyId, flowId, setStage = () => {}) {
  setStage("ui_project_page");
  const projectsResponsePromise = page.waitForResponse(
    (response) => response.url().startsWith(`${baseUrl}/api/projects?`) && response.request().method() === "GET",
  );
  const flowsResponsePromise = page.waitForResponse(
    (response) => response.url().startsWith(`${baseUrl}/api/flows?`) && response.request().method() === "GET",
  );
  const companiesResponsePromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/companies` && response.request().method() === "GET",
  );
  await page.goto(`${baseUrl}/projects`, { waitUntil: "domcontentloaded" });
  const [projectsResponse, flowsResponse, companiesResponse] = await Promise.all([
    projectsResponsePromise,
    flowsResponsePromise,
    companiesResponsePromise,
  ]);
  if (!projectsResponse.ok() || !flowsResponse.ok() || !companiesResponse.ok()) {
    throw new Error("project_create_catalogue_load_rejected");
  }
  setStage("ui_project_open_form");
  await page.getByRole("button", { name: "New Project", exact: true }).first().click();
  const projectForm = page.locator("form").filter({ has: page.getByPlaceholder("my-project") });
  setStage("ui_project_fill_name");
  await page.getByPlaceholder("my-project").fill("Hermetic Research Brief Project");
  setStage("ui_project_fill_goal");
  await projectForm.locator("textarea").nth(0).fill("Research a bounded topic and produce an evidence-backed brief.");
  setStage("ui_project_fill_context");
  await projectForm.locator("textarea").nth(1).fill("Use only the synthetic findings supplied by the test adapter.");
  const startImmediately = projectForm.getByLabel("Start CEO feasibility after creation");
  setStage("ui_project_prepare_without_start");
  if (await startImmediately.isChecked()) await startImmediately.uncheck();
  setStage("ui_project_workspace_mode");
  await projectForm.locator("select").nth(0).selectOption("init");
  setStage("ui_project_attach_flow");
  await projectForm.locator("select").nth(1).selectOption(flowId);
  setStage("ui_project_select_company");
  const companySelect = projectForm.locator("#new-project-company");
  await companySelect.locator(`option[value="${companyId}"]`).waitFor({ state: "attached" });
  await companySelect.selectOption(companyId);
  const createPromise = page.waitForResponse(
    (response) => response.url() === `${baseUrl}/api/projects` && response.request().method() === "POST",
  );
  setStage("ui_project_submit");
  await projectForm.getByRole("button", { name: "Create", exact: true }).click();
  const response = await createPromise;
  setStage("ui_project_created_readback");
  if (response.status() !== 201) throw new Error("project_create_rejected");
  const project = await response.json();
  const id = String(project?.id ?? "");
  if (!uuidPattern.test(id) || String(project.company_id) !== companyId) throw new Error("company_scoped_project_not_persisted");

  return id;
}

async function advanceProjectToExecution(page) {
  const start = await page.evaluate(async (path) => {
    const response = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
    return { status: response.status, body: await response.json().catch(() => null) };
  }, `/api/projects/${projectId}/start`);
  if (start.status !== 200) throw new Error("project_start_rejected");

  async function transition(event) {
    const result = await page.evaluate(async ({ path, eventName }) => {
      const response = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ event: eventName }),
      });
      return { status: response.status, body: await response.json().catch(() => null) };
    }, { path: `/api/projects/${projectId}/transition`, eventName: event });
    if (result.status !== 200) throw new Error(`project_transition_${event}_rejected`);
  }

  async function approveProjectGate() {
    const pending = await readJsonInPage(page, `/api/projects/${projectId}/decisions`);
    if (pending.status !== 200 || !Array.isArray(pending.body)) throw new Error("project_review_gate_unavailable");
    const gate = pending.body.find((row) => row.status === "PENDING" && row.flow_node_execution_id == null);
    if (!gate) throw new Error("project_review_gate_not_pending");
    const decision = await page.evaluate(async ({ path, gateId }) => {
      const response = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ gate_id: gateId, decision: "APPROVED", comments: "Synthetic operator decision for isolated test" }),
      });
      return { status: response.status, body: await response.json().catch(() => null) };
    }, { path: `/api/projects/${projectId}/decisions`, gateId: gate.id });
    if (decision.status !== 200) throw new Error("project_review_decision_rejected");
  }

  await transition("all_reviews_in");
  await approveProjectGate();
  await transition("pdr_submitted");
  await transition("all_reviews_in");
  await transition("cdr_submitted");
  await transition("cdr_presented");
  await approveProjectGate();
  await transition("rr_submitted");
  await transition("sprints_created");
  await transition("infra_ready");
  const projectRead = await readJsonInPage(page, `/api/projects/${projectId}`);
  if (projectRead.status !== 200 || projectRead.body.state !== "IN_PROGRESS") {
    throw new Error("project_not_in_dispatchable_state");
  }
}

async function verifyAssignmentGateBeforeRun(companyId) {
  const denied = await apiRequest("/workers/runs", {
    method: "POST",
    body: {
      worker_id: researchWorkerId,
      idempotency_key: "research-brief-assignment-gate-probe",
      task_type: "research.collect",
      task_input: { query: "company assignment gate probe" },
      project_id: projectId,
      dispatch_mode: "inline",
    },
  });
  const detail = denied.body?.detail;
  if (denied.status !== 403 || detail?.code !== "COMPANY_ASSIGNMENT_REQUIRED") {
    throw new Error("company_assignment_gate_not_enforced");
  }
  return { company_id: companyId, status: denied.status, blocker: detail.code };
}

async function runWorkerRunOperatorActions(page, baseUrl, runIds, setStage = () => {}) {
  if (runIds.length !== 5 || runIds.some((id) => !uuidPattern.test(id))) {
    throw new Error("worker_run_operator_fixture_ids_invalid");
  }
  const [cancelRunId, retryRunId, staleRunId, revokedRunId, raceRunId] = runIds;
  page.on("dialog", (dialog) => dialog.accept());

  async function openRun(runId, state) {
    await page.goto(`${baseUrl}/workers/runs/${encodeURIComponent(runId)}`, { waitUntil: "domcontentloaded" });
    await page.getByTestId("run-identity").waitFor({ state: "visible" });
    await page.getByText(`State · ${state}`, { exact: true }).waitFor({ state: "visible" });
  }

  async function waitUntilIdle() {
    await page.waitForFunction(() => {
      const main = document.querySelector('main[aria-label="Worker Run detail"]');
      return main?.getAttribute("aria-busy") === "false";
    }, null, { timeout: timeoutMs });
  }

  setStage("operator_cancel_open");
  await openRun(cancelRunId, "QUEUED");
  setStage("operator_cancel_submit");
  await page.getByRole("button", { name: "Cancel Worker Run", exact: true }).click();
  await page.getByText("Cancellation confirmed. Worker Run state is CANCELLED.", { exact: true }).waitFor({ state: "visible" });
  await page.getByText("State · CANCELLED", { exact: true }).waitFor({ state: "visible" });
  setStage("operator_cancel_transition_evidence");
  await page.getByTestId("run-transitions").getByText("QUEUED → CANCELLED · operator", { exact: true }).waitFor({ state: "visible" });

  setStage("operator_retry_open");
  await openRun(retryRunId, "FAILED");
  setStage("operator_retry_submit");
  await page.getByRole("button", { name: "Retry Worker Run", exact: true }).click();
  await page.getByText(/^Retry created as QUEUED\./).waitFor({ state: "visible" });
  const retryLink = page.locator('[aria-label="Retry lineage"] a').last();
  const retryHref = await retryLink.getAttribute("href");
  const createdRetryId = retryHref?.split("/").at(-1) ?? "";
  if (!uuidPattern.test(createdRetryId)) throw new Error("worker_run_retry_link_missing");
  setStage("operator_retry_link_click");
  await retryLink.click();
  setStage("operator_retry_detail_load");
  await page.getByTestId("run-identity").waitFor({ state: "visible" });
  setStage("operator_retry_status_readback");
  await page.getByText("State · QUEUED", { exact: true }).waitFor({ state: "visible" });

  setStage("operator_stale_open");
  await openRun(staleRunId, "QUEUED");
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (await page.getByText("Operator actions are disabled until this Worker Run refreshes successfully.", { exact: true }).isVisible().catch(() => false)) break;
    const refresh = page.getByRole("button", { name: "Refresh", exact: true });
    if (await refresh.count() === 0) break;
    setStage(`operator_stale_refresh_${attempt + 1}`);
    await refresh.click();
    await waitUntilIdle();
  }
  await page.getByText("Operator actions are disabled until this Worker Run refreshes successfully.", { exact: true }).waitFor({ state: "visible" });
  const staleCancelButton = page.getByRole("button", { name: "Cancel Worker Run", exact: true });
  if (!(await staleCancelButton.isDisabled())) throw new Error("stale_worker_run_action_remained_enabled");

  setStage("operator_revoked_open");
  await openRun(revokedRunId, "QUEUED");
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (await page.getByRole("region", { name: "Worker Run access status" }).isVisible().catch(() => false)) break;
    const refresh = page.getByRole("button", { name: "Refresh", exact: true });
    if (await refresh.count() === 0) break;
    setStage(`operator_revoked_refresh_${attempt + 1}`);
    await refresh.click();
    await waitUntilIdle();
  }
  await page.getByRole("region", { name: "Worker Run access status" }).waitFor({ state: "visible" });
  if (await page.getByRole("button", { name: "Cancel Worker Run", exact: true }).count() !== 0) {
    throw new Error("revoked_worker_run_controls_remained_visible");
  }

  setStage("operator_out_of_order_open");
  await openRun(raceRunId, "QUEUED");
  const cancelResponsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && response.url().endsWith(`/api/governance/runs/${raceRunId}/cancel`)
  ));
  setStage("operator_out_of_order_cancel_submit");
  await page.getByRole("button", { name: "Cancel Worker Run", exact: true }).click();
  const cancelResponse = await cancelResponsePromise;
  if (!cancelResponse.ok()) throw new Error("synthetic_race_cancel_failed");
  // The cancel response starts a quiet readback. Keep its older successful
  // response in flight while the manual refresh receives the newer 403.
  await page.waitForTimeout(100);
  setStage("operator_out_of_order_refresh");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  const accessStatus = page.getByRole("region", { name: "Worker Run access status" });
  await accessStatus.waitFor({ state: "visible" });
  await page.waitForTimeout(2000);
  if (!(await accessStatus.isVisible())) throw new Error("older_worker_run_success_overwrote_newer_denial");
  if (await page.getByTestId("run-identity").count() !== 0) {
    throw new Error("run_data_reappeared_after_access_revocation");
  }
  if (await page.getByRole("button", { name: "Cancel Worker Run", exact: true }).count() !== 0) {
    throw new Error("operator_control_reappeared_after_access_revocation");
  }

  return {
    cancellation: { run_id: cancelRunId, state: "CANCELLED" },
    retry: { source_run_id: retryRunId, source_state: "FAILED", retry_run_id: createdRetryId, retry_state: "QUEUED" },
    stale_refresh: { run_id: staleRunId, state: "QUEUED", actions_disabled: true },
    revoked_access: { run_id: revokedRunId, actions_hidden: true },
    out_of_order_denial: { run_id: raceRunId, access_denied_persisted: true, older_success_ignored: true },
    assertions: [
      "real_worker_run_detail_page",
      "real_next_bff_to_fastapi_cancel_and_retry",
      "durable_cancelled_and_retry_lineage",
      "persisted_worker_run_transition_history",
      "stale_status_keeps_last_known_state_and_disables_actions",
      "revoked_read_access_hides_worker_run_controls",
      "newer_access_denial_wins_over_older_successful_read",
    ],
  };
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

    if (workerRunOperatorMode) {
      currentStage = "worker_run_operator_actions";
      const runActionResult = await runWorkerRunOperatorActions(
        page,
        baseUrl,
        process.argv.slice(4, 9),
        (stage) => { currentStage = stage; },
      );
      if (blockedOrigins.length > 0) throw new Error("external_origin_blocked");
      process.stdout.write(`${JSON.stringify(runActionResult)}\n`);
      return;
    }

    let companyId = null;
    let flowId = null;
    let assignmentGate = null;
    if (createAssets) {
      currentStage = "ui_create_company";
      const company = await createCompanyThroughDashboard(page, baseUrl, (stage) => { currentStage = stage; });
      companyId = company.companyId;
      currentStage = "ui_create_and_bind_research_flow";
      const flow = await createBoundResearchFlowThroughDashboard(page, baseUrl, (stage) => { currentStage = stage; });
      flowId = flow.flowId;
      flowNodeIds = flow.nodeIds;
      currentStage = "ui_create_company_scoped_project";
      projectId = await createProjectThroughDashboard(page, baseUrl, companyId, flowId, (stage) => { currentStage = stage; });
      currentStage = "project_lifecycle_start";
      await page.goto(`${baseUrl}/projects/${projectId}`, { waitUntil: "domcontentloaded" });
      await advanceProjectToExecution(page);
      currentStage = "company_assignment_negative_gate";
      assignmentGate = await verifyAssignmentGateBeforeRun(companyId);
      currentStage = "ui_apply_company_worker_assignments";
      await applyCompanyAssignmentsThroughDashboard(
        page,
        baseUrl,
        companyId,
        company.manifest,
        (stage) => { currentStage = stage; },
      );
      await selectCompanyEvidencePolicyForProjectThroughDashboard(
        page,
        companyId,
        projectId,
        (stage) => { currentStage = stage; },
      );
    }

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
    const startNodeActionPromise = page.waitForResponse(
      (response) => response.url().includes("/node-action") && response.request().method() === "POST",
    );
    await page.getByTestId("complete-node-button").click();
    const startNodeActionResponse = await startNodeActionPromise;
    if (!startNodeActionResponse.ok()) {
      const actionBody = await startNodeActionResponse.json().catch(() => null);
      const actionCode = [
        actionBody?.detail?.code,
        actionBody?.detail?.error_code,
        actionBody?.code,
        actionBody?.error_code,
      ].find((value) => typeof value === "string" && /^[A-Z0-9_]+$/.test(value));
      throw new Error(`research_start_node_action_http_${startNodeActionResponse.status()}_${actionCode?.toLowerCase() ?? "unknown"}`);
    }

    currentStage = "research_run_activation";
    const researchActivation = await page.waitForFunction((nodeId) => {
      const visible = (element) => Boolean(element && element.getClientRects().length);
      const run = Array.from(document.querySelectorAll('[data-testid^="active-worker-run-"]'))
        .find((element) => element.getAttribute("data-testid") === `active-worker-run-${nodeId}`);
      if (visible(run)) return "run";
      const dispatch = document.querySelector('[data-testid="dispatch-worker-run-button"]');
      if (visible(dispatch)) return "dispatch";
      return null;
    }, flowNodeIds.research, { timeout: timeoutMs });
    if (researchActivation === "dispatch") {
      const researchDispatchResponsePromise = page.waitForResponse(
        (response) => response.url().includes("/node-action") && response.request().method() === "POST",
      );
      await page.getByTestId("dispatch-worker-run-button").click();
      const researchDispatchResponse = await researchDispatchResponsePromise;
      if (!researchDispatchResponse.ok()) throw new Error("research_worker_run_dispatch_rejected");
    }

    currentStage = "research_run";
    await page.getByTestId(`active-worker-run-${flowNodeIds.research}`).waitFor({ state: "visible" });
    const researchRun = await waitForRun(page, "research.collect");
    await refreshFlowRun(page, flowNodeIds.research);
    await page.getByTestId(`apply-worker-run-${flowNodeIds.research}`).click();

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
        (execution) => execution.node_id === flowNodeIds.review && execution.node_type === "approval" && execution.status === "RUNNING",
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
      await page.getByTestId(`active-worker-run-${flowNodeIds.report}`).waitFor({ state: "visible" });
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
        && instanceResponse.body.active_node_ids.includes(flowNodeIds.report);
      const reportExecution = Array.isArray(executionsResponse.body)
        && executionsResponse.body.some((row) => row.node_id === flowNodeIds.report && row.status === "RUNNING");
      const reportRun = Array.isArray(runResponse.body)
        && runResponse.body.some((row) => row.task_type === "research.report");
      throw new Error(
        `report_activation_${activeReport ? "active" : "inactive"}_${reportExecution ? "execution" : "no_execution"}_${reportRun ? "run" : "no_run"}`,
      );
    }
    currentStage = "report_run_wait";
    const reportRun = await waitForRun(page, "research.report");
    currentStage = "report_run_refresh";
    await refreshFlowRun(page, flowNodeIds.report);
    currentStage = "report_run_apply_enabled_check";
    const applyReportButton = page.getByTestId(`apply-worker-run-${flowNodeIds.report}`);
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
    if (!/required[\s_]document[\s_]types/.test(missingNames)) {
      throw new Error("missing_report_document_gate_not_enforced");
    }
    const incompleteEvidence = await readJsonInPage(page, `/api/projects/${projectId}/evidence`);
    if (incompleteEvidence.status !== 200 || incompleteEvidence.body.status !== "incomplete") {
      throw new Error("missing_report_document_evidence_state_invalid");
    }
    const incompleteChecks = Object.fromEntries(
      incompleteEvidence.body.checks.map((check) => [check.name, check.passed]),
    );
    if (
      incompleteChecks.documents_exist !== true ||
      incompleteChecks.document_bodies_retrievable !== true
    ) {
      throw new Error("legacy_document_evidence_was_not_retrievable");
    }
    if (incompleteChecks.required_document_types !== false) {
      throw new Error("missing_report_document_gate_check_not_failed");
    }

    await page.getByTestId("project-tab-context").click();
    currentStage = "legacy_text_document_download";
    const legacyTextDownload = page.getByRole("button", { name: "Download LEGACY_TEXT v1" });
    await legacyTextDownload.waitFor({ state: "visible" });
    const legacyTextEvent = page.waitForEvent("download", { timeout: 10_000 }).catch(() => null);
    await legacyTextDownload.click();
    const legacyTextFile = await legacyTextEvent;
    if (!legacyTextFile) throw new Error("legacy_text_document_download_missing");
    if (legacyTextFile.suggestedFilename() !== "legacy_text-v1.txt") {
      throw new Error("legacy_text_document_filename_invalid");
    }
    const legacyTextPath = await legacyTextFile.path();
    assert.ok(legacyTextPath);
    assert.equal(
      await readFile(legacyTextPath, "utf8"),
      "Legacy inline project note without a stored checksum.",
    );

    currentStage = "legacy_blob_document_download";
    const legacyBlobDownload = page.getByRole("button", { name: "Download LEGACY_BLOB v1" });
    await legacyBlobDownload.waitFor({ state: "visible" });
    const legacyBlobEvent = page.waitForEvent("download", { timeout: 10_000 }).catch(() => null);
    await legacyBlobDownload.click();
    const legacyBlobFile = await legacyBlobEvent;
    if (!legacyBlobFile) throw new Error("legacy_blob_document_download_missing");
    if (legacyBlobFile.suggestedFilename() !== "legacy_blob-v1.md") {
      throw new Error("legacy_blob_document_filename_invalid");
    }
    const legacyBlobPath = await legacyBlobFile.path();
    assert.ok(legacyBlobPath);
    assert.deepEqual(
      await readFile(legacyBlobPath),
      Buffer.from("# Legacy blob document without a stored checksum\n"),
    );

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

    currentStage = "document_digest_mismatch_rejected";
    const documentDownloadUrl = `**/api/projects/${projectId}/documents/${reportDocument.id}/download`;
    const mismatchedDigest = `${reportArtifact.sha256[0] === "0" ? "1" : "0"}${reportArtifact.sha256.slice(1)}`;
    await page.route(documentDownloadUrl, (route) => route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/markdown; charset=utf-8",
        "X-AIAT-Document-SHA256": mismatchedDigest,
      },
      body: documentBytes,
    }));
    const mismatchedHeaderDownload = page.waitForEvent("download", { timeout: 500 }).catch(() => null);
    await reportDocumentDownload.click();
    await page.getByText("Document metadata did not match the downloaded content.", { exact: true }).waitFor();
    if (await mismatchedHeaderDownload) throw new Error("mismatched_document_header_was_downloaded");
    await page.unroute(documentDownloadUrl);

    await page.route(documentDownloadUrl, (route) => route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "text/markdown; charset=utf-8",
        "X-AIAT-Document-SHA256": reportArtifact.sha256,
      },
      body: Buffer.from("tampered registered document body"),
    }));
    const mismatchedBodyDownload = page.waitForEvent("download", { timeout: 500 }).catch(() => null);
    await reportDocumentDownload.click();
    await page.getByText("Downloaded document checksum did not match its declared digest.", { exact: true }).waitFor();
    if (await mismatchedBodyDownload) throw new Error("mismatched_document_body_was_downloaded");
    await page.unroute(documentDownloadUrl);

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
    const completionTransition = page.waitForResponse(
      (response) =>
        response.url() === `${baseUrl}/api/projects/${projectId}/transition` &&
        response.request().method() === "POST",
      { timeout: 10_000 },
    );
    await page.getByRole("button", { name: "kpi saved", exact: true }).click();
    const completionResponse = await completionTransition;
    if (!completionResponse.ok()) throw new Error("project_completion_transition_rejected");
    await page.getByText("COMPLETED", { exact: true }).first().waitFor({ state: "visible" });
    const finalProject = await readJsonInPage(page, `/api/projects/${projectId}`);
    if (finalProject.status !== 200) throw new Error("completed_project_readback_unavailable");
    if (finalProject.body.state !== "COMPLETED") throw new Error("project_completion_not_persisted");
    if (blockedOrigins.length > 0) throw new Error("external_origin_blocked");

    process.stdout.write(`${JSON.stringify({
      browser: "system-chrome-or-local-playwright",
      project_id: projectId,
      company_id: companyId,
      flow_id: flowId,
      assignment_gate: assignmentGate,
      research_worker_id: researchWorkerId,
      report_worker_id: reportWorkerId,
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
        "legacy_inline_document_download_without_digest",
        "legacy_blob_document_download_without_digest",
        "mismatched_document_digests_rejected",
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
