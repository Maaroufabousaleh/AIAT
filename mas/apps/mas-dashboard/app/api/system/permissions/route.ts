import { NextResponse } from "next/server";

const POLICY_RULES = {
  orchestrator: {
    allowed_targets: ["*"],
    allowed_msg_types: ["*"],
    human_interface: true,
    allowed_tools: ["*"],
  },
  executive: {
    allowed_targets: ["role:orchestrator", "role:c_suite", "role:admin"],
    allowed_msg_types: [
      "TASK", "RESULT", "ADMIN_TASK", "ADMIN_REPLY", "REVIEW_REQUEST", "REVIEW_RESPONSE",
      "DOCUMENT_SUBMIT", "DOCUMENT_REVISION", "DIRECTIVE", "BROADCAST", "SHUTDOWN",
      "SHUTDOWN_ACK", "ESCALATION", "HEARTBEAT", "SYSTEM_EVENT"
    ],
    allowed_tools: [
      "document.*", "review.*", "project.status", "project.transition", "project.list",
      "blob.*", "kpi.query_history", "department_task", "human.notify", "approval.*",
      "capability.register", "capability.deregister", "capability.search", "capability.list_workers"
    ],
  },
  c_suite: {
    allowed_targets: ["role:orchestrator", "role:executive", "role:c_suite", "team:own"],
    allowed_msg_types: [
      "TASK", "RESULT", "QUERY", "RESPONSE", "REVIEW_REQUEST", "REVIEW_RESPONSE",
      "SPRINT_PLAN", "SPRINT_REPORT", "ISSUE_ASSIGN", "ISSUE_COMPLETE", "ADMIN_TASK",
      "ADMIN_REPLY", "ESCALATION", "INFRA_READY", "DIRECTIVE", "SHUTDOWN_ACK"
    ],
    cross_team_msg_types: [
      "REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY", "SPRINT_PLAN", "DIRECTIVE", "INFRA_READY"
    ],
    allowed_tools: [
      "document.get_latest", "document.list", "blob.download", "blob.list",
      "web_search", "web_fetch", "review.submit", "review.submit_veto",
      "approval.override_cso", "capability.search", "capability.list_workers"
    ],
    cto_extra_tools: [
      "sprint.*", "issue.*", "kpi.compute", "kpi.query_history", "kpi.update_agent_profile",
      "velocity.report", "estimation.adjust", "capability.search", "capability.list_workers"
    ],
  },
  admin: {
    allowed_targets: ["role:executive", "role:c_suite:cto", "team:own"],
    allowed_msg_types: [
      "TASK", "RESULT", "QUERY", "RESPONSE", "ADMIN_TASK", "DOCUMENT_SUBMIT",
      "DOCUMENT_REVISION", "ISSUE_ASSIGN", "ISSUE_COMPLETE", "SPRINT_REPORT",
      "ESCALATION", "INFRA_READY", "ADMIN_REPLY", "SHUTDOWN_ACK"
    ],
    allowed_tools: [
      "document.create_draft", "document.submit", "document.revise", "document.get_latest",
      "document.list", "blob.*", "issue.update_status", "capability.search", "capability.list_workers",
      "file_read", "file_write", "shared_memory_read", "shared_memory_write"
    ],
    supplemental_admin_tools: [],
  },
  worker: {
    allowed_targets: ["team:own"],
    allowed_msg_types: ["TASK", "RESULT", "QUERY", "RESPONSE", "ISSUE_COMPLETE", "ESCALATION", "ADMIN_REPLY", "SHUTDOWN_ACK"],
    allowed_tools: [
      "document.get_latest", "document.list", "blob.upload", "blob.download", "blob.list",
      "web_search", "web_fetch", "file_read", "file_write", "shared_memory_read", "shared_memory_write"
    ],
    blocked_tools: [
      "project.*", "approval.*", "review.start_session", "review.aggregate",
      "sprint.create", "sprint.activate", "infra.provision", "cicd.configure",
      "monitoring.setup", "secrets.manage", "infra.ready_signal"
    ],
  },
  sub_agent: {
    allowed_targets: ["parent:only"],
    allowed_msg_types: ["RESULT", "QUERY"],
    allowed_tools: ["blob.download", "web_search"],
  },
};

