"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export function SeedDefaultCompanyButton() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function seed() {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/system/seed-default-company", { method: "POST" });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        setError(payload.error ?? payload.detail ?? "Seed failed");
        return;
      }
      router.refresh();
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      {error && <span className="text-xs text-red-400">{error}</span>}
      <button
        onClick={seed}
        disabled={loading}
        className="px-3 py-1.5 rounded border border-blue-700 bg-blue-600/20 text-blue-200 text-xs hover:bg-blue-600/30 disabled:opacity-50"
      >
        {loading ? "Seeding..." : "Seed Default AIAT"}
      </button>
    </div>
  );
}
