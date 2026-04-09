export const TEAM_STREAMS = [
  { id: "exec_ceo",        label: "CEO",        role: "C-Suite" },
  { id: "exec_coo",        label: "COO",        role: "C-Suite" },
  { id: "office_cfo",      label: "CFO",        role: "C-Office" },
  { id: "office_cio",      label: "CIO",        role: "C-Office" },
  { id: "office_chrm",     label: "CHRM",       role: "C-Office" },
  { id: "office_cso",      label: "CSO",        role: "C-Office" },
  { id: "office_cto",      label: "CTO",        role: "C-Office" },
  { id: "dept_production", label: "Production", role: "Department" },
  { id: "dept_system",     label: "System",     role: "Department" },
  { id: "dept_qa",         label: "QA",         role: "Department" },
  { id: "dept_devops",     label: "DevOps",     role: "Department" },
] as const;

export type TeamStreamId = (typeof TEAM_STREAMS)[number]["id"];

export const WORKFLOW_STATES = [
  "INIT",
  "FEASIBILITY_CHECK",
  "FEASIBILITY_REPORT",
  "PDR_CREATION",
  "PDR_REVIEW",
  "SECURITY_BLOCKED",
  "CDR_CREATION",
  "CDR_REVIEW",
  "HUMAN_APPROVAL",
  "RR_CREATION",
  "SPRINT_PLANNING",
  "INFRA_PROVISIONING",
  "IN_PROGRESS",
  "RETROSPECTIVE",
  "KPI_PERSISTENCE",
  "COMPLETED",
  "ARCHIVED",
  "FAILED",
] as const;

export type WorkflowState = (typeof WORKFLOW_STATES)[number];

export const TERMINAL_STATES: WorkflowState[] = ["COMPLETED", "ARCHIVED", "FAILED"];

export const STATE_COLORS: Record<WorkflowState, string> = {
  INIT:               "bg-gray-500",
  FEASIBILITY_CHECK:  "bg-blue-600",
  FEASIBILITY_REPORT: "bg-blue-500",
  PDR_CREATION:       "bg-indigo-600",
  PDR_REVIEW:         "bg-indigo-500",
  SECURITY_BLOCKED:   "bg-red-600",
  CDR_CREATION:       "bg-violet-600",
  CDR_REVIEW:         "bg-violet-500",
  HUMAN_APPROVAL:     "bg-amber-500",
  RR_CREATION:        "bg-cyan-600",
  SPRINT_PLANNING:    "bg-teal-600",
  INFRA_PROVISIONING: "bg-teal-500",
  IN_PROGRESS:        "bg-green-600",
  RETROSPECTIVE:      "bg-lime-600",
  KPI_PERSISTENCE:    "bg-lime-500",
  COMPLETED:          "bg-emerald-600",
  ARCHIVED:           "bg-stone-500",
  FAILED:             "bg-rose-600",
};

export const MSG_TYPE_COLORS: Record<string, string> = {
  DIRECTIVE:    "bg-blue-600",
  REPORT:       "bg-green-600",
  TOOL_CALL:    "bg-orange-500",
  TOOL_RESULT:  "bg-yellow-500 text-gray-900",
  VETO:         "bg-red-600",
  SHUTDOWN:     "bg-gray-600",
  HEARTBEAT:    "bg-gray-400",
  TASK_ASSIGN:  "bg-purple-600",
  TASK_RESULT:  "bg-purple-400",
  SYSTEM_EVENT: "bg-slate-500",
};

export const CONTAINER_NAMES = [
  "mas-orchestrator-api",
  "mas-message-router",
  "mas-tool-service",
  "mas-team-exec-ceo",
  "mas-team-exec-coo",
  "mas-team-office-cfo",
  "mas-team-office-cio",
  "mas-team-office-chrm",
  "mas-team-office-cso",
  "mas-team-office-cto",
  "mas-team-dept-production",
  "mas-team-dept-system",
  "mas-team-dept-qa",
  "mas-team-dept-devops",
] as const;
