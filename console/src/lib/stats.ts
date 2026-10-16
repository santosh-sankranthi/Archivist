import type { GraphState } from "./types";

/** Landing stat strip — every number read live from `/api/state`, never
 *  estimated (DESIGN-BRIEF §P2.1). */
export interface LandingStats {
  nodes: number;
  edges: number;
  commits: number;
  symbols: number;
}

/** Totals + per-label lookup from `/api/state` — one convention for every
 *  surface that prints graph numbers (Landing, Home, TopBar, About), so
 *  they can't drift apart. */
export interface GraphTotals {
  nodes: number;
  edges: number;
  byLabel: ReadonlyMap<string, number>;
}

export function graphTotals(state: GraphState): GraphTotals {
  const byLabel = new Map(state.nodes.map((row) => [row.label, row.count]));
  return {
    nodes: state.nodes.reduce((sum, row) => sum + row.count, 0),
    edges: state.edges.reduce((sum, row) => sum + row.count, 0),
    byLabel,
  };
}

export function deriveLandingStats(state: GraphState): LandingStats {
  const totals = graphTotals(state);
  return {
    nodes: totals.nodes,
    edges: totals.edges,
    commits: totals.byLabel.get("Commit") ?? 0,
    symbols: totals.byLabel.get("Symbol") ?? 0,
  };
}