const TEAM_TIERS = {
  exec_ceo: "orchestrator",
  exec_coo: "executive",
  office_cfo: "c_suite",
  office_cio: "c_suite",
  office_chrm: "c_suite",
  office_cso: "c_suite",
  office_cto: "c_suite",
  dept_production: "admin",
  dept_system: "admin",
  dept_qa: "admin",
  dept_devops: "admin",
};

const MESSAGE_TYPE_GROUPS = {
  core: ["TASK", "RESULT", "QUERY", "RESPONSE", "BROADCAST", "ADMIN_TASK", "ADMIN_REPLY", "SHUTDOWN"],
  document: ["DOCUMENT_SUBMIT", "DOCUMENT_REVISION"],
  review: ["REVIEW_REQUEST", "REVIEW_RESPONSE"],
  human: ["APPROVAL_REQUEST", "APPROVAL_RESPONSE"],
  sprint: ["SPRINT_PLAN", "SPRINT_REPORT", "ISSUE_ASSIGN", "ISSUE_COMPLETE"],
  hierarchy: ["ESCALATION", "DIRECTIVE"],
  system: ["HEARTBEAT", "ACK", "SHUTDOWN_ACK", "SHUTDOWN_NACK", "SYSTEM_EVENT", "INFRA_READY"],
};

