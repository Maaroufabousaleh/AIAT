import { NextResponse } from "next/server";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

const WORKFLOW_STATES = [
  { id: "INIT", label: "Init", description: "Project created, waiting for feasibility check" },
  { id: "FEASIBILITY_CHECK", label: "Feasibility Check", description: "CTO reviewing technical feasibility" },
  { id: "FEASIBILITY_REPORT", label: "Feasibility Report", description: "Feasibility analysis completed" },
  { id: "PDR_CREATION", label: "PDR Creation", description: "Product doc being created" },
  { id: "PDR_REVIEW", label: "PDR Review", description: "C-Suite reviewing product document" },
  { id: "CDR_CREATION", label: "CDR Creation", description: "Technical design being created" },
  { id: "CDR_REVIEW", label: "CDR Review", description: "Technical design under review" },
  { id: "HUMAN_APPROVAL", label: "Human Approval", description: "Awaiting human decision" },
  { id: "RR_CREATION", label: "RR Creation", description: "Requirements document being created" },
  { id: "SPRINT_PLANNING", label: "Sprint Planning", description: "Sprints being planned" },
  { id: "INFRA_PROVISIONING", label: "Technical Readiness", description: "CTO validating technical readiness" },
  { id: "IN_PROGRESS", label: "In Progress", description: "Sprints executing" },
  { id: "RETROSPECTIVE", label: "Retrospective", description: "Sprint completed, retrospective done" },
  { id: "KPI_PERSISTENCE", label: "KPI Persistence", description: "Metrics being recorded" },
  { id: "COMPLETED", label: "Completed", description: "Project fully completed" },
  { id: "SECURITY_BLOCKED", label: "Security Blocked", description: "Blocked by CSO review" },
  { id: "FAILED", label: "Failed", description: "Project failed" },
  { id: "ARCHIVED", label: "Archived", description: "Project archived" },
];

const STATE_TRANSITIONS: Record<string, string[]> = {
  INIT: ["FEASIBILITY_CHECK", "FAILED"],
  FEASIBILITY_CHECK: ["FEASIBILITY_REPORT", "FAILED"],
  FEASIBILITY_REPORT: ["PDR_CREATION", "FAILED"],
  PDR_CREATION: ["PDR_REVIEW"],
  PDR_REVIEW: ["CDR_CREATION", "PDR_CREATION", "FAILED", "SECURITY_BLOCKED"],
  CDR_CREATION: ["CDR_REVIEW"],
  CDR_REVIEW: ["HUMAN_APPROVAL", "CDR_CREATION", "FAILED"],
  HUMAN_APPROVAL: ["RR_CREATION", "HUMAN_APPROVAL", "FAILED"],
  RR_CREATION: ["SPRINT_PLANNING"],
  SPRINT_PLANNING: ["INFRA_PROVISIONING"],
  INFRA_PROVISIONING: ["IN_PROGRESS", "FAILED"],
  IN_PROGRESS: ["RETROSPECTIVE", "FAILED"],
  RETROSPECTIVE: ["KPI_PERSISTENCE", "IN_PROGRESS"],
  KPI_PERSISTENCE: ["COMPLETED", "IN_PROGRESS"],
  COMPLETED: ["ARCHIVED"],
  FAILED: ["INIT"],
  ARCHIVED: ["INIT"],
  SECURITY_BLOCKED: ["INIT"],
};

