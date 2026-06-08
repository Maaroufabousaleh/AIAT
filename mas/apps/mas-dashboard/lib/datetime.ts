/**
 * lib/datetime.ts
 *
 * Single source of truth for the dashboard's display timezone.
 *
 * Timezone contract
 * ----------------
 * Every backend system (orchestrator-api, message-router, Redis, agents) emits
 * timestamps as UTC ISO-8601 strings (e.g. "2024-06-07T14:30:00Z") or Unix
 * epoch values.  The dashboard never assumes local time anywhere in the stack.
 *
 * Display timezone
 * ---------------
 * The operator-facing UI always shows times in `DISPLAY_TZ`.  This is
 * configurable via the `NEXT_PUBLIC_DISPLAY_TZ` environment variable (an IANA
 * zone name, e.g. "America/New_York", "Europe/London", "Asia/Tokyo").
 * Defaults to "America/New_York" — change this to match the operator's region.
 *
 * Note: "America/New_York" auto-switches between EDT (UTC-4) and EST (UTC-5)
 * with daylight saving, so log timestamps always match the wall-clock time on
 * the East Coast.  A fixed UTC-offset label would be wrong half the year.
 *
 * Type coercion
 * ------------
 * JavaScript `Date` stores all times as a UTC milliseconds-since-epoch number.
 * When the caller passes a Unix epoch value:
 *   • seconds  (Prometheus API, Redis XREADGROUP IDs)  → multiply × 1000
 *   • ms      (JavaScript native)                  → use as-is
 * `formatInTz` handles both by normalising to ms before formatting.
 */
import { formatInTimeZone } from "date-fns-tz";

/**
 * IANA timezone used for every timestamp shown in the dashboard.
 * Operators in different regions should set `NEXT_PUBLIC_DISPLAY_TZ`
 * to match their location (e.g. "Europe/Berlin", "Asia/Dubai").
 *
 * Fallback chain:
 *   1. `NEXT_PUBLIC_DISPLAY_TZ` env var (public, read on the client)
 *   2. `DISPLAY_TZ` env var     (server-only fallback)
 *   3. "America/New_York"       (safe default for US-based ops)
 */
function resolveDisplayTz(): string {
  return (
    process.env.NEXT_PUBLIC_DISPLAY_TZ ??
    process.env.DISPLAY_TZ ??
    "America/New_York"
  );
}

/** IANA zone used for every timestamp shown in the dashboard. */
export const DISPLAY_TZ = resolveDisplayTz();

/**
 * Short label suitable for inline use next to timestamps ("EDT", "EST", "BST").
 * Regenerated at module load so it reflects the current (summer/winter)
 * offset of `DISPLAY_TZ`.
 */
export const DISPLAY_TZ_LABEL = formatInTimeZone(new Date(), DISPLAY_TZ, "zzz");

/**
 * Threshold (in seconds) below which a numeric value is treated as a Unix
 * epoch in seconds rather than milliseconds.  Values above 1e12 (≈ Sat
 * Sep 20 2001) are unambiguously ms; values below 1e10 (≈ Sat Sep 20 2001
 * in seconds) are unambiguously seconds.  We use 1e11 as the boundary to
 * safely cover Prometheus seconds values without false-positives on ms values.
 */
const MS_EPOCH_THRESHOLD = 1e11; // 100 billion = ~513 years in ms

/**
 * Normalise a value that may be a Unix epoch in seconds, a Unix epoch in
 * milliseconds, or a `Date` object to a JavaScript `Date`.
 *
 * @param value  A `Date`, epoch ms, epoch seconds, or ISO-8601 string.
 */
function toDate(value: Date | number | string): Date {
  if (value instanceof Date) return value;
  if (typeof value === "number") {
    // Unix epoch in seconds → convert to ms
    return new Date(value < MS_EPOCH_THRESHOLD ? value * 1000 : value);
  }
  // ISO-8601 string
  return new Date(value);
}

/**
 * Format a date in the dashboard's display timezone.
 *
 * @param value  A `Date`, an epoch ms number, an epoch seconds number,
 *               or an ISO-8601 string.
 * @param pattern  date-fns format pattern (e.g. "yyyy-MM-dd HH:mm:ss").
 *
 * Examples:
 *   formatInTz(Date.now(), "HH:mm:ss")           → "10:30:45"  (display TZ)
 *   formatInTz(1718000000, "HH:mm:ss")           → "14:13:20"  (Prometheus epoch-sec)
 *   formatInTz("2024-06-07T14:30:00Z", "HH:mm")  → "10:30"     (UTC ISO → display TZ)
 */
export function formatInTz(
  value: Date | number | string,
  pattern: string,
): string {
  return formatInTimeZone(toDate(value), DISPLAY_TZ, pattern);
}

/**
 * Locale-formatted absolute datetime in the dashboard's display timezone.
 * Use for one-off displays that benefit from en-US conventions
 * (e.g. credential `last_used_at`).
 *
 * @param value  A `Date`, an epoch ms number, an epoch seconds number,
 *               or an ISO-8601 string.
 * @param options  Additional `Intl.DateTimeFormat` options.
 */
export function formatLocaleInTz(
  value: Date | number | string,
  options: Intl.DateTimeFormatOptions = {},
): string {
  return toDate(value).toLocaleString("en-US", { timeZone: DISPLAY_TZ, ...options });
}

/**
 * Format a UTC label suffix for display next to timestamps.
 * Use this when you want to show the raw UTC time alongside the
 * display-timezone time (e.g. "10:30 EDT (14:30 UTC)").
 */
export function formatInTzWithUtc(
  value: Date | number | string,
  pattern: string,
): { display: string; utc: string } {
  const d = toDate(value);
  return {
    display: formatInTimeZone(d, DISPLAY_TZ, pattern),
    utc: formatInTimeZone(d, "UTC", pattern),
  };
}
