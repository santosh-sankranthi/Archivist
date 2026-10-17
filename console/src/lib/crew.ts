/** Crew view logic — pure spine/handoff helpers (unit-tested; the view
 *  stays a thin renderer).
 *
 *  Mirrors the server's honesty rules, never loosens them:
 *  - expected-but-absent roles surface as ghost slots on the spine — a run
 *    that never reached its verifier shows the gap, it is not silently
 *    shortened to what ran;
 *  - recorded HANDOFF edges are matched back to the spine boundary they
 *    connect, so a boundary *without* a recorded edge can be labelled as
 *    a gap (degraded bookkeeping) instead of quietly rendering nothing;
 *  - ordering follows the same rule the server sorted by (timestamp, then
 *    the known crew order), so this never re-opinionates the sequence. */

import { formatInt, stamp } from "./format";
import type { CrewHandoff, CrewPhase, CrewRun } from "./types";

/** Known spine order; unknown roles sort after these by timestamp. */
export const CREW_ROLES = ["planner", "investigator", "verifier"] as const;

export function isKnownRole(role: string): boolean {
  return (CREW_ROLES as readonly string[]).includes(role);
}

export function roleRank(role: string): number {
  const i = (CREW_ROLES as readonly string[]).indexOf(role);
  return i === -1 ? CREW_ROLES.length : i;
}

/** Stable spine order: timestamp first, known crew order as tie-break,
 *  episode id last — the exact sort the server uses. */
export function spineOrder(phases: CrewPhase[]): CrewPhase[] {
  return [...phases].sort((a, b) => {
    const ta = a.ts ?? 0;
    const tb = b.ts ?? 0;
    if (ta !== tb) return ta - tb;
    const ra = roleRank(a.role);
    const rb = roleRank(b.role);
    if (ra !== rb) return ra - rb;
    return a.id < b.id ? -1 : a.id > b.id ? 1 : 0;
  });
}

export interface SpineNode {
  role: string;
  /** null: expected at this slot on the spine but never ran. */
  phase: CrewPhase | null;
}

/** The spine as slots: whenever any known phase exists, the full expected
 *  crew order forms the spine and absent roles keep their slot as ghosts —
 *  the gap is the story. Unknown roles append after, timestamp-ordered;
 *  a run with no known phases shows only what was recorded (no invented
 *  slots at all). */
export function spineNodes(run: CrewRun): SpineNode[] {
  const first = new Map<string, CrewPhase>();
  const unknown: CrewPhase[] = [];
  for (const phase of spineOrder(run.phases)) {
    if (isKnownRole(phase.role)) {
      if (!first.has(phase.role)) first.set(phase.role, phase);
    } else {
      unknown.push(phase);
    }
  }
  const slots: SpineNode[] =
    first.size > 0
      ? CREW_ROLES.map((role) => ({ role, phase: first.get(role) ?? null }))
      : [];
  return [...slots, ...unknown.map((phase) => ({ role: phase.role, phase }))];
}

/** The recorded handoff edge for a spine boundary (from → to), if any.
 *  `null` means no edge was recorded — at a boundary between two phases
 *  that is a gap to show, not the same as "no note". */
export function handoffBetween(
  run: CrewRun,
  from: string,
  to: string,
): CrewHandoff | null {
  return run.handoffs.find((h) => h.from === from && h.to === to) ?? null;
}

/** Run stamps for the header: "Oct 17, 00:15" or "… → …" when the run
 *  spans more than the recorded second. */
export function runWhen(run: CrewRun): string {
  const { started_at: start, ended_at: end } = run;
  if (start === null) return "—";
  if (end === null || end === start) return stamp(start);
  return `${stamp(start)} → ${stamp(end)}`;
}

/** "72ms" / "1.4s" / "2m 05s" — wall time for display. */
export function formatMs(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const m = Math.floor(ms / 60_000);
  const s = Math.round((ms % 60_000) / 1000);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

export interface Chip {
  key: string;
  value: string;
}

/** Per-phase trace chips (kept in a fixed order; nulls skipped, never
 *  rendered as zeros). */
export function phaseChips(phase: CrewPhase): Chip[] {
  const chips: Chip[] = [];
  if (phase.steps !== null) {
    chips.push({ key: "steps", value: formatInt(phase.steps) });
  }
  if (phase.queries !== null) {
    chips.push({ key: "queries", value: formatInt(phase.queries) });
  }
  if (phase.llm_calls !== null) {
    chips.push({ key: "llm calls", value: formatInt(phase.llm_calls) });
  }
  if (phase.duration_ms !== null) {
    chips.push({ key: "time", value: formatMs(phase.duration_ms) });
  }
  return chips;
}
