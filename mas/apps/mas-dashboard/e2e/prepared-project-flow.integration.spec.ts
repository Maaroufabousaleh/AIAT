import { randomUUID } from "node:crypto";
import { expect, test, type APIResponse } from "@playwright/test";
import { authenticate } from "./auth";

type JsonRecord = Record<string, unknown>;

async function readJson(response: APIResponse): Promise<unknown> {
  return response.json().catch(() => null);
}

function asRecord(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

async function recoverExactId(
  listResponse: APIResponse,
  exactName: string,
): Promise<string | undefined> {
  if (!listResponse.ok()) return undefined;
  const payload = await readJson(listResponse);
  if (!Array.isArray(payload)) return undefined;
  const exactMatches = payload
    .map(asRecord)
    .filter((row) => row.name === exactName && typeof row.id === "string");
  return exactMatches.length === 1 ? String(exactMatches[0].id) : undefined;
}

test("prepared project runs a worker-free approval flow and exposes canonical evidence", async ({ page }) => {
  test.skip(
    process.env.AIAT_ALLOW_EXTERNAL_PROVIDER_DISPATCH === "true",
    "This worker-free local test is only run with external provider dispatch disabled.",
  );

  await authenticate(page, "/projects");
  const api = page.request;
  const suffix = randomUUID();
  const projectName = `Prepared project integration ${suffix}`;
  const flowName = `Prepared project approval flow ${suffix}`;
  let projectId: string | undefined;
  let flowId: string | undefined;

  try {
    const createProject = await api.post("/api/projects", {
      data: {
        name: projectName,
        description: "Disposable operator-created project for a worker-free local integration path.",
        start_immediately: false,
        idempotency_key: `prepared-project-e2e-${suffix}`,
        workspace: { mode: "none" },
      },
    });
    const projectBody = asRecord(await readJson(createProject));
    expect(createProject.status(), JSON.stringify(projectBody)).toBe(201);
    projectId = typeof projectBody.id === "string" ? projectBody.id : undefined;
    if (!projectId) {
      projectId = await recoverExactId(await api.get("/api/projects?limit=1000"), projectName);
    }
    expect(projectId, "The created project must expose its canonical UUID").toMatch(/^[0-9a-f-]{36}$/i);
    test.info().annotations.push({ type: "fixture_project_id", description: projectId ?? "missing" });
    console.info("AIAT_PREPARED_PROJECT_FIXTURE", JSON.stringify({ project_id: projectId }));

    // Keep the project prepared. This is the explicit guard against CEO
    // feasibility work and any provider-backed governance dispatch.
    expect(projectBody.state).toBe("INIT");

    const definition = {
      schema_version: "1.0",
      nodes: [
        { id: "start", type: "start", label: "Start", config: {} },
        {
          id: "operator-approval",
          type: "approval",
          label: "Operator approval",
          config: { approver_role: "human_operator" },
        },
        { id: "end", type: "end", label: "Complete", config: {} },
      ],
      edges: [
        { id: "start-approval", source: "start", target: "operator-approval" },
        { id: "approval-end", source: "operator-approval", target: "end" },
      ],
    };
    const createFlow = await api.post("/api/flows", {
      data: {
        name: flowName,
        description: "Worker-free approval and completion path for a disposable local project.",
        definition_json: definition,
        created_by: "human",
        is_active: false,
      },
    });
    const flowBody = asRecord(await readJson(createFlow));
    expect(createFlow.status(), JSON.stringify(flowBody)).toBe(201);
    flowId = typeof flowBody.id === "string" ? flowBody.id : undefined;
    if (!flowId) {
      flowId = await recoverExactId(await api.get("/api/flows?limit=1000"), flowName);
    }
    expect(flowId, "The saved flow must expose its canonical UUID").toMatch(/^[0-9a-f-]{36}$/i);
    test.info().annotations.push({ type: "fixture_flow_id", description: flowId ?? "missing" });

    const attach = await api.post(`/api/projects/${projectId}/flow-instance`, {
      data: { flow_id: flowId },
    });
    const instanceBody = asRecord(await readJson(attach));
    expect(attach.status(), JSON.stringify(instanceBody)).toBe(200);
    const instanceId = String(instanceBody.id ?? "");
    expect(instanceId).toMatch(/^[0-9a-f-]{36}$/i);
    expect(instanceBody.project_id).toBe(projectId);
    expect(instanceBody.status).toBe("NOT_STARTED");

    const start = await api.post(`/api/flows/instances/${instanceId}/action`, {
      data: { action: "start" },
    });
    const started = asRecord(await readJson(start));
    expect(start.status(), JSON.stringify(started)).toBe(200);
    expect(started.active_node_ids).toContain("start");

    const completeStart = await api.post(`/api/flows/instances/${instanceId}/node-action`, {
      data: {
        node_id: "start",
        action: "complete",
        output: { integration_fixture: "prepared-project-worker-free" },
      },
    });
    const approvalStage = asRecord(await readJson(completeStart));
    expect(completeStart.status(), JSON.stringify(approvalStage)).toBe(200);
    expect(approvalStage.active_node_ids).toContain("operator-approval");

    const pendingResponse = await api.get(`/api/projects/${projectId}/decisions`);
    const pending = await readJson(pendingResponse);
    expect(pendingResponse.status(), JSON.stringify(pending)).toBe(200);
    expect(Array.isArray(pending)).toBe(true);
    const pendingGates = (pending as unknown[]).map(asRecord);
    expect(pendingGates).toHaveLength(1);
    expect(pendingGates[0].status).toBe("PENDING");
    const gateId = String(pendingGates[0].id ?? "");
    expect(gateId).toMatch(/^[0-9a-f-]{36}$/i);

    const decisionResponse = await api.post(`/api/projects/${projectId}/decisions`, {
      data: {
        gate_id: gateId,
        decision: "APPROVED",
        comments: "Approved by the disposable local integration operator.",
      },
    });
    const decision = asRecord(await readJson(decisionResponse));
    expect(decisionResponse.status(), JSON.stringify(decision)).toBe(200);
    expect(decision.status).toBe("decision_recorded");
    expect(decision.gate_id).toBe(gateId);
    expect(decision.flow_instance_id).toBe(instanceId);

    const noPendingResponse = await api.get(`/api/projects/${projectId}/decisions`);
    expect(noPendingResponse.status()).toBe(200);
    expect(await readJson(noPendingResponse)).toEqual([]);

    const resolvedInstanceResponse = await api.get(`/api/flows/instances/${instanceId}`);
    const resolvedInstance = asRecord(await readJson(resolvedInstanceResponse));
    expect(resolvedInstanceResponse.status(), JSON.stringify(resolvedInstance)).toBe(200);
    expect(resolvedInstance.status).toBe("COMPLETED");
    expect(resolvedInstance.active_node_ids).toEqual([]);

    const executionsResponse = await api.get(`/api/flows/instances/${instanceId}/executions`);
    const executionsPayload = await readJson(executionsResponse);
    expect(executionsResponse.status(), JSON.stringify(executionsPayload)).toBe(200);
    expect(Array.isArray(executionsPayload)).toBe(true);
    const executions = (executionsPayload as unknown[]).map(asRecord);
    expect(executions).toContainEqual(
      expect.objectContaining({
        node_id: "operator-approval",
        status: "COMPLETED",
        output_json: expect.objectContaining({ approval_gate_id: gateId }),
      }),
    );
    expect(executions).toContainEqual(
      expect.objectContaining({ node_id: "end", status: "COMPLETED" }),
    );

    const persistedProjectResponse = await api.get(`/api/projects/${projectId}`);
    const persistedProject = asRecord(await readJson(persistedProjectResponse));
    expect(persistedProjectResponse.status()).toBe(200);
    expect(persistedProject.state).toBe("INIT");

    const evidenceResponse = await api.get(`/api/projects/${projectId}/evidence/package`);
    const evidence = asRecord(await readJson(evidenceResponse));
    expect(evidenceResponse.status(), JSON.stringify(evidence)).toBe(200);
    expect(evidence.project_id).toBe(projectId);
    const items = Array.isArray(evidence.items) ? evidence.items.map(asRecord) : [];
    expect(items).toContainEqual(
      expect.objectContaining({
        id: instanceId,
        category: "flow",
        kind: "flow_instance",
        status: "COMPLETED",
      }),
    );
    expect(items).toContainEqual(
      expect.objectContaining({
        id: gateId,
        category: "approvals",
        status: "APPROVED",
      }),
    );

    const runsResponse = await api.get(`/api/governance/runs?project_id=${projectId}&limit=100`);
    expect(runsResponse.status()).toBe(200);
    expect(await readJson(runsResponse)).toEqual([]);
  } finally {
    // The project intentionally remains INIT, so archive is not a legal
    // lifecycle transition. Delete only this exact disposable project; its
    // workspace mode is none, so cleanup does not invoke repository tools.
    if (!projectId) {
      projectId = await recoverExactId(await api.get("/api/projects?limit=1000"), projectName);
    }
    if (projectId) {
      const removeProject = await api.delete(`/api/projects/${projectId}`);
      expect([200, 404]).toContain(removeProject.status());
      const verifyProject = await api.get(`/api/projects/${projectId}`);
      expect(verifyProject.status()).toBe(404);
      const verifyInstances = await api.get(`/api/flows/instances?project_id=${projectId}`);
      expect(verifyInstances.status()).toBe(200);
      expect(await readJson(verifyInstances)).toEqual([]);
      const verifyEvidence = await api.get(`/api/projects/${projectId}/evidence/package`);
      expect(verifyEvidence.status()).toBe(404);
    }

    if (!flowId) {
      flowId = await recoverExactId(await api.get("/api/flows?limit=1000"), flowName);
    }
    if (flowId) {
      const removeFlow = await api.delete(`/api/flows/${flowId}`);
      expect([200, 404]).toContain(removeFlow.status());
      const verifyFlow = await api.get(`/api/flows/${flowId}`);
      expect(verifyFlow.status()).toBe(404);
    }
  }
});
