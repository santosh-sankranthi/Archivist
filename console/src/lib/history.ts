/** Time machine view logic — pure scaling, selection, and query math for
 *  the history skyline (unit-tested; the view stays a thin renderer).
 *
 *  Honesty notes:
 *  - Bar heights use a square-root scale (`barHeightPct`) so the long tail
 *    stays visible next to peak months; the tooltip and readout always
 *    carry the exact commit count — the height is the glance, the number
 *    is the record.
 *  - A selection is a run of whole buckets, and bucket epoch bounds tile
 *    exactly (`t1 == next t0`, guaranteed server-side), so the window we
 *    ask for is precisely the months the band covers — no calendar math
 *    re-derived client-side. */

import { formatInt } from "./format";
import type { HistoryBucket } from "./types";

export interface BucketRange {
  a: number;
  b: number;
}

/** Max commits across the buckets (≥1, so scaling never divides by zero). */
export function skylineMax(buckets: HistoryBucket[]): number {
  let max = 1;
  for (const bucket of buckets) max = Math.max(max, bucket.commits);
  return max;
}

/** Height in percent of the skyline for a commit count. Zero stays zero
 *  (the view renders those as a baseline tick); non-zero takes a
 *  square-root scale with a small floor so quiet months stay visible. */
export function barHeightPct(commits: number, max: number): number {
  if (commits <= 0) return 0;
  return Math.max(6, Math.sqrt(commits / max) * 100);
}

/** Order a from/to bucket pair into [a, b] — drag direction must not matter. */
export function clampRange(a: number, b: number): BucketRange {
  return { a: Math.min(a, b), b: Math.max(a, b) };
}

/** Epoch window `[from, to)` covering buckets a..b inclusive; null when the
 *  skyline is empty or the indices fall outside it. */
export function windowFromSelection(
  buckets: HistoryBucket[],
  a: number,
  b: number,
): { from: number; to: number } | null {
  if (buckets.length === 0) return null;
  const range = clampRange(a, b);
  const first = buckets[range.a];
  const last = buckets[range.b];
  if (!first || !last) return null;
  return { from: first.t0, to: last.t1 };
}

/** Summarize a selected run: "2010-04 → 2011-06 · 15 months · 1,254 commits". */
export function selectionLabel(
  buckets: HistoryBucket[],
  a: number,
  b: number,
): string {
  const range = clampRange(a, b);
  const months = range.b - range.a + 1;
  let commits = 0;
  for (let i = range.a; i <= range.b; i += 1) commits += buckets[i]?.commits ?? 0;
  const from = buckets[range.a]?.label ?? "—";
  const to = buckets[range.b]?.label ?? "—";
  return `${from} → ${to} · ${months} month${months === 1 ? "" : "s"} · ${formatInt(commits)} commits`;
}

/** Exact tooltip for one bucket — the height is decorative, this is not. */
export function bucketTitle(bucket: HistoryBucket): string {
  return (
    `${bucket.label} · ${formatInt(bucket.commits)} commits · ` +
    `${formatInt(bucket.touches)} files touched · ` +
    `+${formatInt(bucket.added)} / −${formatInt(bucket.removed)}`
  );
}

/** Year ticks for the axis: the first bucket of each calendar year. */
export function yearTicks(
  buckets: HistoryBucket[],
): Array<{ index: number; label: string }> {
  const ticks: Array<{ index: number; label: string }> = [];
  let year = "";
  buckets.forEach((bucket, index) => {
    const label = bucket.label.slice(0, 4);
    if (label !== year) {
      ticks.push({ index, label });
      year = label;
    }
  });
  return ticks;
}

/** Read-only endpoint path for a scope + optional window. */
export function historyPath(
  file: string | null,
  window?: { from: number; to: number } | null,
): string {
  const params = new URLSearchParams();
  if (file) params.set("file", file);
  if (window) {
    params.set("from", String(window.from));
    params.set("to", String(window.to));
  }
  const qs = params.toString();
  return `/api/graph/history${qs ? `?${qs}` : ""}`;
}

/** "+1,327 / −294" — one string; callers style it. */
export function deltaLabel(added: number, removed: number): string {
  return `+${formatInt(added)} / −${formatInt(removed)}`;
}
