type ApiRecord = {
  id?: string;
  name?: string;
  worker_id?: string;
};

const ORCHESTRATOR_URL =
  process.env.E2E_ORCHESTRATOR_URL ??
  process.env.ORCHESTRATOR_URL ??
  "http://127.0.0.1:8000";

const PROJECT_PATTERNS = [
  /^aiat_smoke_[a-z0-9_]+$/i,
  /^gamma-workspace-\d+$/,
  /^flow-ui-project-\d+$/,
  /^test2-[a-z0-9-]+-\d+$/i,
  /^Test Project \d+$/,
  /^proj-\d+$/,
  /^live_probe_\d+_[a-f0-9]+$/,
  /^Live infra-ready audit \d+$/,
];

const WORKER_PATTERNS = [
  /^aiat_smoke_[a-z0-9_]+$/i,
  /^e2e_worker_\d+$/,
  /^e2e_candidate_\d+$/,
  /^test_evaluation_worker$/,
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
  const response = await fetch(`${ORCHESTRATOR_URL}${path}`, init);
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

  await run("projects", async () => {
    const projects = (await api<ApiRecord[]>("/projects?limit=1000")) ?? [];
    await deleteMatching("test projects", projects, PROJECT_PATTERNS, (project) =>
      project.id ? `/projects/${project.id}` : null,
    );
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

  if (errors.length > 0) {
    console.warn(`[e2e cleanup] skipped some cleanup: ${errors.join("; ")}`);
  }
}
