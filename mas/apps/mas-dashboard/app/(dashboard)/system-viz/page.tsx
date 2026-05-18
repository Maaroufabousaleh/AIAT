"use client";

import { useEffect, useCallback, useMemo, useState } from "react";
import { clsx } from "clsx";
import {
  Network,
  Users,
  GitBranch,
  ChevronRight,
  RefreshCw,
  Info,
  ArrowRight,
  Search,
} from "lucide-react";

import { useSystemVizStore } from "@/lib/system-viz-store";
import { HierarchyViz } from "@/components/system-viz/HierarchyViz";
import { PermissionsViz } from "@/components/system-viz/PermissionsViz";
import { OrchestrationViz } from "@/components/system-viz/OrchestrationViz";
import type { ViewMode, TeamInfo, WorkflowState, OrchestrationFlow } from "@/lib/system-viz-types";

const VIEW_MODES: { id: ViewMode; label: string; icon: React.ElementType }[] = [
  { id: "hierarchy", label: "Team Hierarchy", icon: Network },
  { id: "permissions", label: "Permissions", icon: Users },
  { id: "orchestration", label: "Orchestration", icon: GitBranch },
];

export default function SystemVisualizationPage() {
  const {
    viewMode,
    setViewMode,
    selectedTeam,
    setSelectedTeam,
    selectedFlow,
    setSelectedFlow,
    systemData,
    setSystemData,
    permissionData,
    setPermissionData,
    orchestrationData,
    setOrchestrationData,
    loading,
    setLoading,
    error,
    setError,
    highlightedPath,
    setHighlightedPath,
  } = useSystemVizStore();

  const [traceMode, setTraceMode] = useState(false);
  const [traceStart, setTraceStart] = useState<string | null>(null);
  const [traceEnd, setTraceEnd] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [sysRes, permRes, orchRes] = await Promise.all([
        fetch("/api/system/hierarchy"),
        fetch("/api/system/permissions"),
        fetch("/api/system/orchestration"),
      ]);

      if (!sysRes.ok || !permRes.ok || !orchRes.ok) {
        throw new Error("Failed to load system data");
      }

      const [sysData, permData, orchData] = await Promise.all([
        sysRes.json(),
        permRes.json(),
        orchRes.json(),
      ]);

      setSystemData(sysData);
      setPermissionData(permData);
      setOrchestrationData(orchData);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [setSystemData, setPermissionData, setOrchestrationData, setLoading, setError]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const teams: TeamInfo[] = useMemo(() => systemData?.teams || [], [systemData]);
  const hierarchy = useMemo(() => systemData?.hierarchy || [], [systemData]);
  const states: WorkflowState[] = useMemo(() => orchestrationData?.states || [], [orchestrationData]);
  const flows: OrchestrationFlow[] = useMemo(() => orchestrationData?.flows || [], [orchestrationData]);

  const findPath = useCallback((start: string, end: string): string[] => {
    const adjacency: Record<string, string[]> = {};
    
    flows.forEach(flow => {
      flow.edges.forEach(edge => {
        if (!adjacency[edge.source]) adjacency[edge.source] = [];
        adjacency[edge.source].push(edge.target);
      });
    });

    const visited = new Set<string>();
    const path: string[] = [];

    function dfs(node: string): boolean {
      if (node === end) {
        path.push(node);
        return true;
      }
      visited.add(node);
      path.push(node);

      const neighbors = adjacency[node] || [];
      for (const neighbor of neighbors) {
        if (!visited.has(neighbor) && dfs(neighbor)) {
          return true;
        }
      }

      path.pop();
      return false;
    }

    dfs(start);
    return path;
  }, [flows]);

  const handleTracePath = useCallback((from: string, to: string) => {
    const path = findPath(from, to);
    setHighlightedPath(path);
  }, [findPath, setHighlightedPath]);

  const clearTrace = useCallback(() => {
    setTraceMode(false);
    setTraceStart(null);
    setTraceEnd(null);
    setHighlightedPath(null);
  }, [setHighlightedPath]);

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-950">
        <div className="text-center">
          <RefreshCw className="w-8 h-8 animate-spin text-blue-500 mx-auto mb-4" />
          <p className="text-gray-400">Loading system visualization...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-950">
        <div className="text-center">
          <p className="text-red-400 mb-4">{error}</p>
          <button
            onClick={fetchData}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-gray-950">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold text-white">System Visualization</h1>
          <div className="flex items-center bg-gray-900 rounded-lg p-1">
            {VIEW_MODES.map(mode => (
              <button
                key={mode.id}
                onClick={() => {
                  setViewMode(mode.id);
                  setSelectedTeam(null);
                  setSelectedFlow(null);
                  clearTrace();
                }}
                className={clsx(
                  "flex items-center gap-2 px-3 py-1.5 rounded text-sm transition-colors",
                  viewMode === mode.id
                    ? "bg-blue-600 text-white"
                    : "text-gray-400 hover:text-white"
                )}
              >
                <mode.icon size={14} />
                {mode.label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {viewMode === "orchestration" && (
            <button
              onClick={() => setTraceMode(!traceMode)}
              className={clsx(
                "px-3 py-1.5 text-sm rounded flex items-center gap-2",
                traceMode ? "bg-amber-600 text-white" : "bg-gray-800 text-gray-300 hover:bg-gray-700"
              )}
            >
              <Search size={14} />
              Trace Path
            </button>
          )}
          <button
            onClick={fetchData}
            className="p-2 text-gray-400 hover:text-white bg-gray-800 rounded hover:bg-gray-700"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {traceMode && viewMode === "orchestration" && (
        <div className="flex items-center gap-4 px-4 py-2 bg-amber-900/20 border-b border-amber-800">
          <span className="text-sm text-amber-400">Path Trace Mode:</span>
          <select
            value={traceStart || ""}
            onChange={(e) => setTraceStart(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
          >
            <option value="">Select start node...</option>
            {flows.find(f => f.id === selectedFlow)?.nodes.map(n => (
              <option key={n.id} value={n.id}>{n.label}</option>
            ))}
          </select>
          <ArrowRight size={16} className="text-gray-500" />
          <select
            value={traceEnd || ""}
            onChange={(e) => setTraceEnd(e.target.value)}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-white"
          >
            <option value="">Select end node...</option>
            {flows.find(f => f.id === selectedFlow)?.nodes.map(n => (
              <option key={n.id} value={n.id}>{n.label}</option>
            ))}
          </select>
          <button
            onClick={() => traceStart && traceEnd && handleTracePath(traceStart, traceEnd)}
            disabled={!traceStart || !traceEnd}
            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 text-white text-sm rounded"
          >
            Find Path
          </button>
          {highlightedPath && (
            <button
              onClick={clearTrace}
              className="px-3 py-1 bg-gray-700 hover:bg-gray-600 text-white text-sm rounded"
            >
              Clear
            </button>
          )}
        </div>
      )}

      <div className="flex-1 flex overflow-hidden">
        <div className="flex-1">
          {viewMode === "hierarchy" && (
            <HierarchyViz
              hierarchy={hierarchy}
              onNodeClick={(teamId) => setSelectedTeam(teamId === selectedTeam ? null : teamId)}
              selectedTeam={selectedTeam}
              highlightedPath={highlightedPath}
            />
          )}
          {viewMode === "permissions" && permissionData && (
            <PermissionsViz
              permissions={permissionData}
              teams={teams}
              selectedTeam={selectedTeam}
              onTeamSelect={(teamId) => setSelectedTeam(teamId === selectedTeam ? null : teamId)}
              onTracePath={(from, to) => handleTracePath(from, to)}
            />
          )}
          {viewMode === "orchestration" && (
            <OrchestrationViz
              flows={flows}
              states={states}
              selectedFlowId={selectedFlow}
              onFlowSelect={(flowId) => {
                setSelectedFlow(flowId);
                clearTrace();
              }}
              highlightedPath={highlightedPath}
              onTracePath={(nodeId) => {
                if (traceMode && traceStart && traceEnd) {
                  handleTracePath(traceStart, nodeId);
                }
              }}
            />
          )}
        </div>

        {(selectedTeam || selectedFlow) && (
          <div className="w-80 border-l border-gray-800 bg-gray-900 overflow-auto">
            {selectedTeam && viewMode === "hierarchy" && teams.find(t => t.teamId === selectedTeam) && (
              <div className="p-4">
                <h3 className="text-sm font-medium text-white mb-3">Team Details</h3>
                {(() => {
                  const team = teams.find(t => t.teamId === selectedTeam)!;
                  return (
                    <div className="space-y-4">
                      <div>
                        <div className="text-xs text-gray-500 uppercase">Team</div>
                        <div className="text-white font-medium">{team.displayName}</div>
                        <div className="text-xs text-gray-400">{team.teamId}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500 uppercase">Tier</div>
                        <div className="text-white">{team.tier}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500 uppercase">Admin Agent</div>
                        <div className="text-white">{team.admin.displayName}</div>
                        <div className="text-xs text-gray-400">{team.admin.agentId}</div>
                      </div>
                      {team.workers.length > 0 && (
                        <div>
                          <div className="text-xs text-gray-500 uppercase mb-2">Workers</div>
                          {team.workers.map(w => (
                            <div key={w.agentId} className="mb-2 p-2 bg-gray-800 rounded">
                              <div className="text-white text-sm">{w.displayName}</div>
                              <div className="text-xs text-gray-400">{w.agentId}</div>
                            </div>
                          ))}
                        </div>
                      )}
                      <div>
                        <div className="text-xs text-gray-500 uppercase mb-2">Allowed Tools</div>
                        <div className="flex flex-wrap gap-1">
                          {team.admin.tools.slice(0, 10).map(tool => (
                            <span key={tool} className="px-2 py-0.5 bg-blue-900/30 text-blue-400 text-xs rounded">
                              {tool}
                            </span>
                          ))}
                          {team.admin.tools.length > 10 && (
                            <span className="text-xs text-gray-500">+{team.admin.tools.length - 10} more</span>
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}

            {selectedTeam && viewMode === "permissions" && permissionData && teams.find(t => t.teamId === selectedTeam) && (
              <div className="p-4">
                <h3 className="text-sm font-medium text-white mb-3">Permissions for {teams.find(t => t.teamId === selectedTeam)?.displayName}</h3>
                <div className="space-y-4">
                  <div>
                    <div className="text-xs text-gray-500 uppercase mb-2">Team Tier</div>
                    <div className="text-white">{permissionData.teamTiers[selectedTeam]}</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-500 uppercase mb-2">Allowed From</div>
                    <div className="space-y-1">
                      {Object.entries(permissionData.communicationMatrix).map(([role, targets]) => {
                        const allowed = targets[selectedTeam]?.allowed;
                        if (!allowed) return null;
                        return (
                          <div key={role} className="flex items-center gap-2 text-sm">
                            <ChevronRight size={12} className="text-green-400" />
                            <span className="text-gray-300 capitalize">{role}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {selectedFlow && viewMode === "orchestration" && flows.find(f => f.id === selectedFlow) && (
              <div className="p-4">
                <h3 className="text-sm font-medium text-white mb-3">Flow Details</h3>
                {(() => {
                  const flow = flows.find(f => f.id === selectedFlow)!;
                  return (
                    <div className="space-y-4">
                      <div>
                        <div className="text-xs text-gray-500 uppercase">Name</div>
                        <div className="text-white font-medium">{flow.name}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500 uppercase">Description</div>
                        <div className="text-gray-300 text-sm">{flow.description}</div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500 uppercase mb-2">Nodes ({flow.nodes.length})</div>
                        <div className="max-h-40 overflow-auto space-y-1">
                          {flow.nodes.map(n => (
                            <div key={n.id} className="text-xs text-gray-400 flex items-center gap-2">
                              <span className="font-mono">{n.id}</span>
                              <span>{n.label}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-gray-500 uppercase mb-2">Edges ({flow.edges.length})</div>
                        <div className="max-h-40 overflow-auto space-y-1">
                          {flow.edges.map((e, i) => (
                            <div key={i} className="text-xs text-gray-500 flex items-center gap-1">
                              <span className="font-mono">{e.source}</span>
                              <ChevronRight size={10} />
                              <span className="font-mono">{e.target}</span>
                              {e.condition && <span className="text-amber-400">({e.condition})</span>}
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
