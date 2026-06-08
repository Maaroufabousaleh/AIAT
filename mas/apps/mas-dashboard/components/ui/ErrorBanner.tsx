"use client";

import { clsx } from "clsx";
import { AlertTriangle, Info, XCircle, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type ErrorTone = "error" | "warning" | "info";

export interface ErrorBannerProps {
  /** Visual tone. Defaults to "error" (red). Use "warning" for non-fatal issues, "info" for notice. */
  tone?: ErrorTone;
  /** Optional title — defaults to a tone-appropriate label. */
  title?: string;
  /** Body content. If a string, the body is rendered inline. */
  children?: ReactNode;
  /** Optional className for the wrapping element. */
  className?: string;
  /** Optional icon override. */
  icon?: LucideIcon;
  /** Right-side action area (e.g. a retry button). */
  action?: ReactNode;
}

const TONE_STYLES: Record<ErrorTone, { container: string; icon: string; iconWrap: string; title: string; defaultTitle: string; Icon: LucideIcon }> = {
  error: {
    container: "border-red-800/70 bg-red-950/35 text-red-200",
    icon: "text-red-400",
    iconWrap: "bg-red-900/30",
    title: "text-red-200",
    defaultTitle: "Something went wrong",
    Icon: XCircle,
  },
  warning: {
    container: "border-amber-800/70 bg-amber-950/35 text-amber-200",
    icon: "text-amber-400",
    iconWrap: "bg-amber-900/30",
    title: "text-amber-200",
    defaultTitle: "Heads up",
    Icon: AlertTriangle,
  },
  info: {
    container: "border-blue-800/70 bg-blue-950/35 text-blue-200",
    icon: "text-blue-400",
    iconWrap: "bg-blue-900/30",
    title: "text-blue-200",
    defaultTitle: "Note",
    Icon: Info,
  },
};

/**
 * Consistent, non-alarming banner for surfacing errors, warnings, and info.
 * Use {@link ErrorBanner} instead of an ad-hoc `bg-red-900/30 border-red-700`
 * div — this version is calmer and exposes a tone for the severity.
 */
export function ErrorBanner({
  tone = "error",
  title,
  children,
  className,
  icon,
  action,
}: ErrorBannerProps) {
  const cfg = TONE_STYLES[tone];
  const Icon = icon ?? cfg.Icon;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={clsx(
        "flex items-start gap-3 p-4 rounded-xl border shadow-sm shadow-black/10",
        cfg.container,
        className
      )}
    >
      <div className={clsx("flex-shrink-0 p-1.5 rounded-md", cfg.iconWrap)}>
        <Icon size={16} className={cfg.icon} />
      </div>
      <div className="flex-1 min-w-0">
        {title !== "" && (
          <div className={clsx("text-sm font-medium", cfg.title)}>
            {title ?? cfg.defaultTitle}
          </div>
        )}
        {children && <div className="text-xs mt-0.5 opacity-90">{children}</div>}
      </div>
      {action && <div className="flex-shrink-0">{action}</div>}
    </div>
  );
}
