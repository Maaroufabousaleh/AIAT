"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[dashboard error boundary]", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center h-full min-h-[300px] p-8 gap-4">
      <AlertTriangle size={32} className="text-rose-400" />
      <div className="text-center">
        <p className="text-white font-medium mb-1">Something went wrong</p>
        <p className="text-sm text-slate-500 max-w-sm">
          {error.message || "An unexpected error occurred loading this page."}
        </p>
        {error.digest && (
          <p className="text-xs text-slate-600 mt-2 font-mono">digest: {error.digest}</p>
        )}
      </div>
      <button
        onClick={reset}
        className="flex items-center gap-2 px-4 py-2 bg-slate-800 hover:bg-slate-700
                   border border-slate-700 rounded-lg text-sm text-slate-300 transition-colors"
      >
        <RefreshCw size={14} />
        Try again
      </button>
    </div>
  );
}