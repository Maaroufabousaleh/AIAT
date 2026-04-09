"use client";

import { useState, useMemo } from "react";
import { clsx } from "clsx";
import {
  ArrowRight,
  Check,
  X,
  Users,
  MessageSquare,
  Wrench,
  ChevronDown,
  ChevronRight,
  Search,
} from "lucide-react";

import type { PermissionData, TeamInfo } from "@/lib/system-viz-types";
import { TIER_COLORS, TIER_LABELS } from "@/lib/system-viz-types";

interface PermissionsVizProps {
  permissions: PermissionData;
  teams: TeamInfo[];
  selectedTeam?: string | null;
  onTeamSelect?: (teamId: string) => void;
  onTracePath?: (from: string, to: string) => void;
}

const ROLE_LABELS: Record<string, string> = {
  orchestrator: "Orchestrator",
  executive: "Executive",
  c_suite: "C-Suite",
  admin: "Admin",
  worker: "Worker",
  sub_agent: "Sub-Agent",
};

const MESSAGE_ICONS: Record<string, string> = {
  TASK: "📋",
  RESULT: "✅",
  QUERY: "❓",
  RESPONSE: "💬",
  REVIEW_REQUEST: "👁️",
  REVIEW_RESPONSE: "📝",
  SPRINT_PLAN: "🏃",
  SPRINT_REPORT: "📊",
  ESCALATION: "⬆️",
  DIRECTIVE: "📢",
  INFRA_READY: "🚀",
  ADMIN_TASK: "⚙️",
  ADMIN_REPLY: "↩️",
  DOCUMENT_SUBMIT: "📄",
  DOCUMENT_REVISION: "✏️",
};

