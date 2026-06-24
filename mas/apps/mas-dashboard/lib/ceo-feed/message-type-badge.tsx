"use client";

import { clsx } from "clsx";
import { getTypeBadgeClass } from "./styling";

export function TypeBadge({ type, outbound = false }: { type: string; outbound?: boolean }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center px-1.5 py-0.5 rounded text-xxs font-bold tracking-wide",
        getTypeBadgeClass(type, outbound),
      )}
    >
      {type}
    </span>
  );
}