const ORCHESTRATION_FLOWS = [
  {
    id: "project-lifecycle",
    name: "Project Lifecycle",
    description: "Standard project workflow from init to completion",
    nodes: [
      { id: "n1", type: "start", label: "Project Created", state: "INIT" },
      { id: "n2", type: "task", label: "Feasibility Check", state: "FEASIBILITY_CHECK", team: "office_cto" },
      { id: "n3", type: "condition", label: "Feasible?" },
      { id: "n4", type: "task", label: "PDR Review", state: "PDR_REVIEW", team: "office_cso" },
      { id: "n5", type: "task", label: "CDR Review", state: "CDR_REVIEW", team: "office_cto" },
      { id: "n6", type: "approval", label: "Human Approval", state: "HUMAN_APPROVAL", approver: "human" },
      { id: "n7", type: "task", label: "Sprint Planning", state: "SPRINT_PLANNING", team: "office_cto" },
      { id: "n8", type: "task", label: "Technical Readiness", state: "INFRA_PROVISIONING", team: "office_cto" },
      { id: "n9", type: "parallel", label: "Execute Sprints" },
      { id: "n10", type: "join", label: "Sprints Complete" },
      { id: "n11", type: "task", label: "KPI Recording", state: "KPI_PERSISTENCE", team: "office_cto" },
      { id: "n12", type: "end", label: "Completed", state: "COMPLETED" },
      { id: "n13", type: "end", label: "Failed", state: "FAILED" },
    ],
    edges: [
      { source: "n1", target: "n2" },
      { source: "n2", target: "n3" },
      { source: "n3", target: "n4", condition: "feasible" },
      { source: "n3", target: "n13", condition: "not feasible" },
      { source: "n4", target: "n5" },
      { source: "n5", target: "n6" },
      { source: "n6", target: "n7", condition: "approved" },
      { source: "n6", target: "n13", condition: "rejected" },
      { source: "n7", target: "n8" },
      { source: "n8", target: "n9" },
      { source: "n9", target: "n10" },
      { source: "n10", target: "n11" },
      { source: "n11", target: "n12" },
    ],
  },
  {
    id: "review-flow",
    name: "Document Review Flow",
    description: "Parallel document review by C-Suite",
    nodes: [
      { id: "r1", type: "start", label: "Review Started" },
      { id: "r2", type: "parallel", label: "C-Suite Reviews" },
      { id: "r3", type: "task", label: "CFO Review", team: "office_cfo" },
      { id: "r4", type: "task", label: "CIO Review", team: "office_cio" },
      { id: "r5", type: "task", label: "CHRM Review", team: "office_chrm" },
      { id: "r6", type: "task", label: "CSO Review", team: "office_cso" },
      { id: "r7", type: "task", label: "CTO Review", team: "office_cto" },
      { id: "r8", type: "join", label: "All Reviews Complete" },
      { id: "r9", type: "task", label: "Aggregate Review", team: "exec_coo" },
      { id: "r10", type: "approval", label: "CSO Veto Check" },
      { id: "r11", type: "end", label: "Approved" },
      { id: "r12", type: "end", label: "Blocked" },
    ],
    edges: [
      { source: "r1", target: "r2" },
      { source: "r2", target: "r3" },
      { source: "r2", target: "r4" },
      { source: "r2", target: "r5" },
      { source: "r2", target: "r6" },
      { source: "r2", target: "r7" },
      { source: "r3", target: "r8" },
      { source: "r4", target: "r8" },
      { source: "r5", target: "r8" },
      { source: "r6", target: "r8" },
      { source: "r7", target: "r8" },
      { source: "r8", target: "r9" },
      { source: "r9", target: "r10" },
      { source: "r10", target: "r11", condition: "no veto" },
      { source: "r10", target: "r12", condition: "veto" },
    ],
  },
  {
    id: "escalation-flow",
    name: "Escalation Flow",
    description: "Error and escalation handling workflow",
    nodes: [
      { id: "e1", type: "start", label: "Escalation Triggered" },
      { id: "e2", type: "switch", label: "Escalation Type", key: "escalation_type" },
      { id: "e3", type: "task", label: "Handle Worker Escalation", team: "office_chrm" },
      { id: "e4", type: "task", label: "Handle Admin Escalation", team: "exec_coo" },
      { id: "e5", type: "task", label: "Handle CTO Escalation", team: "exec_ceo" },
      { id: "e6", type: "task", label: "Handle Security Issue", team: "office_cso" },
      { id: "e7", type: "task", label: "Log & Notify", team: "exec_coo" },
      { id: "e8", type: "end", label: "Escalation Resolved" },
    ],
    edges: [
      { source: "e1", target: "e2" },
      { source: "e2", target: "e3", condition: "worker" },
      { source: "e2", target: "e4", condition: "admin" },
      { source: "e2", target: "e5", condition: "cto" },
      { source: "e2", target: "e6", condition: "security" },
      { source: "e3", target: "e7" },
      { source: "e4", target: "e7" },
      { source: "e5", target: "e7" },
      { source: "e6", target: "e7" },
      { source: "e7", target: "e8" },
    ],
  },
];

export async function GET() {
  try {
    let dbFlows: unknown[] = [];
    try {
      const data = await orchestratorFetch<{ flows: unknown[] }>("/flows?limit=100");
      dbFlows = data.flows || [];
    } catch {
      // Orchestrator may not be running, continue with static flows
    }

    return NextResponse.json({
      states: WORKFLOW_STATES,
      transitions: STATE_TRANSITIONS,
      flows: ORCHESTRATION_FLOWS,
      dbFlows,
    });
  } catch (e) {
    console.error("Error loading orchestration flows:", e);
    return NextResponse.json({ error: "Failed to load orchestration flows" }, { status: 500 });
  }
}
