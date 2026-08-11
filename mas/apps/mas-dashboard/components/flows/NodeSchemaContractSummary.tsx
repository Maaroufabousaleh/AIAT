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
  required?: boolean;
  enum?: readonly string[];
  default?: unknown;
  deprecated?: boolean;
};

type SchemaContract = FlowNodeSchema & {
  fields: readonly SchemaField[];
  required_any?: readonly string[];
};

function formatDefault(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/**
 * Shows the generated node contract beside the generated editor.
 *
 * Deprecated compatibility fields remain visible in the contract metadata,
 * while the primary form renders only canonical fields. The legacy editor
 * stays available below for definitions that have not migrated yet.
 */
export function NodeSchemaContractSummary({ nodeType }: { nodeType: FlowNodeType }) {
  const schema = FLOW_NODE_SCHEMA_CATALOG.node_types[nodeType] as SchemaContract;
  const requiredAny = schema.required_any || [];

  return (
    <section
      className="rounded-md border border-slate-700/80 bg-slate-950/45 p-2.5"
      aria-label={`${schema.label} node schema contract`}
      data-testid="node-schema-contract"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-xxs font-semibold uppercase tracking-wider text-slate-500">
          Schema contract
        </div>
        <span className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono text-[10px] text-slate-400">
          v{FLOW_NODE_SCHEMA_CATALOG.schema_version}
        </span>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-slate-400">{schema.description}</p>

      {requiredAny.length > 0 && (
        <p className="mt-2 text-[11px] leading-relaxed text-amber-200/80">
          Provide at least one of: {requiredAny.join(", ")}.
        </p>
      )}

      {schema.fields.length > 0 ? (
        <ul className="mt-2 space-y-1.5" aria-label="Schema fields">
          {schema.fields.map((field) => {
            const defaultValue = formatDefault(field.default);
            return (
              <li key={field.name} className="flex items-start justify-between gap-2 text-[11px]">
                <span className="min-w-0 text-slate-300">
                  <span className="font-medium">{field.label}</span>
                  <span className="ml-1 font-mono text-slate-500">{field.name}</span>
                  {field.deprecated && <span className="ml-1 text-amber-300">(compatibility)</span>}
                  {field.required && <span className="ml-1 text-rose-300" aria-label="required">*</span>}
                </span>
                <span className="shrink-0 text-right text-slate-500">
                  {field.type}
                  {field.enum && field.enum.length > 0 ? ` · ${field.enum.join("/")}` : ""}
                  {defaultValue ? ` · default ${defaultValue}` : ""}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="mt-2 text-[11px] text-slate-500">This node has no configuration fields.</p>
      )}
    </section>
  );
}
