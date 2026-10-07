"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Building2, Check, ChevronRight, Plus, RefreshCw, RotateCcw, ShieldCheck } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorBanner } from "@/components/ui/ErrorBanner";
import { PageHeader } from "@/components/ui/PageHeader";

type Company = {
  id: string;
  slug: string;
  name: string;
  description?: string | null;
  status?: string;
  created_at?: string;
  updated_at?: string;
};

type Row = Record<string, unknown>;
type CompanyReadModel = {
  company?: Company | null;
  manifest?: Row | null;
  departments?: Row[];
  assignments?: Row[];
  budgets?: Row[];
};

type ManifestVersion = {
  id?: string;
  manifest_version: number;
  digest?: string;
  status?: string;
  source?: string;
  compiled_by?: string;
  compiled_at?: string;
  created_at?: string;
  manifest_json?: Record<string, unknown> | string;
};

type RegisteredWorker = {
  worker_id?: string;
  name?: string;
  display_name?: string;
  worker_name?: string;
  status?: string;
};

type HttpError = Error & { status: number };

const fieldClass = "w-full min-h-10 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/20";
const buttonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm font-medium text-slate-200 transition hover:border-slate-500 hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50";
const primaryButtonClass = "inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-blue-500/50 bg-blue-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50";
const enforcedCompanyBudgetKeys = ["max_cost_usd", "max_concurrent_runs"] as const;
const ceoOnlyPermissionGrants = new Set(["company.manage", "company.delete", "approval.decide"]);

function asRecord(value: unknown): Row {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
}

function display(value: unknown, fallback = "—"): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return fallback;
}

function dateTime(value: unknown): string {
  if (typeof value !== "string") return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function rowName(row: Row, candidates: string[]): string {
  for (const candidate of candidates) {
    if (typeof row[candidate] === "string" && row[candidate]) return row[candidate] as string;
  }
  return "—";
}

function records(value: unknown): Row[] {
  return Array.isArray(value) ? value.map(asRecord) : [];
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function workerId(worker: RegisteredWorker): string {
  return worker.worker_id ?? worker.name ?? "";
}

function workerLabel(worker: RegisteredWorker): string {
  const id = workerId(worker);
  const name = worker.display_name ?? worker.worker_name ?? worker.name;
  return name && name !== id ? `${name} (${id})` : id;
}

function parseErrorPayload(payload: unknown, fallback: string): string {
  const body = asRecord(payload);
  const detail = body.detail ?? body.error;
  if (typeof detail === "string") {
    try {
      const nested = JSON.parse(detail) as unknown;
      const nestedRecord = asRecord(nested);
      if (typeof nestedRecord.detail === "string") return nestedRecord.detail;
    } catch {
      // The API's plain-text validation/conflict message is already useful.
    }
    return detail;
  }
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return fallback;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init?.headers ?? {}),
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(parseErrorPayload(payload, `Request failed (HTTP ${response.status}).`)) as HttpError;
    error.status = response.status;
    throw error;
  }
  return payload as T;
}

function starterManifest(company: Company): string {
  return JSON.stringify({
    schema_version: "1",
    slug: company.slug,
    name: company.name,
    description: company.description ?? "",
    ceo_worker_id: "ceo",
    timezone: "UTC",
    departments: [{ id: "exec_ceo", name: "CEO Office", chief_worker_id: "ceo", worker_ids: ["ceo"] }],
    worker_assignments: [{ worker_id: "ceo", department_id: "exec_ceo", status: "ACTIVE", permission_grants: ["company.manage", "approval.decide"] }],
    budgets: {},
    policy: {},
  }, null, 2);
}

function manifestFromReadModel(detail: CompanyReadModel): Record<string, unknown> | null {
  const value = detail.manifest?.manifest_json;
  if (typeof value === "string") {
    try {
      const parsed: unknown = JSON.parse(value);
      return Object.keys(asRecord(parsed)).length ? asRecord(parsed) : null;
    } catch {
      return null;
    }
  }
  const parsed = asRecord(value);
  return Object.keys(parsed).length ? parsed : null;
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-white">{value}</div>
    </div>
  );
}

