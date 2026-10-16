/** Memory view logic — pure geometry, grouping, and chain resolution for the
 *  bi-temporal timeline (unit-tested; the view stays a thin renderer).
 *
 *  Honesty notes (both matter for reviewers):
 *  - The timeline domain is the union of the episode range and every fact
 *    window actually in view — widened, never narrowed, across fetches, so
 *    bars and the scrubber marker don't jump when the mode changes.
 *  - Windows shorter than the display floor (`MIN_WIDTH_PCT`) render at the
 *    floor; the tooltip carries the exact instants. A floor, never a lie. */

import { stampDay } from "./format";
import type {
  MemoryChainFact,
  MemoryEpisode,
  MemoryFact,
  MemoryResponse,
} from "./types";

export interface Domain {
  t0: number;
  t1: number;
}

export interface Span {
  startPct: number;
  endPct: number;
  open: boolean;
}

/** Display floor (percent of the domain) so an instant-length window (e.g. a
 *  fact superseded in the same second) still renders as a visible tick. */
export const MIN_WIDTH_PCT = 1.4;

type WindowedFact = {
  valid_at: number | null;
  invalid_at: number | null;
  created_at?: number | null;
};

export function widenDomain(a: Domain | null, b: Domain | null): Domain | null {
  if (!a) return b;
  if (!b) return a;
  return { t0: Math.min(a.t0, b.t0), t1: Math.max(a.t1, b.t1) };
}

/** Domain across one response: union of `range` and every fact window in
 *  view (chain neighbours included); extended to `now` while any shown fact
 *  is open, because "still true" reaches the present. */
export function memoryDomain(resp: MemoryResponse, now = Date.now() / 1000): Domain {
  const points: number[] = [];
  const add = (v: number | null | undefined) => {
    if (typeof v === "number" && Number.isFinite(v)) points.push(v);
  };
  add(resp.range.earliest);
  add(resp.range.latest);
  let open = false;
  for (const fact of resp.facts as WindowedFact[]) {
    add(fact.valid_at ?? fact.created_at);
    if (fact.invalid_at === null) open = true;
    else add(fact.invalid_at);
  }
  for (const fact of resp.chain_facts as WindowedFact[]) {
    add(fact.valid_at);
    add(fact.invalid_at);
  }
  let t0 = points.length ? Math.min(...points) : 0;
  let t1 = points.length ? Math.max(...points) : t0 + 1;
  if (open) t1 = Math.max(t1, now);
  if (t1 <= t0) t1 = t0 + 1;
  return { t0, t1 };
}

/** Position of a timestamp within the domain, clamped to [0, 100]. */
export function pct(ts: number, domain: Domain): number {
  const p = ((ts - domain.t0) / (domain.t1 - domain.t0)) * 100;
  return Math.min(100, Math.max(0, p));
}

/** A fact's valid window within the domain. Open facts run to the right
 *  edge ("open" = no invalid_at); closed windows take the display floor. */
export function factSpan(fact: WindowedFact, domain: Domain): Span {
  const start = fact.valid_at ?? fact.created_at ?? domain.t0;
  const open = fact.invalid_at === null;
  const end = open ? domain.t1 : (fact.invalid_at as number);
  const startPct = pct(start, domain);
  const endPct = Math.max(pct(end, domain), startPct + MIN_WIDTH_PCT);
  return { startPct, endPct: Math.min(endPct, 100), open };
}

/** Exact window label for tooltips: "Oct 16, 21:53 → open" style is built
 *  by the caller; this returns the two instants (nulls intact). */
export function windowInstants(fact: WindowedFact): {
  from: number | null;
  to: number | null;
} {
  return { from: fact.valid_at ?? fact.created_at ?? null, to: fact.invalid_at };
}

export interface ChainLink {
  id: string;
  text: string;
  validAt: number | null;
  invalidAt: number | null;
  /** "older" = claim this fact superseded · "newer" = claim that superseded it. */
  side: "older" | "newer";
}

/** Lookup for chain neighbours: shown facts first, hidden chain facts fill
 *  the gaps (server sends their minimal fields for exactly this). */
export function memoryIndex(resp: MemoryResponse): Map<string, MemoryFact | MemoryChainFact> {
  const map = new Map<string, MemoryFact | MemoryChainFact>();
  for (const fact of resp.chain_facts) map.set(fact.id, fact);
  for (const fact of resp.facts) map.set(fact.id, fact);
  return map;
}

export function chainLinks(
  fact: MemoryFact,
  index: Map<string, MemoryFact | MemoryChainFact>,
): ChainLink[] {
  const links: ChainLink[] = [];
  for (const id of fact.supersedes) {
    const other = index.get(id);
    links.push({
      id,
      text: other?.text ?? id,
      validAt: other?.valid_at ?? null,
      invalidAt: other?.invalid_at ?? null,
      side: "older",
    });
  }
  for (const id of fact.superseded_by) {
    const other = index.get(id);
    links.push({
      id,
      text: other?.text ?? id,
      validAt: other?.valid_at ?? null,
      invalidAt: other?.invalid_at ?? null,
      side: "newer",
    });
  }
  return links.sort((a, b) => (a.validAt ?? 0) - (b.validAt ?? 0));
}

export interface EpisodeGroup {
  day: string;
  episodes: MemoryEpisode[];
}

/** Group a newest-first episode list into contiguous day runs. */
export function groupEpisodes(episodes: MemoryEpisode[]): EpisodeGroup[] {
  const groups: EpisodeGroup[] = [];
  for (const episode of episodes) {
    const day = stampDay(episode.ts);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.episodes.push(episode);
    else groups.push({ day, episodes: [episode] });
  }
  return groups;
}

/** Subset of episode props the view surfaces (mono chips), in render order.
 *  Unknown future props stay out rather than leaking as "[object Object]". */
export function episodeChips(episode: MemoryEpisode): Array<{ key: string; value: string }> {
  const chips: Array<{ key: string; value: string }> = [];
  const { props } = episode;
  const push = (key: string, value: unknown) => {
    if (typeof value === "string" && value) chips.push({ key, value });
    else if (typeof value === "number") chips.push({ key, value: String(value) });
    else if (typeof value === "boolean") chips.push({ key, value: value ? "yes" : "no" });
  };
  push("model", props["model"]);
  push("steps", props["steps"]);
  push("queries", props["queries"]);
  push("llm calls", props["llm_calls"]);
  if (props["partial"] === true) push("partial", true);
  return chips;
}
