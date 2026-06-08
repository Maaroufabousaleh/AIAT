export const TEAM_STREAMS = [
  { id: "exec_ceo",        label: "CEO",        role: "C-Suite",    description: "Top-level executive stream — strategic directives and org-wide decisions broadcast from the CEO." },
  { id: "exec_coo",        label: "COO",        role: "C-Suite",    description: "Operations executive stream — scheduling, capacity, and cross-team coordination directives." },
  { id: "office_cfo",      label: "CFO",        role: "C-Office",   description: "Financial stream — budget approvals, spend reports, and cost optimisation messages." },
  { id: "office_cio",      label: "CIO",        role: "C-Office",   description: "Information / data stream — data governance, analytics, and reporting traffic." },
  { id: "office_chrm",     label: "CHRM",       role: "C-Office",   description: "Human resources stream — workforce planning, role assignments, and HR escalations." },
  { id: "office_cso",      label: "CSO",        role: "C-Office",   description: "Security stream — security incidents, access reviews, and compliance directives." },
  { id: "office_cto",      label: "CTO",        role: "C-Office",   description: "Technology stream — architecture decisions, tech-radar updates, and engineering standards." },
  { id: "dept_production", label: "Production", role: "Department", description: "Production department stream — build, release, and runtime operations traffic." },
  { id: "dept_system",     label: "System",     role: "Department", description: "System department stream — infrastructure, networking, and platform-level events." },
  { id: "dept_qa",         label: "QA",         role: "Department", description: "Quality assurance stream — test results, coverage reports, and defect escalations." },
  { id: "dept_devops",     label: "DevOps",     role: "Department", description: "DevOps stream — CI/CD pipeline events, deploy approvals, and incident reports." },
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
