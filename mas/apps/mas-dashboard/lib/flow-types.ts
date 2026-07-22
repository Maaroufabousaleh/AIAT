export type FlowNodeType = "start" | "end" | "task" | "approval" | "condition" | "parallel" | "join" | "switch" | "escalate";

export type FlowInstanceStatus = 
  | "NOT_STARTED" 
  | "RUNNING" 
  | "WAITING_APPROVAL" 
  | "PAUSED" 
  | "CANCELLED" 
  | "COMPLETED" 
  | "FAILED";

export type FlowExecutionStatus = "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED" | "RETRYING";

export interface FlowNodeConfig {
  worker_id?: string;
  action?: string;
  team_id?: string;
  runtime_type?: string;
  adapter_version?: string;
  steward_id?: string;
  skill_bundle_version?: string;
  model_profile_id?: string;
  model_mode?: "none" | "aiat_gateway" | "certified_external_runtime" | "hybrid";
  task_type?: string;
  required_capabilities?: string[];
  permission_requirements?: string[];
  project_workspace_mode?: "isolated" | "shared_readonly" | "approved_write";
  tool_grants?: string[];
  budget?: Record<string, number>;
  retry_policy?: { max_attempts?: number; backoff_seconds?: number; strategies?: string[] };
  cancellation_policy?: { cooperative?: boolean; force_after_seconds?: number };
  checkpoint_policy?: { mode?: string; required?: boolean; resume_from_last_safe_node?: boolean };
  artifact_expectations?: Array<{ name: string; kind?: string; required?: boolean }>;
  completion_criteria?: Record<string, unknown>;
  runtime_extensions?: Record<string, unknown>;
  approver_role?: string;
  approver_user?: string;
  expression?: string;
  branches?: string[];
  switch_key?: string;
  switch_cases?: Record<string, string>;
  escalate_to_team?: string;
  escalate_to_agent?: string;
  timeout_seconds?: number;
  retries?: number;
  [key: string]: unknown;
}

export interface FlowNodeDefinition {
  id: string;
  type: FlowNodeType;
  label: string;
  config: FlowNodeConfig;
  position?: { x: number; y: number };
}

export interface FlowEdgeDefinition {
  id: string;
  source: string;
  target: string;
  condition?: string;
}

export interface FlowDefinition {
  nodes: FlowNodeDefinition[];
  edges: FlowEdgeDefinition[];
}

export interface Flow {
  id: string;
  name: string;
  description?: string;
  definition_json: FlowDefinition;
  version: number;
  created_by: string;
  is_active: boolean;
  metadata_json?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface FlowInstance {
  id: string;
  flow_id: string;
  flow_version: number;
  project_id: string;
  active_node_ids: string[];
  status: FlowInstanceStatus;
  context_json: Record<string, unknown>;
  started_at?: string;
  completed_at?: string;
  retry_count?: number;
  max_retries?: number;
  escalated_to?: string;
  escalation_reason?: string;
  created_at: string;
  updated_at: string;
}

export interface FlowNodeExecution {
  id: string;
  instance_id: string;
  node_id: string;
  node_type: FlowNodeType;
  node_label?: string;
  status: FlowExecutionStatus;
  input_json?: Record<string, unknown>;
  output_json?: Record<string, unknown>;
  error?: string;
  started_at: string;
  completed_at?: string;
}

export const FLOW_NODE_COLORS: Record<FlowNodeType, string> = {
  start: "bg-green-500",
  end: "bg-red-500",
  task: "bg-blue-500",
  approval: "bg-amber-500",
  condition: "bg-purple-500",
  parallel: "bg-cyan-500",
  join: "bg-indigo-500",
  switch: "bg-pink-500",
  escalate: "bg-orange-500",
};

export const FLOW_STATUS_COLORS: Record<FlowInstanceStatus, string> = {
  NOT_STARTED: "bg-gray-500",
  RUNNING: "bg-blue-500",
  WAITING_APPROVAL: "bg-amber-500",
  PAUSED: "bg-yellow-500",
  CANCELLED: "bg-stone-500",
  COMPLETED: "bg-emerald-500",
  FAILED: "bg-rose-500",
};

export const NODE_TYPE_LABELS: Record<FlowNodeType, string> = {
  start: "Start",
  end: "End",
  task: "Task",
  approval: "Approval",
  condition: "Condition",
  parallel: "Parallel",
  join: "Join",
  switch: "Switch",
  escalate: "Escalate",
};
