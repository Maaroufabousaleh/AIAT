import { NextResponse } from "next/server";
import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";

export const dynamic = "force-dynamic";

function findTeamsDir(): string | null {
  const candidates = [
    process.env.TEAMS_DIR,
    path.resolve(process.cwd(), "..", "..", "teams"),
    path.resolve(process.cwd(), "teams"),
    "/app/teams",
    "/teams",
  ].filter((candidate): candidate is string => Boolean(candidate));

  return candidates.find(candidate => fs.existsSync(candidate)) ?? null;
}

interface TeamConfig {
  team_id: string;
  admin: {
    agent_id: string;
    role: string;
    class: string;
    display_name: string;
    system_prompt_file?: string;
    budget_defaults: {
      max_llm_calls: number;
      max_tool_calls: number;
      max_cost_usd: number;
    };
    tools: string[];
  };
  workers: Array<{
    agent_id: string;
    role: string;
    class: string;
    display_name: string;
    min_instances: number;
    max_instances: number;
    budget_defaults: {
      max_llm_calls: number;
      max_tool_calls: number;
      max_cost_usd: number;
    };
    tools: string[];
  }>;
}

interface TeamHierarchyNode {
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

const TIER_MAP: Record<string, "orchestrator" | "executive" | "c_suite" | "admin"> = {
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

const DISPLAY_NAMES: Record<string, string> = {
  exec_ceo: "CEO Office",
  exec_coo: "COO Office",
  office_cfo: "CFO Office",
  office_cio: "CIO Office",
  office_chrm: "CHRM Office",
  office_cso: "CSO Office",
  office_cto: "CTO Office",
  dept_production: "Production",
  dept_system: "System",
  dept_qa: "Quality Assurance",
  dept_devops: "DevOps",
};

const C_SUITE_TEAM_IDS = [
  "office_cfo",
  "office_cio",
  "office_chrm",
  "office_cso",
  "office_cto",
];

const CTO_DEPARTMENT_TEAM_IDS = [
  "dept_production",
  "dept_system",
  "dept_qa",
  "dept_devops",
];

function loadTeamConfig(teamId: string): TeamConfig | null {
  const teamsDir = findTeamsDir();
  if (!teamsDir) return null;

  const filePath = path.join(teamsDir, `${teamId}.yaml`);
  if (!fs.existsSync(filePath)) return null;
  const content = fs.readFileSync(filePath, "utf-8");
  return yaml.load(content) as TeamConfig;
}

function buildHierarchy(): TeamHierarchyNode[] {
  const teamsDir = findTeamsDir();
  if (!teamsDir) return [];

  const teams = fs.readdirSync(teamsDir).filter(f => f.endsWith(".yaml")).map(f => f.replace(".yaml", ""));
  
  const nodes: Record<string, TeamHierarchyNode> = {};
  
  for (const teamId of teams) {
    const config = loadTeamConfig(teamId);
    if (!config) continue;
    
    nodes[teamId] = {
      teamId: config.team_id,
      displayName: DISPLAY_NAMES[config.team_id] || config.team_id,
      role: config.admin.role,
      admin: {
        agentId: config.admin.agent_id,
        displayName: config.admin.display_name,
        role: config.admin.role,
        tools: config.admin.tools,
      },
      workers: config.workers.map(w => ({
        agentId: w.agent_id,
        displayName: w.display_name,
        role: w.role,
        minInstances: w.min_instances,
        maxInstances: w.max_instances,
        tools: w.tools,
      })),
      tier: TIER_MAP[config.team_id] || "admin",
      children: [],
    };
  }
  
  const hierarchy: TeamHierarchyNode[] = [];
  
  if (nodes.exec_ceo) {
    if (nodes.exec_coo) {
      nodes.exec_ceo.children = [nodes.exec_coo];
      nodes.exec_coo.children = C_SUITE_TEAM_IDS
        .map(teamId => nodes[teamId])
        .filter((node): node is TeamHierarchyNode => Boolean(node));

      if (nodes.office_cto) {
        nodes.office_cto.children = CTO_DEPARTMENT_TEAM_IDS
          .map(teamId => nodes[teamId])
          .filter((node): node is TeamHierarchyNode => Boolean(node));
      }
    }
    hierarchy.push(nodes.exec_ceo);
  }
  
  return hierarchy;
}

export async function GET() {
  try {
    const teamsDir = findTeamsDir();
    if (!teamsDir) {
      return NextResponse.json({ teams: [], hierarchy: [] });
    }
    const teams = fs.readdirSync(teamsDir).filter(f => f.endsWith(".yaml")).map(f => f.replace(".yaml", ""));
    
    const teamsData = teams.map(teamId => {
      const config = loadTeamConfig(teamId);
      if (!config) return null;
      
      return {
        teamId: config.team_id,
        displayName: DISPLAY_NAMES[config.team_id] || config.team_id,
        tier: TIER_MAP[config.team_id] || "admin",
        admin: {
          agentId: config.admin.agent_id,
          displayName: config.admin.display_name,
          role: config.admin.role,
          class: config.admin.class,
          budget: config.admin.budget_defaults,
          tools: config.admin.tools,
        },
        workers: config.workers.map(w => ({
          agentId: w.agent_id,
          displayName: w.display_name,
          role: w.role,
          minInstances: w.min_instances,
          maxInstances: w.max_instances,
          budget: w.budget_defaults,
          tools: w.tools,
        })),
      };
    }).filter(Boolean);
    
    const hierarchy = buildHierarchy();
    
    return NextResponse.json({
      teams: teamsData,
      hierarchy,
    });
  } catch (e) {
    console.error("Error loading team hierarchy:", e);
    return NextResponse.json({ error: "Failed to load team hierarchy" }, { status: 500 });
  }
}
