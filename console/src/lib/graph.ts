/** Pure graph helpers for the Explorer: element conversion, labels,
 *  spotlight sets, weak-edge filtering. No DOM, no cytoscape runtime —
 *  unit-testable against fixture payloads. */

import type { ElementDefinition } from "cytoscape";

import type { GraphEdge, GraphNode, GraphPayload } from "./types";

export type NodeKind = "symbol" | "file" | "module" | "other";

export function nodeKind(node: GraphNode): NodeKind {
  if (node.type === "Symbol") return "symbol";
  if (node.type === "File") return "file";
  if (node.type === "Module") return "module";
  return "other";
}

/** Canvas label: short and legible. Symbol → name; File → path; Module → name. */
export function labelFor(node: GraphNode): string {
  const p = node.props;
  switch (node.type) {
    case "Symbol":
      return typeof p.name === "string" && p.name
        ? p.name
        : String(p.qualname ?? node.id);
    case "File":
      return typeof p.path === "string" ? p.path : String(node.id);
    case "Module":
      return typeof p.name === "string" ? p.name : String(node.id);
    default:
      return String(p.name ?? p.path ?? p.sha ?? node.id);
  }
}

/** Hover title: the full coordinates, no truncation. */
export function titleFor(node: GraphNode): string {
  const p = node.props;
  switch (node.type) {
    case "Symbol":
      return `${String(p.qualname ?? "?")} — ${String(p.file ?? "?")}:${String(p.lineno ?? "?")}`;
    case "File":
      return String(p.path ?? "?");
    case "Module":
      return `module ${String(p.name ?? "?")}`;
    default:
      return String(p.name ?? p.path ?? p.sha ?? node.id);
  }
}

/** One plain question the Ask view can answer about this node. */
export function questionFor(node: GraphNode): string {
  const p = node.props;
  switch (node.type) {
    case "Symbol":
      return (
        `Tell me about ${String(p.qualname)} in ${String(p.file)} — who calls it, ` +
        `what it calls, and how it changed recently.`
      );
    case "File":
      return `Walk me through ${String(p.path)} — its symbols, imports, and recent history.`;
    case "Module":
      return `Who imports ${String(p.name)}, and what would break if it changed?`;
    default:
      return `Tell me about ${labelFor(node)}.`;
  }
}

export interface Spotlight {
  nodes: Set<number>;
  edges: Set<number>;
}

/** Selection spotlight: the node, its neighbours, and the edges between them. */
export function neighborhood(payload: GraphPayload, nodeId: number): Spotlight {
  const nodes = new Set<number>([nodeId]);
  const edges = new Set<number>();
  for (const edge of payload.edges) {
    if (edge.source === nodeId || edge.target === nodeId) {
      edges.add(edge.id);
      nodes.add(edge.source);
      nodes.add(edge.target);
    }
  }
  return { nodes, edges };
}

/** Server id ↔ canvas id: node/edge id spaces can collide, so prefix. */
export function canvasId(kind: "n" | "e", serverId: number): string {
  return `${kind}${serverId}`;
}

export function serverIdOf(elementId: string): number {
  return Number(elementId.slice(1));
}

/** Weak edges are resolver guesses; the toggle decides whether they plot. */
export function filterWeakEdges(
  edges: GraphEdge[],
  includeWeak: boolean,
): { edges: GraphEdge[]; hidden: number } {
  if (includeWeak) return { edges, hidden: 0 };
  const kept = edges.filter((edge) => edge.props.weak !== true);
  return { edges: kept, hidden: edges.length - kept.length };
}

/** Convert a server payload to cytoscape elements. Edges whose endpoints are
 *  absent are dropped here too (defensive; the server already prunes). */
export function toCyElements(payload: GraphPayload): ElementDefinition[] {
  const nodeIds = new Set(payload.nodes.map((node) => node.id));
  const nodes: ElementDefinition[] = payload.nodes.map((node) => ({
    group: "nodes",
    data: {
      id: canvasId("n", node.id),
      nodeId: node.id,
      label: labelFor(node),
      title: titleFor(node),
      type: node.type,
      kind: typeof node.props.kind === "string" ? node.props.kind : "",
      focus: node.focus === true,
    },
  }));
  const edges: ElementDefinition[] = payload.edges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge) => ({
      group: "edges",
      data: {
        id: canvasId("e", edge.id),
        edgeId: edge.id,
        source: canvasId("n", edge.source),
        target: canvasId("n", edge.target),
        rel: edge.rel,
        weak: edge.props.weak === true,
      },
    }));
  return [...nodes, ...edges];
}

export interface Chain {
  /** Node ids in walk order; `edges[i]` runs `nodes[i]` → `nodes[i + 1]`. */
  nodes: number[];
  edges: number[];
}

/** Order a lit edge set into one directed chain (the Explorer's traced
 *  path) so the canvas can draw it in and pulse along it. The walk is
 *  deliberately strict: split, merged, parallel, cyclic, or disconnected
 *  sets return null, and callers skip the animation instead of inventing
 *  a narrative the edges do not tell. */
export function orderChain(edges: GraphEdge[], lit: Set<number>): Chain | null {
  const picked = edges.filter((edge) => lit.has(edge.id));
  if (picked.length === 0) return null;
  const outgoing = new Map<number, Array<{ to: number; id: number }>>();
  const indegree = new Map<number, number>();
  const nodes = new Set<number>();
  for (const edge of picked) {
    nodes.add(edge.source);
    nodes.add(edge.target);
    const list = outgoing.get(edge.source) ?? [];
    list.push({ to: edge.target, id: edge.id });
    outgoing.set(edge.source, list);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  }
  const starts = [...nodes].filter((node) => (indegree.get(node) ?? 0) === 0);
  if (starts.length !== 1) return null;
  const chain: Chain = { nodes: [starts[0]], edges: [] };
  const used = new Set<number>();
  let cursor = starts[0];
  for (;;) {
    const next = (outgoing.get(cursor) ?? []).filter((hop) => !used.has(hop.id));
    if (next.length === 0) break;
    if (next.length > 1) return null;
    used.add(next[0].id);
    chain.edges.push(next[0].id);
    cursor = next[0].to;
    chain.nodes.push(cursor);
  }
  return used.size === picked.length ? chain : null;
}
