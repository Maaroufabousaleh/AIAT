"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Eye, EyeOff } from "lucide-react";
import { clsx } from "clsx";
import { ErrorBanner } from "@/components/ui/ErrorBanner";

// App version, surfaced for support and operator awareness.
const APP_VERSION = "0.1.0";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        router.push("/");
        router.refresh();
      } else {
        const data = await res.json();
        setError(data.error ?? "Login failed");
      }
    } catch {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main
      className="min-h-screen bg-[var(--aiat-bg)] text-slate-100 lg:grid lg:grid-cols-[minmax(0,1fr)_460px]"
      aria-label="AIAT MAS sign-in"
      aria-busy={loading}
    >
      <section
        className="relative hidden overflow-hidden lg:block"
        style={{
          backgroundImage:
            "linear-gradient(90deg, rgba(7,10,15,0.9) 0%, rgba(7,10,15,0.56) 50%, rgba(7,10,15,0.2) 100%), url('/mission-control.jpg')",
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_30%_20%,rgba(37,99,235,0.28),transparent_28rem)]" />
        <div className="relative flex min-h-screen flex-col justify-between p-12">
          <div className="inline-flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 via-cyan-500 to-emerald-400 text-sm font-bold text-white shadow-lg shadow-blue-500/25">
              M
            </div>
            <div>
              <div className="text-lg font-semibold text-white">AIAT MAS</div>
              <div className="text-xs text-slate-300">Governed multi-agent control plane</div>
            </div>
          </div>
          <div className="max-w-2xl">
            <div className="mb-4 inline-flex rounded-full border border-cyan-400/25 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-cyan-100">
              Operator console
            </div>
            <h1 className="text-5xl font-bold leading-tight tracking-tight text-white">
              Inspect agents, approvals, tools, and runtime health from one command surface.
            </h1>
            <p className="mt-5 max-w-xl text-base leading-7 text-slate-300">
              The dashboard is the human-facing proof layer for project progress, worker readiness,
              credentials, dead letters, metrics, streams, and system control.
            </p>
            <div
              className="mt-8 h-44 max-w-xl overflow-hidden rounded-2xl border border-cyan-400/20 bg-slate-900 shadow-2xl shadow-black/40"
              style={{
                backgroundImage:
                  "linear-gradient(0deg, rgba(7,10,15,0.16), rgba(7,10,15,0.16)), url('/mission-control.jpg')",
                backgroundSize: "cover",
                backgroundPosition: "center",
              }}
              aria-label="Mission control operations room preview"
            />
          </div>
          <div className="text-xs text-slate-500">
            Background image: NASA STS-1 mission control room, Wikimedia Commons.
          </div>
        </div>
      </section>

      <section className="flex min-h-screen items-center justify-center px-6 py-10" aria-label="Operator sign-in">
        <div className="w-full max-w-sm">
        {/* Logo / Title */}
        <div className="mb-8 lg:hidden">
          <div className="inline-flex items-center gap-2 mb-2">
            <span className="text-2xl font-bold text-white">AIAT</span>
            <span className="text-2xl font-light text-slate-400">MAS</span>
          </div>
          <p className="text-slate-500 text-sm">Multi-Agent System Monitor</p>
        </div>

        {/* Card */}
        <form
          onSubmit={handleSubmit}
          className="dashboard-surface-strong p-8"
          aria-label="Sign-in form"
          noValidate
        >
          <div className="mb-6">
            <h1 className="text-2xl font-semibold text-slate-100">Sign in</h1>
            <p className="mt-1 text-sm text-slate-500">Use your operator credentials to open the dashboard.</p>
          </div>

          <div className="space-y-4">
            <div>
              <label htmlFor="username" className="block text-sm font-medium text-slate-400 mb-1.5">
                Username
              </label>
              <input
                id="username"
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                aria-required="true"
                className="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2
                           text-slate-100 placeholder-slate-600 text-sm
                           focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                           transition-colors hover:border-slate-600"
                placeholder="admin"
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label htmlFor="password" className="block text-sm font-medium text-slate-400">
                  Password
                </label>
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className="min-h-11 min-w-11 text-xs font-medium text-slate-500 hover:text-slate-300
                             focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70
                             rounded px-1.5 py-0.5 transition-colors"
                >
                  {showPassword ? "Hide" : "Show"}
                </button>
              </div>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  required
                  aria-required="true"
                  className={clsx(
                    "w-full bg-slate-950 border border-slate-700 rounded-lg pl-3 pr-10 py-2",
                    "text-slate-100 placeholder-slate-600 text-sm",
                    "focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent",
                    "transition-colors hover:border-slate-600"
                  )}
                  placeholder="••••••••"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  tabIndex={-1}
                  className="absolute inset-y-0 right-0 flex min-h-11 min-w-11 items-center justify-center
                             text-slate-500 hover:text-slate-200
                             focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70
                             rounded-r-lg transition-colors"
                >
                  {showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
                </button>
              </div>
            </div>
          </div>

          {/* Forgot password link — placeholder anchor; wire to a real route when available. */}
          <div className="mt-3 flex justify-end">
            <a
              href="#forgot-password"
              className="text-xs font-medium text-slate-400 hover:text-slate-200
                         focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70
                         rounded px-1 py-0.5 transition-colors"
            >
              Forgot password?
            </a>
          </div>

          {error && (
            <div className="mt-4" role="alert">
              <ErrorBanner tone="error" title="Sign-in failed">
                {error}
              </ErrorBanner>
            </div>
          )}

          <p className="sr-only" role="status" aria-live="polite">
            {loading ? "Signing in…" : "Ready to sign in"}
          </p>

          <button
            type="submit"
            disabled={loading}
            aria-busy={loading}
            className="mt-6 w-full bg-blue-600 hover:bg-blue-500 active:bg-blue-700
                       disabled:bg-blue-900 disabled:cursor-not-allowed
                       min-h-11 text-white font-medium rounded-lg px-4 py-2.5 text-sm
                       transition-colors duration-150
                       focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>

        {/* Version footer — useful for support tickets and operator awareness. */}
        <div className="mt-6 text-center text-xxs text-slate-600" aria-label="Application version">
          AIAT MAS v{APP_VERSION}
        </div>
        </div>
      </section>
    </main>
  );
}
