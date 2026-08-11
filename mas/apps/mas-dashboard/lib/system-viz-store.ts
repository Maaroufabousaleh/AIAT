"use client";

import { create } from "zustand";
import type { SystemData, PermissionData, OrchestrationData, ViewMode } from "@/lib/system-viz-types";

interface SystemVizStore {
  viewMode: ViewMode;
  setViewMode: (mode: ViewMode) => void;
  selectedTeam: string | null;
  setSelectedTeam: (teamId: string | null) => void;
  selectedFlow: string | null;
  setSelectedFlow: (flowId: string | null) => void;
  systemData: SystemData | null;
  setSystemData: (data: SystemData) => void;
  permissionData: PermissionData | null;
  setPermissionData: (data: PermissionData) => void;
  orchestrationData: OrchestrationData | null;
  setOrchestrationData: (data: OrchestrationData) => void;
  loading: boolean;
  setLoading: (loading: boolean) => void;
  error: string | null;
  setError: (error: string | null) => void;
  partialErrors: string[];
  setPartialErrors: (errors: string[]) => void;
  highlightedPath: string[] | null;
  setHighlightedPath: (path: string[] | null) => void;
}

export const useSystemVizStore = create<SystemVizStore>((set) => ({
  viewMode: "hierarchy",
  setViewMode: (mode) => set({ viewMode: mode }),
  selectedTeam: null,
  setSelectedTeam: (teamId) => set({ selectedTeam: teamId }),
  selectedFlow: null,
  setSelectedFlow: (flowId) => set({ selectedFlow: flowId }),
  systemData: null,
  setSystemData: (data) => set({ systemData: data }),
  permissionData: null,
  setPermissionData: (data) => set({ permissionData: data }),
  orchestrationData: null,
  setOrchestrationData: (data) => set({ orchestrationData: data }),
  loading: true,
  setLoading: (loading) => set({ loading }),
  error: null,
  setError: (error) => set({ error }),
  partialErrors: [],
  setPartialErrors: (errors) => set({ partialErrors: errors }),
  highlightedPath: null,
  setHighlightedPath: (path) => set({ highlightedPath: path }),
}));