export default function CompaniesPage() {
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<CompanyReadModel | null>(null);
  const [history, setHistory] = useState<ManifestVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [manifestText, setManifestText] = useState("");
  const [validatedText, setValidatedText] = useState("");
  const [validationDigest, setValidationDigest] = useState("");
  const [manifestBusy, setManifestBusy] = useState(false);
  const [rollbackVersion, setRollbackVersion] = useState<number | null>(null);
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollbackBusy, setRollbackBusy] = useState(false);
  const [registeredWorkers, setRegisteredWorkers] = useState<RegisteredWorker[]>([]);
  const [workerLoadError, setWorkerLoadError] = useState("");
  const [newAssignmentWorker, setNewAssignmentWorker] = useState("");
  const [newAssignmentDepartment, setNewAssignmentDepartment] = useState("");
  const [budgetDraftValues, setBudgetDraftValues] = useState<Record<string, string>>({});

  const selectedCompany = useMemo(
    () => companies.find((company) => company.id === selectedId) ?? detail?.company ?? null,
    [companies, detail?.company, selectedId],
  );
  const parsedManifest = useMemo(() => {
    try {
      return JSON.parse(manifestText) as Record<string, unknown>;
    } catch {
      return null;
    }
  }, [manifestText]);

  const manifestDepartments = useMemo(() => records(parsedManifest?.departments), [parsedManifest]);
  const manifestAssignments = useMemo(() => records(parsedManifest?.worker_assignments), [parsedManifest]);
  const manifestBudgets = useMemo(() => asRecord(parsedManifest?.budgets), [parsedManifest]);
  const ceoCandidates = useMemo(() => {
    const leaders = new Set(manifestDepartments.map((department) => String(department.chief_worker_id ?? "")));
    return manifestAssignments.filter((assignment) => leaders.has(String(assignment.worker_id ?? "")));
  }, [manifestAssignments, manifestDepartments]);
  const unsupportedBudgetKeys = useMemo(
    () => Object.keys(manifestBudgets).filter((key) => !enforcedCompanyBudgetKeys.includes(key as typeof enforcedCompanyBudgetKeys[number])),
    [manifestBudgets],
  );

  const workerOptions = useMemo(() => {
    const byId = new Map<string, RegisteredWorker>();
    registeredWorkers.forEach((worker) => {
      const id = workerId(worker);
      if (id) byId.set(id, worker);
    });
    manifestAssignments.forEach((assignment) => {
      const id = typeof assignment.worker_id === "string" ? assignment.worker_id : "";
      if (id && !byId.has(id)) byId.set(id, { worker_id: id });
    });
    return [...byId.entries()].map(([id, worker]) => ({
      id,
      label: `${workerLabel(worker)}${registeredWorkers.some((item) => workerId(item) === id) ? "" : " (not currently registered)"}`,
    }));
  }, [manifestAssignments, registeredWorkers]);

  const unassignedWorkers = useMemo(() => {
    const assignedIds = new Set(manifestAssignments.map((assignment) => String(assignment.worker_id ?? "")));
    return registeredWorkers.filter((worker) => workerId(worker) && !assignedIds.has(workerId(worker)));
  }, [manifestAssignments, registeredWorkers]);

  useEffect(() => {
    let active = true;
    void requestJson<RegisteredWorker[]>("/api/workers")
      .then((workers) => {
        if (active) {
          setRegisteredWorkers(Array.isArray(workers) ? workers : []);
          setWorkerLoadError("");
        }
      })
      .catch((reason) => {
        if (active) setWorkerLoadError(reason instanceof Error ? reason.message : "Registered workers could not be loaded.");
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const next: Record<string, string> = {};
    Object.entries(manifestBudgets).forEach(([key, value]) => {
      if (typeof value === "number") next[key] = String(value);
    });
    setBudgetDraftValues(next);
  }, [manifestBudgets]);

  const loadCompanies = useCallback(async (preferId?: string) => {
    setLoading(true);
    setError("");
    try {
      const rows = await requestJson<Company[]>("/api/companies");
      const next = Array.isArray(rows) ? rows : [];
      setCompanies(next);
      const nextId = preferId && next.some((company) => company.id === preferId)
        ? preferId
        : selectedId && next.some((company) => company.id === selectedId)
          ? selectedId
          : next[0]?.id ?? "";
      setSelectedId(nextId);
      if (!nextId) {
        setDetail(null);
        setHistory([]);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Companies could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => { void loadCompanies(); }, [loadCompanies]);

  const loadSelected = useCallback(async (id: string) => {
    if (!id) return;
    setDetailLoading(true);
    setError("");
    try {
      const [nextDetail, nextHistory] = await Promise.all([
        requestJson<CompanyReadModel>(`/api/companies/${encodeURIComponent(id)}`),
        requestJson<ManifestVersion[]>(`/api/companies/${encodeURIComponent(id)}/manifest/history`),
      ]);
      setDetail(nextDetail);
      setHistory(Array.isArray(nextHistory) ? nextHistory : []);
      const manifest = manifestFromReadModel(nextDetail);
      const company = nextDetail.company;
      setManifestText(manifest
        ? JSON.stringify(manifest, null, 2)
        : company ? starterManifest(company) : "{}");
      setValidatedText("");
      setValidationDigest("");
      setRollbackVersion(null);
      setRollbackReason("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Company details could not be loaded.");
      setDetail(null);
      setHistory([]);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void loadSelected(selectedId); }, [loadSelected, selectedId]);

  function writeManifest(next: Row) {
    setManifestText(JSON.stringify(next, null, 2));
    setValidatedText("");
    setValidationDigest("");
    setNotice("");
    setError("");
  }

  function updateDepartment(index: number, patch: Row) {
    if (!parsedManifest) return;
    const currentDepartments = records(parsedManifest.departments);
    const current = currentDepartments[index];
    if (!current) return;
    const next = { ...current, ...patch };
    const oldId = String(current.id ?? "");
    const nextId = String(next.id ?? "");
    const nextDepartments = currentDepartments.map((department, itemIndex) => itemIndex === index ? next : department);
    const nextManifest: Row = { ...parsedManifest, departments: nextDepartments };
    if (oldId !== nextId) {
      nextManifest.worker_assignments = records(parsedManifest.worker_assignments).map((assignment) =>
        assignment.department_id === oldId ? { ...assignment, department_id: nextId } : assignment,
      );
      if (newAssignmentDepartment === oldId) setNewAssignmentDepartment(nextId);
    }
    writeManifest(nextManifest);
  }

  function updateAssignment(index: number, patch: Row) {
    if (!parsedManifest) return;
    const nextAssignments = records(parsedManifest.worker_assignments);
    const current = nextAssignments[index];
    if (!current) return;
    const next = { ...current, ...patch };
    nextAssignments[index] = next;
    const oldDepartment = String(current.department_id ?? "");
    const nextDepartment = String(next.department_id ?? "");
    const id = String(current.worker_id ?? "");
    const moving = oldDepartment !== nextDepartment;
    const nextDepartments = records(parsedManifest.departments).map((department) => {
      const departmentId = String(department.id ?? "");
      let workerIds = stringList(department.worker_ids);
      if (moving && departmentId === oldDepartment) workerIds = workerIds.filter((worker) => worker !== id);
      if (departmentId === nextDepartment && !workerIds.includes(id)) workerIds = [...workerIds, id];
      return {
        ...department,
        worker_ids: workerIds,
        ...(moving && departmentId === oldDepartment && department.chief_worker_id === id
          ? { chief_worker_id: null }
          : {}),
      };
    });
    writeManifest({ ...parsedManifest, departments: nextDepartments, worker_assignments: nextAssignments });
  }

  function addDepartment() {
    if (!parsedManifest) return;
    const current = records(parsedManifest.departments);
    const ids = new Set(current.map((department) => String(department.id ?? "")));
    let suffix = current.length + 1;
    let id = `department_${suffix}`;
    while (ids.has(id)) id = `department_${++suffix}`;
    writeManifest({
      ...parsedManifest,
      departments: [...current, { id, name: "New department", chief_worker_id: null, worker_ids: [] }],
    });
  }

  function removeDepartment(index: number) {
    if (!parsedManifest || manifestDepartments.length <= 1) return;
    const department = manifestDepartments[index];
    if (!department || manifestAssignments.some((assignment) => assignment.department_id === department.id)) return;
    const nextDepartments = manifestDepartments.filter((_item, itemIndex) => itemIndex !== index);
    if (newAssignmentDepartment === String(department.id ?? "")) {
      setNewAssignmentDepartment(String(nextDepartments[0]?.id ?? ""));
    }
    writeManifest({
      ...parsedManifest,
      departments: nextDepartments,
    });
  }

  function addAssignment() {
    if (!parsedManifest) return;
    const firstDepartmentId = String(manifestDepartments[0]?.id ?? "");
    const departmentId = newAssignmentDepartment || firstDepartmentId;
    if (!departmentId || !manifestDepartments.some((department) => String(department.id ?? "") === departmentId)) {
      setNewAssignmentDepartment("");
      setError("That department is no longer in this manifest. Select a current department before adding the worker.");
      return;
    }
    if (!newAssignmentWorker) return;
    if (manifestAssignments.some((assignment) => assignment.worker_id === newAssignmentWorker)) {
      setError("That worker already has an assignment. Edit the existing assignment instead.");
      return;
    }
    const nextAssignments = [...manifestAssignments, {
      worker_id: newAssignmentWorker,
      department_id: departmentId,
      status: "ACTIVE",
      tool_grants: [],
      permission_grants: [],
      budget: {},
      approval_required: false,
      metadata: {},
    }];
    const nextDepartments = manifestDepartments.map((department) => String(department.id ?? "") === departmentId
      ? { ...department, worker_ids: [...new Set([...stringList(department.worker_ids), newAssignmentWorker])] }
      : department);
    writeManifest({ ...parsedManifest, departments: nextDepartments, worker_assignments: nextAssignments });
    setNewAssignmentWorker("");
  }

  function removeAssignment(index: number) {
    if (!parsedManifest) return;
    const assignment = manifestAssignments[index];
    if (!assignment) return;
    const id = String(assignment.worker_id ?? "");
    if (id === parsedManifest.ceo_worker_id) {
      setError("Select a different CEO before removing this assignment.");
      return;
    }
    const nextDepartments = manifestDepartments.map((department) => ({
      ...department,
      worker_ids: stringList(department.worker_ids).filter((worker) => worker !== id),
      ...(department.chief_worker_id === id ? { chief_worker_id: null } : {}),
    }));
    writeManifest({
      ...parsedManifest,
      departments: nextDepartments,
      worker_assignments: manifestAssignments.filter((_item, itemIndex) => itemIndex !== index),
    });
  }

  function addBudget() {
    if (!parsedManifest) return;
    const budgets = asRecord(parsedManifest.budgets);
    const key = enforcedCompanyBudgetKeys.find((candidate) => !Object.hasOwn(budgets, candidate));
    if (!key) {
      setNotice("Both supported company budget keys are configured.");
      return;
    }
    writeManifest({ ...parsedManifest, budgets: { ...budgets, [key]: key === "max_concurrent_runs" ? 1 : 0 } });
  }

  function updateBudgetDraft(key: string, raw: string) {
    setBudgetDraftValues((current) => ({ ...current, [key]: raw }));
    setValidatedText("");
    setValidationDigest("");
    setNotice("");
    setError("");
  }

  function budgetValidationError(key: string, raw: string): string | null {
    const amount = Number(raw);
    if (!raw.trim() || !Number.isFinite(amount) || amount < 0) {
      return `Budget ${key} must be a finite amount of zero or greater.`;
    }
    if (key === "max_concurrent_runs" && !Number.isInteger(amount)) {
      return "Budget max_concurrent_runs must be a whole number.";
    }
    return null;
  }

  function commitBudget(key: string) {
    if (!parsedManifest) return;
    const raw = budgetDraftValues[key] ?? "";
    const amount = Number(raw);
    const validationError = budgetValidationError(key, raw);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    writeManifest({ ...parsedManifest, budgets: { ...manifestBudgets, [key]: amount } });
  }

  function removeBudget(key: string) {
    if (!parsedManifest) return;
    const nextBudgets = { ...manifestBudgets };
    delete nextBudgets[key];
    setBudgetDraftValues((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    writeManifest({ ...parsedManifest, budgets: nextBudgets });
  }

  function changeCeo(nextCeoId: string) {
    if (!parsedManifest) return;
    const currentCeoId = String(parsedManifest.ceo_worker_id ?? "");
    if (!nextCeoId || nextCeoId === currentCeoId) return;
    const isExistingDepartmentChief = manifestDepartments.some((department) => department.chief_worker_id === nextCeoId);
    const nextAssignment = manifestAssignments.find((assignment) => assignment.worker_id === nextCeoId);
    if (!isExistingDepartmentChief || !nextAssignment) {
      setError("Choose an assigned worker who already leads a department to become CEO.");
      return;
    }
    const currentAssignment = manifestAssignments.find((assignment) => assignment.worker_id === currentCeoId);
    const transferredGrants = stringList(currentAssignment?.permission_grants).filter((grant) => ceoOnlyPermissionGrants.has(grant));
    const nextAssignments = manifestAssignments.map((assignment) => {
      const id = String(assignment.worker_id ?? "");
      if (id === currentCeoId) {
        return { ...assignment, permission_grants: stringList(assignment.permission_grants).filter((grant) => !ceoOnlyPermissionGrants.has(grant)) };
      }
      if (id === nextCeoId) {
        return { ...assignment, permission_grants: [...new Set([...stringList(assignment.permission_grants), ...transferredGrants])] };
      }
      return assignment;
    });
    writeManifest({ ...parsedManifest, ceo_worker_id: nextCeoId, worker_assignments: nextAssignments });
    setNotice(`CEO authority is now drafted for ${nextCeoId}. Executive grants moved from ${currentCeoId}; validate and apply the manifest to save this change.`);
  }

  async function createCompany(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setCreateBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await requestJson<Company>("/api/companies", {
        method: "POST",
        body: JSON.stringify({ slug: slug.trim(), name: name.trim(), description: description.trim() }),
      });
      setShowCreate(false);
      setSlug("");
      setName("");
      setDescription("");
      setNotice(`Company ${created.name} was created.`);
      await loadCompanies(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Company could not be created.");
    } finally {
      setCreateBusy(false);
    }
  }

  async function validateManifest() {
    if (!selectedCompany || !parsedManifest) {
      setError("Manifest must be valid JSON before it can be validated.");
      setValidatedText("");
      setValidationDigest("");
      return;
    }
    const invalidBudget = Object.entries(manifestBudgets).find(([key, value]) =>
      budgetValidationError(key, budgetDraftValues[key] ?? String(value)) !== null,
    );
    if (invalidBudget) {
      setError(budgetValidationError(invalidBudget[0], budgetDraftValues[invalidBudget[0]] ?? String(invalidBudget[1])) ?? "A budget limit is invalid.");
      setValidatedText("");
      setValidationDigest("");
      return;
    }
    setManifestBusy(true);
    setError("");
    setNotice("");
    setValidatedText("");
    setValidationDigest("");
    try {
      const result = await requestJson<{ valid: boolean; digest?: string }>(
        `/api/companies/${encodeURIComponent(selectedCompany.id)}/manifest/validate`,
        { method: "POST", body: JSON.stringify({ manifest: parsedManifest, source: "dashboard_operator" }) },
      );
      if (!result.valid || !result.digest) throw new Error("The API did not return a valid manifest digest.");
      setValidatedText(manifestText);
      setValidationDigest(result.digest);
      setNotice(`Manifest is valid. Digest ${result.digest.slice(0, 12)}…`);
    } catch (reason) {
      const status = (reason as HttpError).status;
      setError(status === 422
        ? `Manifest validation failed (HTTP 422): ${reason instanceof Error ? reason.message : "Invalid manifest."}`
        : reason instanceof Error ? reason.message : "Manifest validation failed.");
    } finally {
      setManifestBusy(false);
    }
  }

  async function applyManifest() {
    if (!selectedCompany || !parsedManifest || !validationDigest || validatedText !== manifestText) return;
    setManifestBusy(true);
    setError("");
    setNotice("");
    try {
      await requestJson<CompanyReadModel>(
        `/api/companies/${encodeURIComponent(selectedCompany.id)}/manifest/apply`,
        { method: "POST", body: JSON.stringify({ manifest: parsedManifest, source: "dashboard_operator" }) },
      );
      setNotice("Company manifest applied through the company control-plane API.");
      await loadSelected(selectedCompany.id);
      await loadCompanies(selectedCompany.id);
    } catch (reason) {
      const failure = reason as HttpError;
      if (failure.status === 409 || failure.status === 422) {
        setValidatedText("");
        setValidationDigest("");
      }
      setError(failure.status === 409
        ? `Manifest apply conflict (HTTP 409): ${reason instanceof Error ? reason.message : "Company state changed."} No changes were applied. Review the current state and validate the manifest again.`
        : failure.status === 422
          ? `Manifest validation failed (HTTP 422): ${reason instanceof Error ? reason.message : "Invalid manifest."}`
          : reason instanceof Error ? reason.message : "Manifest could not be applied.");
    } finally {
      setManifestBusy(false);
    }
  }

  async function rollbackManifest() {
    if (!selectedCompany || rollbackVersion === null || !rollbackReason.trim()) return;
    setRollbackBusy(true);
    setError("");
    setNotice("");
    try {
      await requestJson<CompanyReadModel>(
        `/api/companies/${encodeURIComponent(selectedCompany.id)}/manifest/rollback`,
        { method: "POST", body: JSON.stringify({ manifest_version: rollbackVersion, reason: rollbackReason.trim() }) },
      );
      setNotice(`Company manifest rolled back to version ${rollbackVersion}.`);
      await loadSelected(selectedCompany.id);
    } catch (reason) {
      const failure = reason as HttpError;
      setError(failure.status === 409
        ? `Manifest rollback conflict (HTTP 409): ${reason instanceof Error ? reason.message : "Company state changed."}`
        : reason instanceof Error ? reason.message : "Manifest rollback failed.");
    } finally {
      setRollbackBusy(false);
    }
  }

  const company = detail?.company ?? selectedCompany;
  const departments = detail?.departments ?? [];
  const assignments = detail?.assignments ?? [];
  const budgets = detail?.budgets ?? [];
  const activeManifestVersion = Number(detail?.manifest?.manifest_version ?? 0);
  const canApply = Boolean(parsedManifest && validationDigest && validatedText === manifestText && !manifestBusy);

  return (
    <main className="space-y-5" aria-label="Companies">
      <PageHeader
        icon="users"
        title="Companies"
        description="Inspect company structure and manage versioned company manifests through the control plane."
        actions={
          <>
            <button type="button" className={buttonClass} onClick={() => void loadCompanies(selectedId)} disabled={loading}>
              <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Refresh
            </button>
            <button type="button" className={primaryButtonClass} onClick={() => setShowCreate((current) => !current)}>
              <Plus size={15} /> New company
            </button>
          </>
        }
      />

      {error && <ErrorBanner title="Company operation failed" action={<button className={buttonClass} type="button" onClick={() => setError("")}>Dismiss</button>}>{error}</ErrorBanner>}
      {notice && <div role="status" className="rounded-xl border border-emerald-800/70 bg-emerald-950/30 px-4 py-3 text-sm text-emerald-200">{notice}</div>}

      {showCreate && (
        <section className="rounded-2xl border border-slate-800 bg-slate-950/45 p-5" aria-labelledby="create-company-title">
          <h2 id="create-company-title" className="text-base font-semibold text-white">Create company</h2>
          <p className="mt-1 text-sm text-slate-400">This creates the company record. Configure departments, assignments and budgets by validating and applying a manifest below.</p>
          <form className="mt-4 grid gap-3 md:grid-cols-2" onSubmit={createCompany}>
            <label className="space-y-1 text-xs text-slate-400">Company name
              <input className={fieldClass} required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} placeholder="AIAT Research" />
            </label>
            <label className="space-y-1 text-xs text-slate-400">Slug
              <input className={fieldClass} required maxLength={120} pattern="[a-z][a-z0-9-]*" value={slug} onChange={(event) => setSlug(event.target.value)} placeholder="aiat-research" />
            </label>
            <label className="space-y-1 text-xs text-slate-400 md:col-span-2">Description
              <textarea className={fieldClass} maxLength={20000} rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this company is responsible for" />
            </label>
            <div className="flex gap-2 md:col-span-2">
              <button type="submit" className={primaryButtonClass} disabled={createBusy}>{createBusy ? "Creating…" : "Create company"}</button>
              <button type="button" className={buttonClass} onClick={() => setShowCreate(false)} disabled={createBusy}>Cancel</button>
            </div>
          </form>
        </section>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(250px,0.7fr)_minmax(0,2fr)]">
        <section className="rounded-2xl border border-slate-800 bg-slate-950/45 p-4" aria-label="Company registry">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-300">Company registry</h2>
            <span className="text-xs text-slate-500">{companies.length} companies</span>
          </div>
          {loading && companies.length === 0 ? <p className="py-8 text-center text-sm text-slate-500">Loading companies…</p> : null}
          {!loading && companies.length === 0 ? (
            <EmptyState icon="users" title="No companies yet" description="Create a company record to start configuring its operating structure." action={<button type="button" className={primaryButtonClass} onClick={() => setShowCreate(true)}><Plus size={14} /> Create company</button>} />
          ) : (
            <ul className="space-y-1">
              {companies.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className={`flex min-h-14 w-full items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left transition ${selectedId === item.id ? "border-blue-500/50 bg-blue-500/10 text-white" : "border-transparent text-slate-300 hover:border-slate-800 hover:bg-slate-900"}`}
                    aria-current={selectedId === item.id ? "true" : undefined}
                    onClick={() => { setNotice(""); setSelectedId(item.id); }}
                  >
                    <span className="min-w-0"><span className="block truncate text-sm font-medium">{item.name}</span><span className="mt-0.5 block truncate text-xs text-slate-500">{item.slug}</span></span>
                    <ChevronRight size={15} className="shrink-0 text-slate-500" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="min-w-0 space-y-5">
          {!company && !loading ? <EmptyState icon="inbox" title="Select a company" description="Choose a company from the registry to inspect its departments, assignments, budgets and manifest history." /> : null}
          {company && (
            <>
              <section className="rounded-2xl border border-slate-800 bg-slate-950/45 p-5" aria-label="Company details">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="rounded-xl border border-blue-400/20 bg-blue-500/10 p-3 text-blue-300"><Building2 size={20} /></div>
                    <div className="min-w-0"><h2 className="truncate text-xl font-semibold text-white">{company.name}</h2><p className="mt-1 text-sm text-slate-400">{company.slug} · {display(company.status, "ACTIVE")}</p>{company.description && <p className="mt-2 max-w-3xl text-sm text-slate-400">{company.description}</p>}</div>
                  </div>
                  <div className="text-xs text-slate-500">{detailLoading ? "Refreshing details…" : `Created ${dateTime(company.created_at)}`}</div>
                </div>
                <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
                  <Metric label="Departments" value={departments.length} />
                  <Metric label="Worker assignments" value={assignments.length} />
                  <Metric label="Budgets" value={budgets.length} />
                  <Metric label="Manifest version" value={activeManifestVersion} />
                </div>
              </section>

              <section className="grid gap-4 lg:grid-cols-3" aria-label="Company operating structure">
                <div className="rounded-2xl border border-slate-800 bg-slate-950/45 p-4"><h3 className="text-sm font-semibold text-white">Departments</h3>{departments.length ? <ul className="mt-3 space-y-2">{departments.map((row, index) => <li key={String(row.id ?? row.department_key ?? index)} className="rounded-lg bg-slate-900/70 px-3 py-2"><div className="text-sm font-medium text-slate-200">{rowName(row, ["name", "department_key", "id"])}</div><div className="mt-1 text-xs text-slate-500">Chief: {rowName(row, ["chief_worker_id", "chief_worker_name"])}</div></li>)}</ul> : <p className="mt-3 text-sm text-slate-500">No departments configured.</p>}</div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950/45 p-4"><h3 className="text-sm font-semibold text-white">Worker assignments</h3>{assignments.length ? <ul className="mt-3 space-y-2">{assignments.map((row, index) => <li key={String(row.id ?? row.worker_id ?? index)} className="rounded-lg bg-slate-900/70 px-3 py-2"><div className="text-sm font-medium text-slate-200">{rowName(row, ["worker_id", "worker_name"])}</div><div className="mt-1 text-xs text-slate-500">{rowName(row, ["department_id", "department_key"])} · {display(row.status)}</div></li>)}</ul> : <p className="mt-3 text-sm text-slate-500">No workers assigned.</p>}</div>
                <div className="rounded-2xl border border-slate-800 bg-slate-950/45 p-4"><h3 className="text-sm font-semibold text-white">Budgets</h3>{budgets.length ? <ul className="mt-3 space-y-2">{budgets.map((row, index) => <li key={String(row.id ?? row.budget_key ?? index)} className="flex items-center justify-between gap-3 rounded-lg bg-slate-900/70 px-3 py-2"><span className="text-sm text-slate-300">{rowName(row, ["budget_key", "key"])}</span><span className="text-sm font-medium text-slate-100">{display(row.limit_value ?? row.limit_usd ?? row.limit ?? row.amount ?? row.value)} {display(row.currency, "USD")}</span></li>)}</ul> : <p className="mt-3 text-sm text-slate-500">No budgets configured.</p>}</div>
              </section>

              <section className="rounded-2xl border border-slate-800 bg-slate-950/45 p-5" aria-label="Guided company configuration">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-white">Operating structure editor</h2>
                    <p className="mt-1 max-w-3xl text-sm text-slate-400">Configure departments, chiefs, worker assignments, and budget limits. Changes remain a draft until the manifest is validated and applied through AIAT.</p>
                  </div>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 px-2.5 py-1 text-xs text-slate-400"><ShieldCheck size={13} /> Manifest draft</span>
                </div>

                {!parsedManifest ? (
                  <p className="mt-4 rounded-lg border border-amber-800/60 bg-amber-950/20 px-3 py-2 text-sm text-amber-200">The advanced manifest JSON is invalid, so guided editing is paused. Correct the JSON or reload the saved company manifest.</p>
                ) : (
                  <div className="mt-5 space-y-6">
                    <div className="grid gap-4 md:grid-cols-2">
                      <label className="space-y-1 text-xs font-medium text-slate-300" htmlFor="company-ceo-worker">CEO worker
                        <select id="company-ceo-worker" className={fieldClass} value={String(parsedManifest.ceo_worker_id ?? "")} onChange={(event) => changeCeo(event.target.value)}>
                          <option value="">Select an assigned department chief</option>
                          {ceoCandidates.map((assignment, index) => {
                            const id = String(assignment.worker_id ?? "");
                            const worker = workerOptions.find((option) => option.id === id);
                            return id ? <option key={`${id}-${index}`} value={id}>{worker?.label ?? id}</option> : null;
                          })}
                        </select>
                        <span className="block font-normal text-slate-500">Only assigned department chiefs are eligible. Changing CEO transfers CEO-only grants in this draft; validate and apply to save.</span>
                      </label>
                      <div className="rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-3 text-xs leading-5 text-slate-400">
                        {workerLoadError ? <span className="text-amber-200">Registered worker choices could not be refreshed: {workerLoadError}</span> : <span>{registeredWorkers.length} registered workers available for assignment.</span>}
                        {registeredWorkers.length === 0 && !workerLoadError ? <span> Refresh the page after workers are registered.</span> : null}
                      </div>
                    </div>

                    <div>
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                        <div><h3 className="text-sm font-semibold text-slate-100">Departments</h3><p className="mt-1 text-xs text-slate-500">Each department needs a unique ID. Chiefs must be assigned to that same department.</p></div>
                        <button type="button" className={buttonClass} onClick={addDepartment}><Plus size={14} /> Add department</button>
                      </div>
                      <div className="space-y-3">
                        {manifestDepartments.map((department, index) => {
                          const departmentId = String(department.id ?? "");
                          const departmentName = String(department.name ?? `Department ${index + 1}`);
                          const assignedHere = manifestAssignments.filter((assignment) => assignment.department_id === departmentId);
                          const canRemove = manifestDepartments.length > 1 && assignedHere.length === 0;
                          return (
                            <fieldset key={`${departmentId}-${index}`} className="rounded-xl border border-slate-800 bg-slate-900/45 p-4">
                              <legend className="sr-only">Department {departmentName}</legend>
                              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[minmax(10rem,0.7fr)_minmax(12rem,1fr)_minmax(14rem,1fr)_auto] xl:items-end">
                                <label className="space-y-1 text-xs text-slate-400">Department ID
                                  <input className={fieldClass} aria-label={`Department ID ${index + 1}`} value={departmentId} maxLength={120} pattern="[a-z][a-z0-9_-]*" onChange={(event) => updateDepartment(index, { id: event.target.value })} />
                                </label>
                                <label className="space-y-1 text-xs text-slate-400">Department name
                                  <input className={fieldClass} aria-label={`Department name ${index + 1}`} value={departmentName} maxLength={200} onChange={(event) => updateDepartment(index, { name: event.target.value })} />
                                </label>
                                <label className="space-y-1 text-xs text-slate-400">Department chief
                                  <select className={fieldClass} aria-label={`Chief for ${departmentName}`} value={typeof department.chief_worker_id === "string" ? department.chief_worker_id : ""} onChange={(event) => updateDepartment(index, { chief_worker_id: event.target.value || null })}>
                                    <option value="">No chief selected</option>
                                    {assignedHere.map((assignment, assignmentIndex) => {
                                      const id = String(assignment.worker_id ?? "");
                                      const worker = workerOptions.find((option) => option.id === id);
                                      return id ? <option key={`${id}-${assignmentIndex}`} value={id}>{worker?.label ?? id}</option> : null;
                                    })}
                                  </select>
                                </label>
                                <button type="button" className={buttonClass} aria-label={`Remove department ${departmentName}`} title={canRemove ? "Remove department" : "Reassign its workers first; at least one department is required."} disabled={!canRemove} onClick={() => removeDepartment(index)}>Remove</button>
                              </div>
                              <p className="mt-3 text-xs text-slate-500">Workers: {stringList(department.worker_ids).map((id) => workerOptions.find((option) => option.id === id)?.label ?? id).join(", ") || "none assigned"}</p>
                            </fieldset>
                          );
                        })}
                      </div>
                    </div>

                    <div>
                      <div className="mb-3"><h3 className="text-sm font-semibold text-slate-100">Worker assignments</h3><p className="mt-1 text-xs text-slate-500">Assignments connect an existing registered worker to one department. Moving a worker updates department membership and clears their old chief role.</p></div>
                      <div className="space-y-2">
                        {manifestAssignments.map((assignment, index) => {
                          const id = String(assignment.worker_id ?? "");
                          const worker = workerOptions.find((option) => option.id === id);
                          const isCeo = id === parsedManifest.ceo_worker_id;
                          return (
                            <div key={`${id}-${index}`} className="grid gap-3 rounded-xl border border-slate-800 bg-slate-900/45 p-3 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_10rem_auto] md:items-end" aria-label={`Worker assignment ${worker?.label ?? id}`}>
                              <div><div className="text-xs font-medium text-slate-400">Worker</div><div className="mt-1 flex min-h-10 items-center rounded-lg bg-slate-950 px-3 text-sm text-slate-100">{worker?.label ?? id}</div></div>
                              <label className="space-y-1 text-xs text-slate-400">Department
                                <select className={fieldClass} aria-label={`Department for ${id}`} value={String(assignment.department_id ?? "")} onChange={(event) => updateAssignment(index, { department_id: event.target.value })}>
                                  {manifestDepartments.map((department, departmentIndex) => <option key={`${String(department.id)}-${departmentIndex}`} value={String(department.id ?? "")}>{String(department.name ?? department.id ?? "Department")}</option>)}
                                </select>
                              </label>
                              <label className="space-y-1 text-xs text-slate-400">Status
                                <select className={fieldClass} aria-label={`Status for ${id}`} value={String(assignment.status ?? "ACTIVE")} onChange={(event) => updateAssignment(index, { status: event.target.value })}>
                                  <option value="ACTIVE">Active</option><option value="INACTIVE">Inactive</option><option value="DRAINING">Draining</option>
                                </select>
                              </label>
                              <button type="button" className={buttonClass} aria-label={`Remove assignment for ${id}`} disabled={isCeo} title={isCeo ? "Select a different CEO before removing this assignment." : "Remove assignment"} onClick={() => removeAssignment(index)}>Remove</button>
                            </div>
                          );
                        })}
                      </div>
                      <div className="mt-3 grid gap-2 rounded-xl border border-dashed border-slate-700 p-3 md:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_auto] md:items-end">
                        <label className="space-y-1 text-xs text-slate-400">Add registered worker
                          <select className={fieldClass} aria-label="New assignment worker" value={newAssignmentWorker} onChange={(event) => setNewAssignmentWorker(event.target.value)}>
                            <option value="">Select worker</option>
                            {unassignedWorkers.map((worker) => <option key={workerId(worker)} value={workerId(worker)}>{workerLabel(worker)}</option>)}
                          </select>
                        </label>
                        <label className="space-y-1 text-xs text-slate-400">Assign to department
                          <select className={fieldClass} aria-label="New assignment department" value={manifestDepartments.some((department) => String(department.id ?? "") === newAssignmentDepartment) ? newAssignmentDepartment : String(manifestDepartments[0]?.id ?? "")} onChange={(event) => setNewAssignmentDepartment(event.target.value)}>
                            {manifestDepartments.map((department, index) => <option key={`${String(department.id)}-${index}`} value={String(department.id ?? "")}>{String(department.name ?? department.id ?? "Department")}</option>)}
                          </select>
                        </label>
                        <button type="button" className={buttonClass} disabled={!newAssignmentWorker || manifestDepartments.length === 0} onClick={addAssignment}><Plus size={14} /> Add assignment</button>
                        {unassignedWorkers.length === 0 && <p className="text-xs text-slate-500 md:col-span-3">All currently registered workers already have an assignment.</p>}
                      </div>
                    </div>

                    <div>
                      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                        <div><h3 className="text-sm font-semibold text-slate-100">Budget limits</h3><p className="mt-1 text-xs text-slate-500">Configure values for the company budget keys supported by WorkerRun dispatch.</p><p className="mt-2 max-w-3xl text-xs text-slate-400">max_cost_usd sets the company budget pool. A cost-producing run needs a positive per-run budget (budget_usd or budget.max_cost_usd) to reserve against that pool. A per-run budget of 0 behaves like an omitted cost budget; a company pool of 0 alone does not universally block dispatch when no cost budget is requested.</p></div>
                        <button type="button" className={buttonClass} onClick={addBudget} disabled={enforcedCompanyBudgetKeys.every((key) => Object.hasOwn(manifestBudgets, key))} title={enforcedCompanyBudgetKeys.every((key) => Object.hasOwn(manifestBudgets, key)) ? "Both supported company budget keys are already configured." : "Add a supported company budget key."}><Plus size={14} /> Add budget</button>
                      </div>
                      {enforcedCompanyBudgetKeys.every((key) => Object.hasOwn(manifestBudgets, key)) && <p role="status" className="mb-3 text-xs text-slate-400">Both supported company budget keys are configured.</p>}
                      {unsupportedBudgetKeys.length > 0 && <p role="note" className="mb-3 rounded-lg border border-amber-800/60 bg-amber-950/20 px-3 py-2 text-sm text-amber-200">These existing manifest keys are preserved, but WorkerRun dispatch does not enforce them: {unsupportedBudgetKeys.join(", ")}.</p>}
                      {Object.keys(manifestBudgets).length ? <div className="space-y-2">{Object.entries(manifestBudgets).map(([key, value], index) => (
                        <div key={key} className="grid gap-3 rounded-xl border border-slate-800 bg-slate-900/45 p-3 md:grid-cols-[minmax(12rem,0.8fr)_minmax(12rem,1fr)_auto] md:items-end">
                          <div><div className="text-xs font-medium text-slate-400">Budget key</div><div className="mt-1 flex min-h-10 items-center rounded-lg bg-slate-950 px-3 font-mono text-sm text-slate-100">{key}</div>{unsupportedBudgetKeys.includes(key) && <span className="mt-1 block text-xs text-amber-200">Not enforced for WorkerRun dispatch</span>}</div>
                          <label className="space-y-1 text-xs text-slate-400">Amount
                            <input type="number" min="0" step={key === "max_concurrent_runs" ? "1" : "any"} className={fieldClass} aria-label={`Budget amount ${key}`} value={budgetDraftValues[key] ?? String(value)} onChange={(event) => updateBudgetDraft(key, event.target.value)} onBlur={() => commitBudget(key)} />
                          </label>
                          <button type="button" className={buttonClass} aria-label={`Remove budget ${key}`} onClick={() => removeBudget(key)}>Remove</button>
                          {typeof value !== "number" && <p className="text-xs text-amber-200 md:col-span-3">This value is not numeric. Replace it with a non-negative number before validation.</p>}
                          <span className="sr-only">Budget row {index + 1}</span>
                        </div>
                      ))}</div> : <p className="rounded-lg border border-dashed border-slate-700 px-3 py-4 text-sm text-slate-500">No budget limits configured yet.</p>}
                    </div>
                  </div>
                )}
              </section>

              <section className="rounded-2xl border border-slate-800 bg-slate-950/45 p-5" aria-label="Company manifest">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div><h2 className="text-base font-semibold text-white">Review and apply</h2><p className="mt-1 text-sm text-slate-400">Validate your guided changes with the company API, then apply the exact validated manifest. The API remains authoritative for policy checks and revision conflicts.</p></div>
                  <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 px-2.5 py-1 text-xs text-slate-400"><ShieldCheck size={13} /> Operator API</span>
                </div>
                <details className="mt-4 rounded-xl border border-slate-800 bg-slate-900/35 p-4">
                  <summary className="cursor-pointer text-sm font-medium text-slate-300 focus-visible:rounded">Advanced manifest JSON</summary>
                  <p className="mt-2 text-xs text-slate-500">Use this for schema-supported fields that do not yet have guided controls. Invalid JSON pauses the guided editor and prevents validation.</p>
                  <label htmlFor="company-manifest-json" className="mt-3 block text-xs font-medium text-slate-400">Manifest JSON</label>
                  <textarea id="company-manifest-json" className={`${fieldClass} mt-1 min-h-[20rem] font-mono text-xs leading-5`} spellCheck={false} value={manifestText} onChange={(event) => { setManifestText(event.target.value); setValidatedText(""); setValidationDigest(""); setNotice(""); setError(""); }} />
                  {!parsedManifest && <p className="mt-2 text-xs text-amber-300">The editor currently contains invalid JSON. Correct the syntax before requesting server validation.</p>}
                </details>
                {validationDigest && validatedText === manifestText && <p className="mt-2 flex items-center gap-1.5 text-xs text-emerald-300"><Check size={13} /> Validated digest: {validationDigest}</p>}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className={buttonClass} onClick={() => void validateManifest()} disabled={!parsedManifest || manifestBusy || detailLoading}>{manifestBusy ? "Working…" : "Validate manifest"}</button>
                  <button type="button" className={primaryButtonClass} onClick={() => void applyManifest()} disabled={!canApply}><Check size={14} /> Apply validated manifest</button>
                </div>
              </section>

              <section className="rounded-2xl border border-slate-800 bg-slate-950/45 p-5" aria-label="Manifest history">
                <div className="flex items-center justify-between gap-3"><div><h2 className="text-base font-semibold text-white">Manifest history</h2><p className="mt-1 text-sm text-slate-400">Applied versions are retained by AIAT for review and rollback.</p></div><span className="text-xs text-slate-500">{history.length} versions</span></div>
                {history.length === 0 ? <p className="mt-4 rounded-xl border border-dashed border-slate-700 px-4 py-6 text-center text-sm text-slate-500">No manifest versions have been applied yet.</p> : <ol className="mt-4 space-y-2">{history.map((version) => <li key={version.id ?? version.manifest_version} className="rounded-xl border border-slate-800 bg-slate-900/50 p-3"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="text-sm font-medium text-slate-200">Version {version.manifest_version} {version.manifest_version === activeManifestVersion && <span className="ml-1 rounded bg-emerald-500/10 px-1.5 py-0.5 text-xs text-emerald-300">Active</span>}</div><div className="mt-1 text-xs text-slate-500">{display(version.status)} · {display(version.source)} · {dateTime(version.compiled_at ?? version.created_at)}</div><div className="mt-1 break-all font-mono text-[11px] text-slate-600">{display(version.digest)}</div></div>{version.manifest_version !== activeManifestVersion && <button type="button" className={buttonClass} onClick={() => { setRollbackVersion(version.manifest_version); setRollbackReason(""); }}><RotateCcw size={14} /> Roll back</button>}</div>{rollbackVersion === version.manifest_version && <div className="mt-3 rounded-lg border border-amber-800/60 bg-amber-950/20 p-3"><label className="block text-xs text-slate-300" htmlFor={`rollback-reason-${version.manifest_version}`}>Reason for rollback to version {version.manifest_version}</label><textarea id={`rollback-reason-${version.manifest_version}`} className={`${fieldClass} mt-1`} rows={2} maxLength={2000} value={rollbackReason} onChange={(event) => setRollbackReason(event.target.value)} placeholder="Explain why this version should be restored" /><div className="mt-2 flex flex-wrap gap-2"><button type="button" className={primaryButtonClass} disabled={!rollbackReason.trim() || rollbackBusy} onClick={() => void rollbackManifest()}>{rollbackBusy ? "Rolling back…" : `Confirm rollback to v${version.manifest_version}`}</button><button type="button" className={buttonClass} onClick={() => setRollbackVersion(null)} disabled={rollbackBusy}>Cancel</button></div></div>}</li>)}</ol>}
              </section>
            </>
          )}
        </div>
      </div>
    </main>
  );
}
