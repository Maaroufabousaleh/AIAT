import { useEffect, useMemo, useState } from "react";

import {
  FLOW_NODE_SCHEMA_CATALOG,
  type FlowNodeSchema,
} from "@/lib/generated/flow-node-schemas";
import { type FlowNodeType } from "@/lib/flow-types";

type SchemaField = {
  name: string;
  label: string;
  type: string;
  widget?: string;
  description?: string;
  placeholder?: string;
  required?: boolean;
  enum?: readonly string[];
  default?: unknown;
  deprecated?: boolean;
  minimum?: number;
  min_items?: number;
  min_properties?: number;
  items?: string;
};

type SchemaContract = FlowNodeSchema & {
  fields: readonly SchemaField[];
  required_any?: readonly string[];
};

export type GovernedWorkerOption = {
  id: string;
  name?: string;
  status?: string;
  adapter_type?: string;
  model_mode?: string;
  model_profile_id?: string | null;
};

export type GovernedModelProfile = {
  profile_id: string;
  status?: string;
  purpose?: string;
};

type NodeSchemaFormProps = {
  nodeType: FlowNodeType;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  governedWorkers?: GovernedWorkerOption[];
  modelProfiles?: GovernedModelProfile[];
  governanceLoading?: boolean;
};

function stringifyJson(value: unknown, fallback: unknown): string {
  const source = value === undefined ? fallback : value;
  if (source === undefined) return "";
  try {
    return JSON.stringify(source, null, 2);
  } catch {
    return String(source);
  }
}

function csvValue(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join(", ") : "";
}

function parseCsv(value: string): string[] {
  return value.split(",").map((entry) => entry.trim()).filter(Boolean);
}

function inputClass(): string {
  return "w-full bg-slate-900/70 border border-slate-700 hover:border-slate-600 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/30 rounded-md px-2.5 py-1.5 text-sm text-white placeholder-slate-500 transition-colors";
}

function fieldTestId(nodeType: FlowNodeType, name: string): string {
  const legacyIds: Record<string, string> = {
    worker_id: "task-worker-select",
    model_mode: "task-model-mode-select",
    model_profile_id: "task-model-profile-select",
    task_type: "task-type-input",
    required_capabilities: "task-capabilities-input",
    project_workspace_mode: "task-workspace-mode-select",
    permission_requirements: "task-permissions-input",
    tool_grants: "task-tool-grants-input",
    timeout_seconds: "task-timeout-input",
  };
  return legacyIds[name] || `node-schema-${nodeType}-${name.replaceAll("_", "-")}`;
}

function JsonField({
  field,
  value,
  onChange,
}: {
  field: SchemaField;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const serialized = useMemo(
    () => stringifyJson(value, field.default ?? (field.type === "array" ? [] : {})),
    [field.default, field.type, value],
  );
  const [draft, setDraft] = useState(serialized);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDraft(serialized);
  }, [serialized]);

  return (
    <>
      <textarea
        value={draft}
        onChange={(event) => {
          const nextDraft = event.target.value;
          setDraft(nextDraft);
          try {
            const parsed = JSON.parse(nextDraft);
            onChange(parsed);
            setError(null);
          } catch {
            setError("Enter valid JSON before leaving this field");
          }
        }}
        placeholder={field.placeholder}
        data-testid={`node-schema-${field.name}-json`}
        aria-label={field.label}
        className={`${inputClass()} min-h-20 font-mono text-xs`}
      />
      {error && <p className="mt-1 text-[11px] text-rose-300" role="alert">{error}</p>}
    </>
  );
}

/**
 * Render the canonical generated node schema as an editable form.
 *
 * The catalogue remains the source of truth for field names, types, defaults,
 * enums, and minimums. Unknown adapter extension keys are deliberately left
 * in the config object and are not discarded by this editor.
 */
