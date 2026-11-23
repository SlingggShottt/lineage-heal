const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const RELATIVE_LIMIT_DAYS = 30;

const relative = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/**
 * A time as people say it: "3 hours ago", "yesterday". Older than a month, or in the future, it
 * falls back to the date, because "5 months ago" hides which day it was.
 */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (seconds < 0 || seconds > RELATIVE_LIMIT_DAYS * DAY) {
    return then.toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
  }
  if (seconds < MINUTE) {
    return "just now";
  }
  if (seconds < HOUR) {
    return relative.format(-Math.floor(seconds / MINUTE), "minute");
  }
  if (seconds < DAY) {
    return relative.format(-Math.floor(seconds / HOUR), "hour");
  }
  return relative.format(-Math.floor(seconds / DAY), "day");
}

/** The exact local date and time, for a tooltip that backs up a relative one. */
export function exactTime(iso: string): string {
  return new Date(iso).toLocaleString("en", { dateStyle: "medium", timeStyle: "short" });
}

/** A 0 to 1 confidence as a whole percentage, or a dash when there is none. */
export function formatConfidence(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${String(Math.round(value * 100))}%`;
}
