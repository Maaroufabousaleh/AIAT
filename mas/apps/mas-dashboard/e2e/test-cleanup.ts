type ApiRecord = {
  id?: string;
  name?: string;
  worker_id?: string;
};

import { setTimeout as delay } from "node:timers/promises";
import { runtimeEnv } from "./runtime-env";

const ORCHESTRATOR_URL = runtimeEnv(
  "E2E_ORCHESTRATOR_URL",
  "http://127.0.0.1:8000",
);
const API_KEY = runtimeEnv("AIAT_OPERATOR_API_KEY") || runtimeEnv("MAS_API_KEY");

const PROJECT_PATTERNS = [
  /^aiat_smoke_[a-z0-9_]+$/i,
  /^gamma-workspace-\d+$/,
  /^flow-ui-project-\d+$/,
  /^test2-[a-z0-9-]+-\d+$/i,
  /^Test Project \d+$/,
  /^proj-\d+$/,
  /^live_probe_\d+_[a-f0-9]+$/,
  /^rebuilt_probe_\d+_[a-f0-9]+$/,
  /^Live infra-ready audit \d+$/,
  /^AIAT live tool audit [a-f0-9]+$/,
  /^[CDF]-\d{3}(?:[ -/]|$)/,
  /^G(?:-chief-scenarios-|\d{3} live trace$)/,
  /^live-(?:i003-|context-|chunk-ledger$)/,
  /^tmp$/,
  /^ceo_live_project_\d+$/,
];

const WORKER_PATTERNS = [
  /^aiat_smoke_[a-z0-9_]+$/i,
  /^e2e_worker_\d+$/,
  /^e2e_candidate_\d+$/,
  /^test-worker-\d+$/,
  /^live_probe_\d+_[a-f0-9]+_worker$/,
  /^live-audit-worker-[a-f0-9]+$/,
  /^rebuilt_probe_\d+_[a-f0-9]+_worker$/,
];

const FLOW_PATTERNS = [
  /^Simple Product Build Flow \d+$/,
  /^Test2-[A-Za-z0-9-]+-\d+$/,
  /^Test Flow \d+$/,
];

const CREDENTIAL_PATTERNS = [/^E2E_SECRET_\d+$/, /^TEST_SECRET_\d+$/];

function matchesAny(value: unknown, patterns: RegExp[]): boolean {
  return typeof value === "string" && patterns.some((pattern) => pattern.test(value));
}

async function api<T>(path: string, init?: RequestInit): Promise<T | null> {
  const response = await fetch(`${ORCHESTRATOR_URL}${path}`, {
    ...init,
    headers: {
      "X-API-Key": API_KEY,
      ...(init?.headers ?? {}),
    },
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed with HTTP ${response.status}`);
  }
  if (response.status === 204) return null;
  return (await response.json()) as T;
}

async function deleteMatching(
  label: string,
  records: ApiRecord[],
  patterns: RegExp[],
  deletePath: (record: ApiRecord) => string | null,
): Promise<number> {
  let deleted = 0;
  for (const record of records) {
    const candidate = record.worker_id ?? record.name;
    if (!matchesAny(candidate, patterns)) continue;
    const path = deletePath(record);
    if (!path) continue;
    await api(path, { method: "DELETE" });
    deleted += 1;
  }
  if (deleted > 0) {
    console.log(`[e2e cleanup] removed ${deleted} ${label}`);
  }
  return deleted;
}

export async function cleanupE2EArtifacts(): Promise<void> {
  const errors: string[] = [];

  async function run(label: string, fn: () => Promise<void>) {
    try {
      await fn();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      errors.push(`${label}: ${message}`);
    }
  }

  const cleanupProjects = async (): Promise<number> => {
    const projects = (await api<ApiRecord[]>("/projects?limit=1000")) ?? [];
    return deleteMatching("test projects", projects, PROJECT_PATTERNS, (project) =>
      project.id ? `/projects/${project.id}` : null,
    );
  };

  await run("projects", async () => {
    await cleanupProjects();
  });

  await run("workers", async () => {
    const workers = (await api<ApiRecord[]>("/capabilities/workers")) ?? [];
    await deleteMatching("test workers", workers, WORKER_PATTERNS, (worker) =>
      worker.id ? `/capabilities/workers/${worker.id}?permanent=true` : null,
    );
  });

  await run("flows", async () => {
    const flows = (await api<ApiRecord[]>("/flows?limit=1000")) ?? [];
    await deleteMatching("test flows", flows, FLOW_PATTERNS, (flow) =>
      flow.id ? `/flows/${flow.id}` : null,
    );
  });

  await run("credentials", async () => {
    const credentials = (await api<ApiRecord[]>("/credentials")) ?? [];
    await deleteMatching("test credentials", credentials, CREDENTIAL_PATTERNS, (credential) =>
      credential.name ? `/credentials/${encodeURIComponent(credential.name)}` : null,
    );
  });

  // A worker can finish publishing a test project while the first cleanup
  // pass is listing records. Re-scan after worker/flow cleanup to close that
  // race without broadening the fixture-name allowlist.
  let lateProjectsDeleted = 0;
  await run("late projects", async () => {
    lateProjectsDeleted = await cleanupProjects();
  });
  if (lateProjectsDeleted > 0) {
    let stablePasses = 0;
    for (let pass = 1; pass <= 30 && stablePasses < 10; pass += 1) {
      await delay(1_000);
      await run(`late projects pass ${pass + 1}`, async () => {
        const deleted = await cleanupProjects();
        stablePasses = deleted === 0 ? stablePasses + 1 : 0;
      });
    }
  }

  if (errors.length > 0) {
    console.warn(`[e2e cleanup] skipped some cleanup: ${errors.join("; ")}`);
  }
}
