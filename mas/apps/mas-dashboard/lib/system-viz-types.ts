export interface TeamHierarchyNode {
  teamId: string;
  displayName: string;
  role: string;
  admin: {
    agentId: string;
    displayName: string;
    role: string;
    tools: string[];
  };
  workers: Array<{
    agentId: string;
    displayName: string;
    role: string;
    minInstances: number;
    maxInstances: number;
    tools: string[];
  }>;
  tier: "orchestrator" | "executive" | "c_suite" | "admin";
  children: TeamHierarchyNode[];
}

export interface TeamInfo {
  teamId: string;
  displayName: string;
  tier: "orchestrator" | "executive" | "c_suite" | "admin";
  admin: {
    agentId: string;
    displayName: string;
    role: string;
    class: string;
    budget: {
      max_llm_calls: number;
      max_tool_calls: number;
      max_cost_usd: number;
    };
    tools: string[];
  };
  workers: Array<{
    agentId: string;
    displayName: string;
    role: string;
    minInstances: number;
    maxInstances: number;
    budget: {
      max_llm_calls: number;
      max_tool_calls: number;
      max_cost_usd: number;
    };
    tools: string[];
  }>;
}

export interface WorkflowState {
  id: string;
  label: string;
  description: string;
}

export interface OrchestrationFlow {
  id: string;
  name: string;
  description: string;
  nodes: Array<{
    id: string;
    type: string;
    label: string;
    state?: string;
    team?: string;
    approver?: string;
  }>;
  edges: Array<{
    source: string;
    target: string;
    condition?: string;
  }>;
}

export interface PermissionMatrix {
  [senderRole: string]: {
    [targetTeam: string]: {
      allowed: boolean;
      msgTypes: string[];
    };
  };
}

export interface SystemData {
  teams: TeamInfo[];
  hierarchy: TeamHierarchyNode[];
}

export interface PermissionData {
  policy: Record<string, unknown>;
  teamTiers: Record<string, string>;
  messageTypes: Record<string, string[]>;
  communicationMatrix: PermissionMatrix;
}

export interface OrchestrationData {
  states: WorkflowState[];
  transitions: Record<string, string[]>;
  flows: OrchestrationFlow[];
  dbFlows: unknown[];
}

export type ViewMode = "hierarchy" | "permissions" | "orchestration";

export const TIER_COLORS: Record<string, string> = {
  orchestrator: "#f59e0b",
  executive: "#3b82f6",
  c_suite: "#8b5cf6",
  admin: "#10b981",
};

export const TIER_LABELS: Record<string, string> = {
  orchestrator: "Orchestrator (CEO)",
  executive: "Executive (COO)",
  c_suite: "C-Suite",
  admin: "Admin (Dept PM)",
};