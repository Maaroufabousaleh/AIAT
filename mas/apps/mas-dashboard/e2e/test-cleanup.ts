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
  /^aiat_e2e_project_[a-z0-9_]+$/i,
  /^live_probe_\d+_[a-f0-9]+$/,
  /^rebuilt_probe_\d+_[a-f0-9]+$/,
  /^AIAT live tool audit [a-f0-9]+$/,
  /^G-chief-scenarios-[a-f0-9-]+$/i,
  /^ceo_live_project_\d+$/,
];

const WORKER_PATTERNS = [
  /^aiat_smoke_[a-z0-9_]+$/i,
  /^aiat_e2e_worker_[a-z0-9_]+$/i,
  /^aiat_e2e_candidate_\d+$/i,
  /^live_probe_\d+_[a-f0-9]+_worker$/,
  /^live-audit-worker-[a-f0-9]+$/,
  /^rebuilt_probe_\d+_[a-f0-9]+_worker$/,
];

const FLOW_PATTERNS = [
  /^aiat_e2e_flow_[a-z0-9_]+$/i,
];

const CREDENTIAL_PATTERNS = [/^E2E_SECRET_\d+$/, /^TEST_SECRET_\d+$/];

function matchesAny(value: unknown, patterns: RegExp[]): boolean {
  return typeof value === "string" && patterns.some((pattern) => pattern.test(value));
}

export function isKnownE2EProjectName(value: unknown): boolean {
  return matchesAny(value, PROJECT_PATTERNS);
}

export function isKnownE2EFlowName(value: unknown): boolean {
  return matchesAny(value, FLOW_PATTERNS);
}

export function isKnownE2EWorkerName(value: unknown): boolean {
  return matchesAny(value, WORKER_PATTERNS);
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
  isFixture: (value: unknown) => boolean,
  deletePath: (record: ApiRecord) => string | null,
): Promise<number> {
  let deleted = 0;
  for (const record of records) {
    const candidate = record.worker_id ?? record.name;
    if (!isFixture(candidate)) continue;
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
    return deleteMatching("test projects", projects, isKnownE2EProjectName, (project) =>
      project.id ? `/projects/${project.id}` : null,
    );
  };

  await run("projects", async () => {
    await cleanupProjects();
  });

  await run("workers", async () => {
    const workers = (await api<ApiRecord[]>("/capabilities/workers")) ?? [];
    await deleteMatching("test workers", workers, isKnownE2EWorkerName, (worker) =>
      worker.id ? `/capabilities/workers/${worker.id}?permanent=true` : null,
    );
  });

  await run("flows", async () => {
    const flows = (await api<ApiRecord[]>("/flows?limit=1000")) ?? [];
    await deleteMatching("test flows", flows, isKnownE2EFlowName, (flow) =>
      flow.id ? `/flows/${flow.id}` : null,
    );
  });

  await run("credentials", async () => {
    const credentials = (await api<ApiRecord[]>("/credentials")) ?? [];
    await deleteMatching("test credentials", credentials, (value) => matchesAny(value, CREDENTIAL_PATTERNS), (credential) =>
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
