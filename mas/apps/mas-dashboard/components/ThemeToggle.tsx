"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme, type ThemeMode } from "@/components/ThemeProvider";

const THEME_LABELS: Record<ThemeMode, string> = {
  system: "System",
  light: "Light",
  dark: "Dark",
};

const THEME_ICONS = {
  system: Monitor,
  light: Sun,
  dark: Moon,
} satisfies Record<ThemeMode, typeof Monitor>;

const NEXT_THEME: Record<ThemeMode, ThemeMode> = {
  system: "light",
  light: "dark",
  dark: "system",
};

export default function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { theme, resolvedTheme, mounted, setTheme } = useTheme();
  const Icon = THEME_ICONS[theme];
  const currentLabel = mounted ? THEME_LABELS[theme] : "System";

  if (compact) {
    return (
      <button
        type="button"
        onClick={() => setTheme(NEXT_THEME[theme])}
        aria-label={`Theme preference: ${currentLabel}. Activate to use ${THEME_LABELS[NEXT_THEME[theme]]}.`}
        title={`Theme: ${currentLabel}`}
        data-testid="theme-toggle-compact"
        className="inline-flex h-11 w-11 items-center justify-center rounded-lg border border-[var(--aiat-border)] bg-[var(--aiat-surface)] text-[var(--aiat-text-muted)] transition-colors hover:text-[var(--aiat-text)]"
      >
        <Icon size={16} aria-hidden="true" />
        <span className="sr-only">{currentLabel} theme</span>
      </button>
    );
  }

  return (
    <label className="flex min-h-11 items-center justify-between gap-3 rounded-lg border border-[var(--aiat-border)] bg-[var(--aiat-surface)] px-3 py-2 text-xs text-[var(--aiat-text-muted)]">
      <span className="flex items-center gap-2">
        <Icon size={14} aria-hidden="true" />
        <span>Theme</span>
      </span>
      <select
        value={theme}
        onChange={(event) => setTheme(event.target.value as ThemeMode)}
        aria-label="Theme preference"
        data-testid="theme-preference"
        className="min-h-8 rounded-md border border-[var(--aiat-border-strong)] bg-[var(--aiat-surface-raised)] px-2 text-xs font-medium text-[var(--aiat-text)] focus-visible:outline-none"
      >
        {Object.entries(THEME_LABELS).map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <span className="sr-only">Active palette: {resolvedTheme}</span>
    </label>
  );
}
