/** Display formatting — pure and deterministic (vitest-covered). */

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;
const WEEK = 604800;

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** 8812 -> "8,812" */
export function formatInt(n: number): string {
  return n.toLocaleString("en-US");
}

/** Full sha -> short sha for display. */
export function shortSha(sha: string, len = 7): string {
  return sha.slice(0, len);
}

/** Unix seconds -> "just now | 5m ago | 3h ago | 2d ago | 6w ago | Apr 2023". */
export function relTime(ts: number, now = Date.now() / 1000): string {
  const delta = Math.max(0, now - ts);
  if (delta < 45) return "just now";
  if (delta < HOUR) return `${Math.round(delta / MINUTE)}m ago`;
  if (delta < DAY) return `${Math.round(delta / HOUR)}h ago`;
  if (delta < 14 * DAY) return `${Math.round(delta / DAY)}d ago`;
  if (delta < 8 * WEEK) return `${Math.round(delta / WEEK)}w ago`;
  const date = new Date(ts * 1000);
  return `${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/** Keep text within `n` chars, ellipsis included; never mid-grapheme for ASCII. */
export function clampText(text: string, n: number): string {
  if (text.length <= n) return text;
  return `${text.slice(0, Math.max(0, n - 1))}…`;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Unix seconds -> "Oct 16, 21:53" (UTC). Deterministic on purpose — the
 *  memory timeline and its screenshots must not depend on the viewer's TZ. */
export function stamp(ts: number): string {
  const d = new Date(ts * 1000);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
}

/** Unix seconds -> "Oct 16" (UTC) — day grouping for the timeline. */
export function stampDay(ts: number): string {
  const d = new Date(ts * 1000);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** Unix seconds -> "Apr 1, 2010" (UTC) — date-only, year included; window
 *  bounds cross years, "Oct 16" alone would not say which one. */
export function stampDate(ts: number): string {
  const d = new Date(ts * 1000);
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
}
