import { create } from "zustand";
import type { Flow, FlowDefinition, FlowInstance, FlowNodeExecution, FlowTemplate } from "./flow-types";

interface FlowState {
  flows: Flow[];
  currentFlow: Flow | null;
  currentInstance: FlowInstance | null;
  nodeExecutions: FlowNodeExecution[];
  activeInstances: FlowInstance[];
  loading: boolean;
  error: string | null;
  
  setFlows: (flows: Flow[]) => void;
  setCurrentFlow: (flow: Flow | null) => void;
  setCurrentInstance: (instance: FlowInstance | null) => void;
  setNodeExecutions: (executions: FlowNodeExecution[]) => void;
  setActiveInstances: (instances: FlowInstance[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  
  fetchFlows: () => Promise<void>;
  fetchFlowTemplates: () => Promise<FlowTemplate[]>;
  fetchFlow: (id: string) => Promise<Flow | null>;
  createFlow: (data: { name: string; description?: string; definition_json: FlowDefinition; is_active?: boolean; version_from_flow_id?: string }) => Promise<Flow | null>;
  updateFlow: (id: string, data: Partial<Flow>) => Promise<Flow | null>;
  deleteFlow: (id: string) => Promise<boolean>;
  migrateLegacyTasks: (id: string, data: {
    worker_bindings: Record<string, string>;
    model_profile_bindings?: Record<string, string>;
    actor_id?: string;
    dry_run?: boolean;
    is_active?: boolean;
    name?: string;
    description?: string;
  }) => Promise<Record<string, unknown> | null>;
  
  fetchFlowInstance: (id: string) => Promise<FlowInstance | null>;
  fetchProjectFlowInstance: (projectId: string) => Promise<FlowInstance | null>;
  createFlowInstance: (flowId: string, projectId: string) => Promise<FlowInstance | null>;
  executeFlowAction: (instanceId: string, action: string) => Promise<FlowInstance | null>;
  executeNodeAction: (instanceId: string, nodeId: string, action: string, data?: { output?: Record<string, unknown>; error?: string; approved?: boolean }) => Promise<FlowInstance | null>;
  fetchNodeExecutions: (instanceId: string) => Promise<FlowNodeExecution[]>;
  switchFlowInstance: (instanceId: string, newFlowId: string, preserveContext?: boolean) => Promise<FlowInstance | null>;
  migrateFlowInstance: (instanceId: string, newFlowId: string, preserveContext?: boolean, actorId?: string) => Promise<FlowInstance | null>;
  updateInstanceContext: (instanceId: string, context: Record<string, unknown>) => Promise<FlowInstance | null>;
  escalateFlowInstance: (instanceId: string, escalateTo: string, reason?: string) => Promise<FlowInstance | null>;
  retryFlowInstance: (instanceId: string) => Promise<FlowInstance | null>;
  fetchActiveInstances: () => Promise<FlowInstance[]>;
}

export const useFlowStore = create<FlowState>((set, get) => ({
  flows: [],
  currentFlow: null,
  currentInstance: null,
  nodeExecutions: [],
  activeInstances: [],
  loading: false,
  error: null,

  setFlows: (flows) => set({ flows }),
  setCurrentFlow: (flow) => set({ currentFlow: flow }),
  setCurrentInstance: (instance) => set({ currentInstance: instance }),
  setNodeExecutions: (executions) => set({ nodeExecutions: executions }),
  setActiveInstances: (instances) => set({ activeInstances: instances }),
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

  fetchFlowTemplates: async () => {
    try {
      const res = await fetch("/api/flow-templates");
      if (!res.ok) throw new Error("Failed to fetch flow templates");
      const data = await res.json();
      return Array.isArray(data?.templates) ? data.templates : [];
    } catch (e) {
      set({ error: (e as Error).message });
      return [];
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

  migrateLegacyTasks: async (id, data) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/${id}/migrate-legacy-tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const payload = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          payload.error || payload.detail?.message || payload.detail || "Failed to migrate legacy tasks",
        );
      }
      const nextFlow = payload.flow;
      if (nextFlow && typeof nextFlow.id === "string") {
        set((state) => ({
          flows: [nextFlow, ...state.flows.filter((flow) => flow.id !== nextFlow.id)],
          currentFlow: nextFlow,
          loading: false,
        }));
      } else {
        set({ loading: false });
      }
      return payload as Record<string, unknown>;
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Failed to migrate legacy tasks", loading: false });
      return null;
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

  switchFlowInstance: async (instanceId, newFlowId, preserveContext = true) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/switch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flow_id: newFlowId, preserve_context: preserveContext }),
      });
      if (!res.ok) throw new Error("Failed to switch flow");
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  migrateFlowInstance: async (instanceId, newFlowId, preserveContext = true, actorId = "human_operator") => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/migrate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ flow_id: newFlowId, preserve_context: preserveContext, actor_id: actorId }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.error || detail.detail?.message || detail.detail || "Failed to migrate flow instance");
      }
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  updateInstanceContext: async (instanceId, context) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/context`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ context }),
      });
      if (!res.ok) throw new Error("Failed to update context");
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  escalateFlowInstance: async (instanceId, escalateTo, reason) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/escalate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ escalate_to: escalateTo, reason }),
      });
      if (!res.ok) throw new Error("Failed to escalate");
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  retryFlowInstance: async (instanceId) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch(`/api/flows/instances/${instanceId}/retry`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to retry");
      const data = await res.json();
      set({ currentInstance: data, loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return null;
    }
  },

  fetchActiveInstances: async () => {
    set({ loading: true, error: null });
    try {
      const res = await fetch("/api/flows/instances/active");
      if (!res.ok) throw new Error("Failed to fetch active instances");
      const data = await res.json();
      set({ activeInstances: Array.isArray(data) ? data : [], loading: false });
      return data;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      return [];
    }
  },
}));
