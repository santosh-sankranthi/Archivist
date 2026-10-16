import type { GraphState } from "./types";

/** Landing stat strip — every number read live from `/api/state`, never
 *  estimated (DESIGN-BRIEF §P2.1). */
export interface LandingStats {
  nodes: number;
  edges: number;
  commits: number;
  symbols: number;
}

export function deriveLandingStats(state: GraphState): LandingStats {
  const byLabel = new Map(state.nodes.map((row) => [row.label, row.count]));
  return {
    nodes: state.nodes.reduce((sum, row) => sum + row.count, 0),
    edges: state.edges.reduce((sum, row) => sum + row.count, 0),
    commits: byLabel.get("Commit") ?? 0,
    symbols: byLabel.get("Symbol") ?? 0,
  };
}
