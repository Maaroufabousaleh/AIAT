import { type KnownType } from "./types";

export function getTypeClass(type: string, outbound = false): string {
  if (outbound) return "border-violet-500/50 bg-violet-500/10 hover:border-violet-400/70";
  switch (type) {
    case "TOOL_CALL":   return "border-orange-500/45 bg-orange-500/10 hover:border-orange-400/70";
    case "TOOL_RESULT": return "border-amber-500/40 bg-amber-500/10 hover:border-amber-400/70";
    case "DIRECTIVE":   return "border-blue-500/45 bg-blue-500/10 hover:border-blue-400/70";
    case "REPORT":      return "border-emerald-500/45 bg-emerald-500/10 hover:border-emerald-400/70";
    case "RESPONSE":    return "border-sky-500/45 bg-sky-500/10 hover:border-sky-400/70";
    case "VETO":        return "border-rose-500/50 bg-rose-500/10 hover:border-rose-400/70";
    case "HEARTBEAT":   return "border-slate-700 bg-slate-800/40 hover:border-slate-600";
    case "PROGRESS":    return "border-cyan-500/35 bg-cyan-500/8 hover:border-cyan-400/60";
    default:            return "border-slate-700/80 bg-slate-900/40 hover:border-slate-600";
  }
}

export function getTypeAccent(type: string, outbound = false): string {
  if (outbound) return "text-violet-300";
  switch (type) {
    case "TOOL_CALL":   return "text-orange-300";
    case "TOOL_RESULT": return "text-amber-300";
    case "DIRECTIVE":   return "text-blue-300";
    case "REPORT":      return "text-emerald-300";
    case "RESPONSE":    return "text-sky-300";
    case "VETO":        return "text-rose-300";
    case "HEARTBEAT":   return "text-slate-400";
    case "PROGRESS":    return "text-cyan-300";
    default:            return "text-slate-400";
  }
}

export function getTypeBadgeClass(type: string, outbound = false): string {
  if (outbound) return "bg-violet-500/90 text-white";
  switch (type) {
    case "TOOL_CALL":   return "bg-orange-500/90 text-white";
    case "TOOL_RESULT": return "bg-amber-500 text-slate-950";
    case "DIRECTIVE":   return "bg-blue-500/90 text-white";
    case "REPORT":      return "bg-emerald-500/90 text-white";
    case "RESPONSE":    return "bg-sky-500/90 text-white";
    case "VETO":        return "bg-rose-500/90 text-white";
    case "HEARTBEAT":   return "bg-slate-600 text-slate-100";
    case "PROGRESS":    return "bg-cyan-500/90 text-slate-950";
    default:            return "bg-slate-700 text-slate-200";
  }
}

export function getChipToneForType(
  type: KnownType,
): "blue" | "emerald" | "amber" | "indigo" | "gray" | "violet" {
  switch (type) {
    case "TOOL_CALL":   return "amber";
    case "TOOL_RESULT": return "amber";
    case "DIRECTIVE":   return "blue";
    case "REPORT":      return "emerald";
    case "RESPONSE":    return "blue";
    case "VETO":        return "amber";
    case "OUTBOUND":    return "violet";
    case "PROGRESS":    return "blue";
    default:            return "gray";
  }
}