function buildCommunicationMatrix() {
  const matrix: Record<string, Record<string, { allowed: boolean; msgTypes: string[] }>> = {};
  const roles = ["orchestrator", "executive", "c_suite", "admin", "worker", "sub_agent"];
  const targets = [
    "exec_ceo",
    "exec_coo",
    "office_cfo",
    "office_cio",
    "office_chrm",
    "office_cso",
    "office_cto",
    "dept_production",
    "dept_system",
    "dept_qa",
    "dept_devops",
  ];
  
  const rolePermissions: Record<string, Record<string, string[]>> = {
    orchestrator: {},
    executive: {
      "exec_ceo": ["TASK", "RESULT", "ADMIN_TASK", "ADMIN_REPLY", "DIRECTIVE", "ESCALATION", "SHUTDOWN_ACK"],
      "office_cfo": ["TASK", "ADMIN_TASK", "REVIEW_REQUEST", "DOCUMENT_SUBMIT", "DIRECTIVE"],
      "office_cio": ["TASK", "ADMIN_TASK", "REVIEW_REQUEST", "DOCUMENT_SUBMIT", "DIRECTIVE"],
      "office_chrm": ["TASK", "ADMIN_TASK", "REVIEW_REQUEST", "DOCUMENT_SUBMIT", "DIRECTIVE"],
      "office_cso": ["TASK", "ADMIN_TASK", "REVIEW_REQUEST", "DOCUMENT_SUBMIT", "DIRECTIVE"],
      "office_cto": ["TASK", "ADMIN_TASK", "REVIEW_REQUEST", "DOCUMENT_SUBMIT", "DIRECTIVE", "SPRINT_PLAN"],
      "dept_production": ["TASK", "ADMIN_TASK", "DOCUMENT_SUBMIT", "DIRECTIVE"],
      "dept_system": ["TASK", "ADMIN_TASK", "DOCUMENT_SUBMIT", "DIRECTIVE"],
      "dept_qa": ["TASK", "ADMIN_TASK", "DIRECTIVE"],
      "dept_devops": ["TASK", "ADMIN_TASK", "DIRECTIVE"],
    },
    c_suite: {
      "exec_ceo": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "exec_coo": ["TASK", "RESULT", "QUERY", "RESPONSE", "ADMIN_TASK", "DIRECTIVE"],
      "office_cfo": ["REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_cio": ["REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_chrm": ["REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_cso": ["REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_cto": ["REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "dept_production": ["SPRINT_PLAN", "DIRECTIVE", "ESCALATION", "ADMIN_REPLY"],
      "dept_system": ["SPRINT_PLAN", "DIRECTIVE", "ESCALATION", "ADMIN_REPLY"],
      "dept_qa": ["SPRINT_PLAN", "DIRECTIVE", "ESCALATION", "ADMIN_REPLY"],
      "dept_devops": ["SPRINT_PLAN", "DIRECTIVE", "INFRA_READY", "ESCALATION", "ADMIN_REPLY"],
    },
    admin: {
      "exec_coo": ["TASK", "RESULT", "QUERY", "RESPONSE", "SPRINT_REPORT", "ESCALATION", "ADMIN_REPLY"],
      "office_cto": ["TASK", "RESULT", "SPRINT_REPORT", "INFRA_READY", "ADMIN_REPLY"],
      "dept_production": ["TASK", "RESULT", "QUERY", "RESPONSE", "ADMIN_TASK", "ADMIN_REPLY", "ISSUE_ASSIGN", "ISSUE_COMPLETE"],
      "dept_system": ["TASK", "RESULT", "QUERY", "RESPONSE", "ADMIN_TASK", "ADMIN_REPLY", "ISSUE_ASSIGN", "ISSUE_COMPLETE"],
      "dept_qa": ["TASK", "RESULT", "QUERY", "RESPONSE", "ADMIN_TASK", "ADMIN_REPLY", "ISSUE_ASSIGN", "ISSUE_COMPLETE"],
      "dept_devops": ["TASK", "RESULT", "QUERY", "RESPONSE", "ADMIN_TASK", "ADMIN_REPLY", "ISSUE_ASSIGN", "ISSUE_COMPLETE", "INFRA_READY"],
    },
    worker: {
      "office_cfo": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_cio": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_chrm": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_cso": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "office_cto": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY"],
      "dept_production": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY", "ISSUE_COMPLETE"],
      "dept_system": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY", "ISSUE_COMPLETE"],
      "dept_qa": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY", "ISSUE_COMPLETE"],
      "dept_devops": ["TASK", "RESULT", "QUERY", "RESPONSE", "ESCALATION", "ADMIN_REPLY", "ISSUE_COMPLETE"],
    },
    sub_agent: {},
  };

  for (const senderRole of roles) {
    matrix[senderRole] = {};
    for (const targetTeam of targets) {
      const targetTier = TEAM_TIERS[targetTeam as keyof typeof TEAM_TIERS];
      let allowed = false;
      let msgTypes: string[] = [];

      if (senderRole === "orchestrator") {
        allowed = true;
        msgTypes = ["*"];
      } else {
        const rolePerms = rolePermissions[senderRole as keyof typeof rolePermissions];
        if (rolePerms && rolePerms[targetTeam]) {
          allowed = true;
          msgTypes = rolePerms[targetTeam];
        } else if (senderRole === "c_suite" && targetTier === "c_suite") {
          allowed = true;
          msgTypes = ["REVIEW_REQUEST", "REVIEW_RESPONSE", "ESCALATION", "ADMIN_REPLY"];
        }
      }

      matrix[senderRole][targetTeam] = { allowed, msgTypes };
    }
  }

  return matrix;
}

export async function GET() {
  try {
    const commMatrix = buildCommunicationMatrix();
    
    return NextResponse.json({
      policy: POLICY_RULES,
      teamTiers: TEAM_TIERS,
      messageTypes: MESSAGE_TYPE_GROUPS,
      communicationMatrix: commMatrix,
    });
  } catch (e) {
    console.error("Error loading permissions:", e);
    return NextResponse.json({ error: "Failed to load permissions" }, { status: 500 });
  }
}
