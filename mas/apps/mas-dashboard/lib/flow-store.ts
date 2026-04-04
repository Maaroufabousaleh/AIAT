import { create } from "zustand";
import type { Flow, FlowDefinition, FlowInstance, FlowNodeExecution } from "./flow-types";

interface FlowState {
  flows: Flow[];
  currentFlow: Flow | null;
  currentInstance: FlowInstance | null;
  nodeExecutions: FlowNodeExecution[];
  loading: boolean;
  error: string | null;
  
  setFlows: (flows: Flow[]) => void;
  setCurrentFlow: (flow: Flow | null) => void;
  setCurrentInstance: (instance: FlowInstance | null) => void;
  setNodeExecutions: (executions: FlowNodeExecution[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  
  fetchFlows: () => Promise<void>;
  fetchFlow: (id: string) => Promise<Flow | null>;
  createFlow: (data: { name: string; description?: string; definition_json: FlowDefinition; is_active?: boolean }) => Promise<Flow | null>;
  updateFlow: (id: string, data: Partial<Flow>) => Promise<Flow | null>;
  deleteFlow: (id: string) => Promise<boolean>;
  
  fetchFlowInstance: (id: string) => Promise<FlowInstance | null>;
  fetchProjectFlowInstance: (projectId: string) => Promise<FlowInstance | null>;
  createFlowInstance: (flowId: string, projectId: string) => Promise<FlowInstance | null>;
  executeFlowAction: (instanceId: string, action: string) => Promise<FlowInstance | null>;
  executeNodeAction: (instanceId: string, nodeId: string, action: string, data?: { output?: Record<string, unknown>; error?: string; approved?: boolean }) => Promise<FlowInstance | null>;
  fetchNodeExecutions: (instanceId: string) => Promise<FlowNodeExecution[]>;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  flows: [],
  currentFlow: null,
  currentInstance: null,
  nodeExecutions: [],
  loading: false,
  error: null,

  setFlows: (flows) => set({ flows }),
  setCurrentFlow: (flow) => set({ currentFlow: flow }),
  setCurrentInstance: (instance) => set({ currentInstance: instance }),
  setNodeExecutions: (executions) => set({ nodeExecutions: executions }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),

  fetchFlows: async () => {
    set({ loading: true, error: null });
    try {
      const res = await fetch("/api/flows");
      if (!res.ok) throw new Error("Failed to fetch flows");
      const data = await res.json();
      set({ flows: Array.isArray(data) ? data : [], loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  fetchFlow: async (id) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/${id}`);
      if (!res.ok) throw new Error("Failed to fetch flow");
      const data = await res.json();
      set({ currentFlow: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  createFlow: async (data) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch("/api/flows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to create flow");
      }
      const created = await res.json();
      set((state) => ({ flows: [created, ...state.flows], loading: false }));
      return created;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  updateFlow: async (id, data) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error("Failed to update flow");
      const updated = await res.json();
      set((state) => ({
        flows: state.flows.map((f) => (f.id === id ? updated : f)),
        currentFlow: state.currentFlow?.id === id ? updated : state.currentFlow,
        loading: false,
      }));
      return updated;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  deleteFlow: async (id) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Failed to delete flow");
      set((state) => ({
        flows: state.flows.filter((f) => f.id !== id),
        currentFlow: state.currentFlow?.id === id ? null : state.currentFlow,
        loading: false,
      }));
      return true;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return false;
    }
  },

  fetchFlowInstance: async (id) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${id}`);
      if (!res.ok) throw new Error("Failed to fetch flow instance");
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  fetchProjectFlowInstance: async (projectId) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/projects/${projectId}/flow-instance`);
      if (!res.ok) throw new Error("No flow instance for project");
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  createFlowInstance: async (flowId, projectId) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch("/api/flows/instances", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flow_id: flowId, project_id: projectId }),
      });
      if (!res.ok) {
        const d = await res.json();
        throw new Error(d.error || "Failed to create flow instance");
      }
      const created = await res.json();
      set({ currentInstance: created, loading: false });
      return created;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  executeFlowAction: async (instanceId, action) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) throw new Error(`Failed to ${action} flow`);
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  executeNodeAction: async (instanceId, nodeId, action, data) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/node-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ node_id: nodeId, action, ...data }),
      });
      if (!res.ok) throw new Error(`Failed to ${action} node`);
      const updated = await res.json();
      set({ currentInstance: updated, loading: false });
      
      if (updated.status === "RUNNING" || updated.status === "WAITING_APPROVAL") {
        await get().fetchNodeExecutions(instanceId);
      }
      
      return updated;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  fetchNodeExecutions: async (instanceId) => {
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/executions`);
      if (!res.ok) throw new Error("Failed to fetch executions");
      const data = await res.json();
      set({ nodeExecutions: Array.isArray(data) ? data : [] });
      return data;
    } catch (e) {
      set({ error: (e as Error).message });
      return [];
    }
  },
}));
