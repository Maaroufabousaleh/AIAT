type ProtocolVersion = "aiat.v1";

type MessageEnvelopeSample = {
  protocol_version: ProtocolVersion;
  msg_type: "TASK";
  sender_id: string;
  sender_role: "orchestrator";
  sender_team: string;
  recipient_id: string;
  project_id: string;
  payload: Record<string, unknown>;
};

type ToolRequestSample = {
  protocol_version: ProtocolVersion;
  agent_id: string;
  sender_role: "orchestrator";
  sender_team: string;
  project_id: string;
  tool_name: string;
  kwargs: Record<string, unknown>;
};

type ToolResponseSample = {
  protocol_version: ProtocolVersion;
  tool_name: string;
  success: boolean;
  result: Record<string, unknown>;
};

type WorkerManifestSample = {
  protocol_version: ProtocolVersion;
  metadata: {
    id: string;
    name: string;
    version: string;
    source_repo: string;
    version_pin: string;
  };
  runtime: {
    transport: "process" | "http" | "oci" | "mcp" | "human";
    adapter_config: Record<string, unknown>;
  };
  capabilities: Array<{ name: string; risk_level: "low" | "medium" | "high" }>;
  sandbox: {
    profile: "standard" | "restricted" | "gvisor" | "firecracker";
    network_mode: "egress-allowlist" | "egress-deny-all" | "unrestricted";
    egress_allowlist: string[];
  };
};

export const messageEnvelopeSample = {
  protocol_version: "aiat.v1",
  msg_type: "TASK",
  sender_id: "ceo_agent",
  sender_role: "orchestrator",
  sender_team: "exec_ceo",
  recipient_id: "worker_alpha",
  project_id: "proj-alpha",
  payload: { task: "validate_contract" },
} as const satisfies MessageEnvelopeSample;

export const toolRequestSample = {
  protocol_version: "aiat.v1",
  agent_id: "ceo_agent",
  sender_role: "orchestrator",
  sender_team: "exec_ceo",
  project_id: "proj-alpha",
  tool_name: "project.transition",
  kwargs: { event: "project_created" },
} as const satisfies ToolRequestSample;

export const toolResponseSample = {
  protocol_version: "aiat.v1",
  tool_name: "project.transition",
  success: true,
  result: { state: "FEASIBILITY_CHECK" },
} as const satisfies ToolResponseSample;

export const workerManifestSample = {
  protocol_version: "aiat.v1",
  metadata: {
    id: "worker_alpha",
    name: "Worker Alpha",
    version: "1.0.0",
    source_repo: "https://github.com/example/worker-alpha",
    version_pin: "v1.0.0",
  },
  runtime: {
    transport: "process",
    adapter_config: { command: "python -m worker_alpha" },
  },
  capabilities: [{ name: "validate_contract", risk_level: "low" }],
  sandbox: {
    profile: "restricted",
    network_mode: "egress-allowlist",
    egress_allowlist: ["api.github.com"],
  },
} as const satisfies WorkerManifestSample;
