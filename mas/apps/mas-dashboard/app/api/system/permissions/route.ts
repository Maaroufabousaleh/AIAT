import { NextResponse } from "next/server";
import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";
import { orchestratorFetch, OrchestratorError } from "@/lib/orchestrator";

export const dynamic = "force-dynamic";

// Live teams/*.yaml discovery — identical to app/api/system/hierarchy/route.ts
// so a teams/*.yaml edit changes this response.
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

interface TeamYamlConfig {
  team_id: string;
  admin: {
    agent_id: string;
    role: string;
    display_name: string;
    tools: string[];
  };
  workers: Array<{
    agent_id: string;
    role: string;
    display_name: string;
    tools: string[];
  }>;
}

interface PolicyRule {
  allowed_targets: string[];
  allowed_msg_types: string[];
  cross_team_msg_types?: string[];
  [key: string]: unknown;
}

interface BackendPolicy {
  policy: Record<string, PolicyRule>;
  teamTiers: Record<string, string>;
  teamDisplayNames: Record<string, string>;
  teamRegistry: {
    orchestratorTeam: string;
    executiveTeam: string;
    ctoTeam: string;
    devopsTeam: string;
    cSuiteTeams: string[];
    deptTeams: string[];
  };
  messageTypes: Record<string, string[]>;
  messageTypeValues: string[];
}

function loadLiveTeams(): Record<string, TeamYamlConfig> {
  const teamsDir = findTeamsDir();
  if (!teamsDir) return {};
  let files: string[];
  try {
    files = fs.readdirSync(teamsDir).filter(f => f.endsWith(".yaml"));
  } catch {
    return {};
  }
  const teams: Record<string, TeamYamlConfig> = {};
  for (const file of files) {
    try {
      const content = fs.readFileSync(path.join(teamsDir, file), "utf-8");
      const config = yaml.load(content) as TeamYamlConfig;
      if (config?.team_id) teams[config.team_id] = config;
    } catch {
      // Skip unreadable team files; the enforced registry still applies.
    }
  }
  return teams;
}

// Communication matrix derived from the LIVE enforced policy (fetched from
// the orchestrator /system/policy endpoint) and LIVE team tiers (teams/*.yaml
// admin.role wins, enforced registry as fallback). Mirrors the structural
// rules of mas_core.policy.engine.CommunicationPolicy at role x team
// granularity: target reachability follows allowed_targets tiers plus the
// engine's ESCALATION/SHUTDOWN_ACK upward exceptions, while every message-type
// list is drawn from the live allowed/cross-team sets — never hardcoded.
function buildCommunicationMatrix(
  policy: BackendPolicy["policy"],
  teamTiers: Record<string, string>,
  orderedTeams: string[],
  registry: BackendPolicy["teamRegistry"],
): Record<string, Record<string, { allowed: boolean; msgTypes: string[] }>> {
  const matrix: Record<string, Record<string, { allowed: boolean; msgTypes: string[] }>> = {};
  const roles = ["orchestrator", "executive", "c_suite", "admin", "worker", "sub_agent"];

  for (const role of roles) {
    matrix[role] = {};
    const rule = policy[role];
    if (role === "orchestrator") {
      for (const target of orderedTeams) matrix[role][target] = { allowed: true, msgTypes: ["*"] };
      continue;
    }
    if (!rule) {
      for (const target of orderedTeams) matrix[role][target] = { allowed: false, msgTypes: [] };
      continue;
    }
    const allowed = rule.allowed_msg_types ?? [];
    const cross = (rule.cross_team_msg_types ?? []).filter(t => allowed.includes(t));

    for (const target of orderedTeams) {
      const tier = teamTiers[target];
      let types: string[] = [];

      if (role === "executive") {
        if (tier === "orchestrator" || tier === "executive" || tier === "c_suite" || tier === "admin") {
          types = allowed;
        }
      } else if (role === "c_suite") {
        if (tier === "orchestrator" || tier === "executive") {
          types = allowed;
        } else if (tier === "c_suite" || tier === "admin") {
          types = cross;
        }
      } else if (role === "admin") {
        if (tier === "executive" || tier === "admin" || target === registry.ctoTeam) {
          types = allowed;
        } else if (tier === "orchestrator") {
          // Engine upward exceptions: ESCALATION may skip to the CEO and any
          // role may confirm shutdown to the orchestrator.
          types = allowed.filter(t => t === "ESCALATION" || t === "SHUTDOWN_ACK");
        }
      } else if (role === "worker") {
        if (tier === "admin" || tier === "c_suite") {
          // Own-team approximation at role granularity: a worker's parent
          // team is always a c_suite or admin team per the engine.
          types = allowed;
        } else if (target === registry.executiveTeam) {
          // Engine ESCALATION exception: a worker may reach the COO directly.
          types = allowed.filter(t => t === "ESCALATION");
        } else if (tier === "orchestrator") {
          types = allowed.filter(t => t === "SHUTDOWN_ACK");
        }
      }
      // sub_agent is parent-team-only, unresolvable at role granularity:
      // every cross-team lookup stays denied.

      matrix[role][target] = { allowed: types.length > 0, msgTypes: types };
    }
  }

  return matrix;
}

export async function GET() {
  try {
    // Live enforced policy: role targets/message types/tools as the
    // message-router enforces them (mas_core.policy.rules), plus display
    // groups derived from the MessageType protocol enum.
    const backend = await orchestratorFetch<BackendPolicy>("/system/policy");

    // Live team config: role tiers and tool grants straight from teams/*.yaml.
    const liveTeams = loadLiveTeams();

    // Enforced registry as the base; a teams/*.yaml admin.role edit
    // overrides the tier, so config edits change this response.
    const teamTiers: Record<string, string> = { ...backend.teamTiers };
    for (const [teamId, config] of Object.entries(liveTeams)) {
      if (config.admin?.role) teamTiers[teamId] = config.admin.role;
    }

    const registry = backend.teamRegistry;
    const orderedTeams = [
      registry.orchestratorTeam,
      registry.executiveTeam,
      ...registry.cSuiteTeams,
      ...registry.deptTeams,
    ].filter((teamId, index, all) => teamId && all.indexOf(teamId) === index);
    for (const teamId of Object.keys(teamTiers).sort()) {
      if (!orderedTeams.includes(teamId)) orderedTeams.push(teamId);
    }

    const teams = orderedTeams.map(teamId => {
      const live = liveTeams[teamId];
      return {
        teamId,
        displayName: backend.teamDisplayNames?.[teamId] ?? live?.admin.display_name ?? teamId,
        tier: teamTiers[teamId] ?? "admin",
        live: Boolean(live),
        admin: live
          ? {
              agentId: live.admin.agent_id,
              displayName: live.admin.display_name,
              role: live.admin.role,
              tools: live.admin.tools ?? [],
            }
          : null,
        workers: live
          ? (live.workers ?? []).map(w => ({
              agentId: w.agent_id,
              displayName: w.display_name,
              role: w.role,
              tools: w.tools ?? [],
            }))
          : [],
      };
    });

    const communicationMatrix = buildCommunicationMatrix(backend.policy, teamTiers, orderedTeams, registry);

    return NextResponse.json({
      policy: backend.policy,
      teamTiers,
      messageTypes: backend.messageTypes,
      communicationMatrix,
      teamRegistry: registry,
      teams,
    });
  } catch (e) {
    if (e instanceof OrchestratorError) return NextResponse.json({ error: e.message }, { status: e.status });
    console.error("Error loading permissions:", e);
    return NextResponse.json({ error: "Failed to load permissions" }, { status: 500 });
  }
}