export function NodeSchemaForm({
  nodeType,
  value,
  onChange,
  governedWorkers = [],
  modelProfiles = [],
  governanceLoading = false,
}: NodeSchemaFormProps) {
  const schema = FLOW_NODE_SCHEMA_CATALOG.node_types[nodeType] as SchemaContract;
  const requiredAny = schema.required_any || [];

  const updateField = (field: SchemaField, fieldValue: unknown) => {
    const next: Record<string, unknown> = { ...value, [field.name]: fieldValue };
    if (field.name === "worker_id") {
      next.team_id = undefined;
      const selected = governedWorkers.find((worker) => worker.id === fieldValue);
      if (selected?.model_mode) next.model_mode = selected.model_mode;
      if (selected?.model_profile_id) next.model_profile_id = selected.model_profile_id;
    }
    if (field.name === "model_mode" && fieldValue === "none") {
      next.model_profile_id = undefined;
    }
    onChange(next);
  };

  return (
    <section
      className="rounded-md border border-blue-900/70 bg-blue-950/15 p-2.5 space-y-3"
      aria-label={`${schema.label} generated configuration form`}
      data-testid="node-schema-form"
    >
      <div>
        <div className="flex items-center justify-between gap-2">
          <div className="text-xxs font-semibold uppercase tracking-wider text-blue-200/80">Generated schema editor</div>
          <span className="rounded border border-blue-900/80 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
            v{FLOW_NODE_SCHEMA_CATALOG.schema_version}
          </span>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-slate-400">{schema.description}</p>
        {requiredAny.length > 0 && (
          <p className="mt-1 text-[11px] leading-relaxed text-amber-200/80">
            At least one of: {requiredAny.join(", ")}.
          </p>
        )}
      </div>

      {schema.fields.length === 0 ? (
        <p className="text-[11px] text-slate-500">This node has no generated configuration fields.</p>
      ) : (
        <div className="space-y-3">
          {schema.fields.filter((field) => !field.deprecated).map((field) => {
            const fieldValue = value[field.name];
            const modelEnabled = String(value.model_mode ?? field.default ?? "aiat_gateway") !== "none";
            const disabled = field.widget === "model-profile-select" && (!modelEnabled || governanceLoading);

            return (
              <div key={field.name}>
                <label className="block text-xs text-slate-300 mb-1" htmlFor={fieldTestId(nodeType, field.name)}>
                  {field.label}{field.required && <span className="ml-1 text-rose-300" aria-label="required">*</span>}
                </label>
                {field.description && <p className="mb-1 text-[11px] leading-relaxed text-slate-500">{field.description}</p>}

                {field.widget === "worker-select" ? (
                  <select
                    id={fieldTestId(nodeType, field.name)}
                    value={String(fieldValue ?? "")}
                    onChange={(event) => updateField(field, event.target.value || undefined)}
                    disabled={governanceLoading}
                    data-testid={fieldTestId(nodeType, field.name)}
                    className={inputClass()}
                  >
                    <option value="">{governanceLoading ? "Loading workers…" : "Select a governed worker"}</option>
                    {governedWorkers.map((worker) => (
                      <option key={worker.id} value={worker.id}>
                        {worker.name || worker.id} · {worker.status || "UNKNOWN"} · {worker.adapter_type || "runtime"}
                      </option>
                    ))}
                  </select>
                ) : field.widget === "model-profile-select" ? (
                  <select
                    id={fieldTestId(nodeType, field.name)}
                    value={String(fieldValue ?? "")}
                    onChange={(event) => updateField(field, event.target.value || undefined)}
                    disabled={disabled}
                    data-testid={fieldTestId(nodeType, field.name)}
                    className={inputClass()}
                  >
                    <option value="">{!modelEnabled ? "Not used in no-model mode" : "Select approved Model Profile"}</option>
                    {modelProfiles.filter((profile) => profile.status === "approved").map((profile) => (
                      <option key={profile.profile_id} value={profile.profile_id}>
                        {profile.profile_id}{profile.purpose ? ` · ${profile.purpose}` : ""}
                      </option>
                    ))}
                  </select>
                ) : field.widget === "select" ? (
                  <select
                    id={fieldTestId(nodeType, field.name)}
                    value={String(fieldValue ?? field.default ?? "")}
                    onChange={(event) => updateField(field, event.target.value)}
                    data-testid={fieldTestId(nodeType, field.name)}
                    className={inputClass()}
                  >
                    {field.enum?.map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                ) : field.widget === "csv" ? (
                  <input
                    id={fieldTestId(nodeType, field.name)}
                    value={csvValue(fieldValue ?? field.default)}
                    onChange={(event) => updateField(field, parseCsv(event.target.value))}
                    placeholder={field.placeholder || "item_one, item_two"}
                    data-testid={fieldTestId(nodeType, field.name)}
                    className={inputClass()}
                  />
                ) : field.widget === "json" ? (
                  <JsonField field={field} value={fieldValue} onChange={(next) => updateField(field, next)} />
                ) : field.type === "boolean" ? (
                  <label className="inline-flex items-center gap-2 text-xs text-slate-300">
                    <input
                      id={fieldTestId(nodeType, field.name)}
                      type="checkbox"
                      checked={Boolean(fieldValue ?? field.default)}
                      onChange={(event) => updateField(field, event.target.checked)}
                      data-testid={fieldTestId(nodeType, field.name)}
                    />
                    Enabled
                  </label>
                ) : (
                  <input
                    id={fieldTestId(nodeType, field.name)}
                    type={field.type === "integer" || field.type === "number" ? "number" : "text"}
                    min={field.minimum}
                    step={field.type === "number" ? "any" : undefined}
                    value={fieldValue === undefined || fieldValue === null ? "" : String(fieldValue)}
                    onChange={(event) => {
                      if (field.type === "integer") {
                        const parsed = event.target.value === "" ? undefined : Number.parseInt(event.target.value, 10);
                        updateField(field, Number.isFinite(parsed) ? parsed : undefined);
                      } else if (field.type === "number") {
                        const parsed = event.target.value === "" ? undefined : Number(event.target.value);
                        updateField(field, Number.isFinite(parsed) ? parsed : undefined);
                      } else {
                        updateField(field, event.target.value);
                      }
                    }}
                    placeholder={field.placeholder}
                    data-testid={fieldTestId(nodeType, field.name)}
                    className={inputClass()}
                  />
                )}
              </div>
            );
          })}
        </div>
      )}

      {schema.fields.some((field) => field.deprecated) && (
        <p className="text-[11px] leading-relaxed text-slate-500">
          Deprecated compatibility fields remain available in the collapsed compatibility editor below.
        </p>
      )}

      {isAdditionalPropertiesEnabled(schema) && (
        <p className="text-[11px] leading-relaxed text-slate-500">
          Adapter-specific extension keys are preserved when saved; this editor only normalizes the generated common fields.
        </p>
      )}
    </section>
  );
}

function isAdditionalPropertiesEnabled(_schema: SchemaContract): boolean {
  return Boolean(FLOW_NODE_SCHEMA_CATALOG.additional_properties);
}