export function PermissionsViz({
  permissions,
  teams,
  selectedTeam,
  onTeamSelect,
  onTracePath,
}: PermissionsVizProps) {
  const [senderRole, setSenderRole] = useState<string>("executive");
  const [expandedMsgTypes, setExpandedMsgTypes] = useState<Set<string>>(new Set(["core"]));
  
  const roles = Object.keys(permissions.communicationMatrix);
  const targetTeams = teams.map(t => t.teamId);

  const toggleMsgTypeGroup = (group: string) => {
    setExpandedMsgTypes(prev => {
      const next = new Set(prev);
      if (next.has(group)) {
        next.delete(group);
      } else {
        next.add(group);
      }
      return next;
    });
  };

  const matrixEntry = permissions.communicationMatrix[senderRole];

  return (
    <div className="h-full flex flex-col">
      <div className="flex-shrink-0 p-4 border-b border-gray-800">
        <div className="flex items-center gap-4">
          <div className="flex-1">
            <label className="block text-xs text-gray-500 mb-1">Sender Role</label>
            <select
              value={senderRole}
              onChange={(e) => setSenderRole(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-white"
            >
              {roles.map(role => (
                <option key={role} value={role}>{ROLE_LABELS[role] || role}</option>
              ))}
            </select>
          </div>
          
          <div className="flex items-center gap-2 text-gray-400 mt-5">
            <ArrowRight size={20} className="rotate-90" />
          </div>
          
          <div className="flex-1">
            <label className="block text-xs text-gray-500 mb-1">Target Team</label>
            <div className="text-sm text-white font-medium">
              {selectedTeam ? teams.find(t => t.teamId === selectedTeam)?.displayName : "All teams"}
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {selectedTeam ? (
          <div className="space-y-6">
            <div className="bg-gray-900 rounded-lg p-4">
              <h3 className="text-sm font-medium text-white mb-3 flex items-center gap-2">
                <MessageSquare size={14} />
                Allowed Message Types
              </h3>
              <div className="flex flex-wrap gap-2">
                {matrixEntry?.[selectedTeam]?.msgTypes.map(msgType => (
                  <span
                    key={msgType}
                    className="inline-flex items-center gap-1 px-2 py-1 bg-green-900/30 text-green-400 text-xs rounded"
                  >
                    {MESSAGE_ICONS[msgType] || "📨"} {msgType}
                  </span>
                ))}
                {matrixEntry?.[selectedTeam]?.msgTypes.length === 0 && (
                  <span className="text-gray-500 text-sm">No messages allowed</span>
                )}
              </div>
            </div>

            <div className="bg-gray-900 rounded-lg p-4">
              <h3 className="text-sm font-medium text-white mb-3 flex items-center gap-2">
                <Wrench size={14} />
                Allowed Tools
              </h3>
              <div className="flex flex-wrap gap-2">
                {senderRole === "orchestrator" ? (
                  <span className="px-2 py-1 bg-amber-900/30 text-amber-400 text-xs rounded">
                    * (all tools)
                  </span>
                ) : (
                  (() => {
                    const policy = permissions.policy[senderRole] as Record<string, unknown> | undefined;
                    const tools = policy?.allowed_tools as string[] | undefined;
                    return tools?.map(tool => (
                      <span
                        key={tool}
                        className="px-2 py-1 bg-blue-900/30 text-blue-400 text-xs rounded font-mono"
                      >
                        {tool}
                      </span>
                    ));
                  })()
                )}
              </div>
            </div>

            {senderRole === "worker" && (
              <div className="bg-gray-900 rounded-lg p-4">
                <h3 className="text-sm font-medium text-white mb-3 flex items-center gap-2">
                  <X size={14} className="text-red-400" />
                  Blocked Tools
                </h3>
                <div className="flex flex-wrap gap-2">
                  {(() => {
                    const policy = permissions.policy.worker as Record<string, unknown> | undefined;
                    const blocked = policy?.blocked_tools as string[] | undefined;
                    return blocked?.map(tool => (
                      <span
                        key={tool}
                        className="px-2 py-1 bg-red-900/30 text-red-400 text-xs rounded font-mono"
                      >
                        {tool}
                      </span>
                    ));
                  })()}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div>
            <h3 className="text-sm font-medium text-gray-400 mb-3">Communication Matrix</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className="text-left text-gray-500 p-2">Target</th>
                    {targetTeams.slice(0, 8).map(teamId => (
                      <th key={teamId} className="text-gray-500 p-2 min-w-[60px]">
                        {teams.find(t => t.teamId === teamId)?.displayName.slice(0, 8) || teamId}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(matrixEntry || {}).slice(0, 8).map(([teamId, entry]) => (
                    <tr key={teamId} className="border-t border-gray-800">
                      <td className="text-gray-400 p-2">{teamId}</td>
                      {targetTeams.slice(0, 8).map(t => {
                        const targetEntry = matrixEntry[t];
                        const allowed = targetEntry?.allowed || false;
                        return (
                          <td key={t} className="text-center p-2">
                            {teamId === t ? (
                              <span className={clsx(
                                "inline-flex items-center justify-center w-5 h-5 rounded text-xs",
                                allowed ? "bg-green-900 text-green-400" : "bg-red-900 text-red-400"
                              )}>
                                {allowed ? "✓" : "✗"}
                              </span>
                            ) : null}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h3 className="text-sm font-medium text-gray-400 mt-6 mb-3">Message Type Groups</h3>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(permissions.messageTypes).map(([group, types]) => (
                <div
                  key={group}
                  className="bg-gray-900 rounded-lg overflow-hidden"
                >
                  <button
                    onClick={() => toggleMsgTypeGroup(group)}
                    className="w-full flex items-center justify-between p-2 text-sm text-gray-300 hover:bg-gray-800"
                  >
                    <span className="capitalize">{group}</span>
                    {expandedMsgTypes.has(group) ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                  {expandedMsgTypes.has(group) && (
                    <div className="px-2 pb-2 flex flex-wrap gap-1">
                      {types.map(type => (
                        <span key={type} className="text-xs text-gray-500">
                          {MESSAGE_ICONS[type] || "📨"} {type}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}