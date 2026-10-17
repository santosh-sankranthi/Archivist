/** Blast-radius view logic — pure query/display math for the impact cone
 *  (unit-tested; the view stays a thin renderer).
 *
 *  The server answers "what breaks if this changes?" as one row per
 *  affected symbol at its shortest distance, each with the call chain that
 *  reaches the target. This module only orders, shortens, and scales what
 *  the server returned — it never invents a caller, a distance, or a count.
 */

import type { ImpactFile, ImpactSymbol } from "./types";

/** Depth choices the server accepts (1..MAX_IMPACT_DEPTH). */
export const DEPTHS = [1, 2, 3, 4, 5, 6] as const;

/** Symbols shown per ring before the "more" control — a display cap only;
 *  the ring header still states the ring's exact size. */
export const RING_SHOW = 9;

export interface ImpactOptions {
  symbol: string;
  file?: string | null;
  depth?: number;
  includeWeak?: boolean;
  limit?: number;
}

/** Read-only endpoint path for one blast-radius question. */
export function impactPath(options: ImpactOptions): string {
  const params = new URLSearchParams({ symbol: options.symbol });
  if (options.file) params.set("file", options.file);
  if (options.depth !== undefined) params.set("depth", String(options.depth));
  if (options.includeWeak) params.set("include_weak", "true");
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  return `/api/graph/impact?${params.toString()}`;
}

/** A constrained depth from a URL param (server re-validates regardless). */
export function parseDepth(raw: string | null): number {
  const value = raw === null ? NaN : Number(raw);
  if (!Number.isInteger(value) || value < 1 || value > 6) return 4;
  return value;
}

/** Last two path segments — enough to place a file without the full path. */
export function fileTail(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || path;
}

/** A qualified name's terminal segment: `flask.helpers.url_for` → `url_for`. */
export function shortName(qualname: string): string {
  const parts = qualname.split(".");
  return parts[parts.length - 1] || qualname;
}

/** Chain label in call order: caller → … → target (the chain the server
 *  returned; every hop is a real CALLS edge). */
export function chainLabel(chain: Array<{ file: string; qualname: string }>): string {
  return chain.map((ref) => shortName(ref.qualname)).join(" → ");
}

/** Chain as file-qualified segments for the tooltip: ambiguous `helper`
 *  names stay distinguishable. */
export function chainTitle(chain: Array<{ file: string; qualname: string }>): string {
  return chain.map((ref) => `${ref.qualname} (${ref.file})`).join(" → ");
}

/** Visual closeness of a ring: 1 at the direct callers, fading to 0.4 at
 *  the deepest ring. Purely presentational — the numeric hops are the
 *  record and are always printed. */
export function ringStrength(hops: number, maxHops: number): number {
  if (maxHops <= 1) return 1;
  return 1 - 0.6 * ((hops - 1) / (maxHops - 1));
}

/** Split the server's file rollup into source and test files, keeping the
 *  server's order (min hops, then path) inside each side. */
export interface FileRollup {
  source: ImpactFile[];
  tests: ImpactFile[];
  sourceSymbols: number;
  testSymbols: number;
}

export function rollupFiles(files: ImpactFile[]): FileRollup {
  const source = files.filter((file) => !file.tests);
  const tests = files.filter((file) => file.tests);
  return {
    source,
    tests,
    sourceSymbols: source.reduce((sum, file) => sum + file.symbols, 0),
    testSymbols: tests.reduce((sum, file) => sum + file.symbols, 0),
  };
}

/** Which symbols of a ring are visible, and how many the display cap hides.
 *  Expanding is per ring and resets on a new answer (view owns the state). */
export function visibleRing(
  symbols: ImpactSymbol[],
  expanded: boolean,
): { shown: ImpactSymbol[]; hidden: number } {
  if (expanded) return { shown: symbols, hidden: 0 };
  return {
    shown: symbols.slice(0, RING_SHOW),
    hidden: Math.max(0, symbols.length - RING_SHOW),
  };
}

/** "1 hop" / "3 hops" — ring adjectives live in the view. */
export function hopLabel(hops: number): string {
  return `${hops} hop${hops === 1 ? "" : "s"}`;
}
