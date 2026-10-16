/** Pure helpers for the Ask + Trace Ribbon (M4R step 3).
 *
 *  Everything here is derived from the *recorded* answer — the client never
 *  invents or re-runs the agent's queries; it only maps a step's tool-result
 *  rows to the plain refs (symbols / files / modules) that the server's
 *  `/api/graph/induced` projection resolves back into a subgraph. No DOM,
 *  no fetch — unit-testable against fixture payloads. */

import type { Spotlight } from "./graph";
import type {
  AskAnswer,
  AskStep,
  GraphEdge,
  GraphNode,
  GraphPayload,
} from "./types";

export interface EvidenceRefs {
  symbols: Array<{ file: string; qualname: string }>;
  files: Array<{ path: string }>;
  modules: Array<{ name: string }>;
}

/** One step's replayed subgraph (server projection, verbatim). */
export interface StepReplay {
  nodes: GraphNode[];
  edges: GraphEdge[];
  notes: string[];
}

export interface ReplayBundle {
  byStep: Map<number, StepReplay>;
  merged: GraphPayload;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/** The step's compacted result rows, defensively (the shape is ``unknown``). */
function resultRows(step: AskStep): Array<Record<string, unknown>> {
  const result = asRecord(step.result);
  const rows = result?.results;
  if (!Array.isArray(rows)) return [];
  return rows
    .map(asRecord)
    .filter((row): row is Record<string, unknown> => row !== null);
}

/** "file_history" → "file history"; registry-style tool names for chips. */
export function toolLabel(tool: string): string {
  return tool.replaceAll("_", " ");
}

/** The one-glance fact for a ribbon chip: what the step actually found. */
export function stepFact(step: AskStep): string {
  if (step.status === "error") return "error";
  if (step.status === "ambiguous") return "ambiguous — pick one";
  if (step.status === "not_found") return "nothing found";
  if (step.tool === "overview") return "graph at a glance";
  if (step.tool === "remember") return step.status === "ok" ? "saved" : step.status;
  const count = step.count;
  const singular: Record<string, string> = {
    callers: "caller",
    callees: "callee",
    importers: "importer",
    find_symbols: "match",
    file_history: "commit",
    owners: "author",
    search_commits: "commit",
    recall: "row",
    query: "row",
    symbol: "symbol",
    file: "file",
  };
  const noun = singular[step.tool] ?? "row";
  if (count == null) return step.status === "ok" ? "done" : step.status;
  if (step.tool === "recall" || step.tool === "query") return `${count} ${noun}(s)`;
  return count === 1 ? `1 ${noun}` : `${count} ${noun}s`;
}

/** Queries the step executed (answer.queries entries share round + tool). */
export function queriesForStep(answer: AskAnswer, step: AskStep) {
  return answer.queries.filter(
    (query) => query.round === step.round && query.tool === step.tool,
  );
}

/** Map a step's result rows to plain refs for `/api/graph/induced`.
 *
 *  Only rows that *point at graph entities* are mapped — commits, authors,
 *  facts and aggregates intentionally stay out (a chip for those reads
 *  "no graph replay", instead of a made-up spotlight). */
export function evidenceRefs(step: AskStep): EvidenceRefs {
  const refs: EvidenceRefs = { symbols: [], files: [], modules: [] };
  const seen = new Set<string>();
  const addSymbol = (file: unknown, qualname: unknown): void => {
    const f = str(file);
    const q = str(qualname);
    if (!f || !q) return;
    const key = `s:${f}:${q}`;
    if (seen.has(key)) return;
    seen.add(key);
    refs.symbols.push({ file: f, qualname: q });
  };
  const addFile = (path: unknown): void => {
    const p = str(path);
    if (!p || seen.has(`f:${p}`)) return;
    seen.add(`f:${p}`);
    refs.files.push({ path: p });
  };
  const addModule = (name: unknown): void => {
    const n = str(name);
    if (!n || seen.has(`m:${n}`)) return;
    seen.add(`m:${n}`);
    refs.modules.push({ name: n });
  };

  for (const row of resultRows(step)) {
    if (step.tool === "callers" || step.tool === "callees") {
      const chain = row.chain;
      if (Array.isArray(chain)) {
        for (const node of chain) {
          const rec = asRecord(node);
          if (rec) addSymbol(rec.file, rec.qualname);
        }
      } else {
        addSymbol(row.file, step.tool === "callers" ? row.caller : row.callee);
      }
    } else if (step.tool === "importers") {
      addFile(row.file);
    } else if (step.tool === "find_symbols") {
      addSymbol(row.file, row.qualname);
    }
  }
  if (step.tool === "file_history") addFile(step.params?.path);
  if (step.tool === "importers") addModule(step.params?.module);
  return refs;
}

/** Request body for `/api/graph/induced`, or null when no step replays. */
export function replayRequest(
  answer: AskAnswer,
): { steps: Array<{ id: string } & EvidenceRefs> } | null {
  const steps = answer.steps
    .map((step, index) => ({ id: String(index), ...evidenceRefs(step) }))
    .filter(
      (item) => item.symbols.length + item.files.length + item.modules.length > 0,
    );
  return steps.length > 0 ? { steps } : null;
}

/** Union per-step subgraphs (dedupe by server id) for the trace canvas. */
export function mergeReplays(byStep: Map<number, StepReplay>): GraphPayload {
  const nodes = new Map<number, GraphNode>();
  const edges = new Map<number, GraphEdge>();
  for (const replay of byStep.values()) {
    for (const node of replay.nodes) if (!nodes.has(node.id)) nodes.set(node.id, node);
    for (const edge of replay.edges) if (!edges.has(edge.id)) edges.set(edge.id, edge);
  }
  const keptEdges = [...edges.values()].filter(
    (edge) => nodes.has(edge.source) && nodes.has(edge.target),
  );
  return { nodes: [...nodes.values()], edges: keptEdges, notes: [] };
}

/** Spotlight for a step: its nodes/edges lit; everything else dims. An
 *  empty set is honest — "this step has no subgraph in the canvas". */
export function spotlightFor(replay: StepReplay | undefined): Spotlight {
  return {
    nodes: new Set((replay?.nodes ?? []).map((node) => node.id)),
    edges: new Set((replay?.edges ?? []).map((edge) => edge.id)),
  };
}

const SAMPLE_LIMIT = 3;

/** One display line for a result row — identifying facts first. */
export function rowLine(row: Record<string, unknown>): string {
  const sha = str(row.sha);
  if (sha) {
    const summary = str(row.summary) ?? "";
    return `${sha.slice(0, 8)} ${summary}`.trim();
  }
  const file = str(row.file);
  const qualname = str(row.qualname) ?? str(row.caller) ?? str(row.callee);
  if (file && qualname) return `${file} · ${qualname}`;
  if (file) {
    const line = row.line ?? row.lineno;
    return typeof line === "number" ? `${file}:${line}` : file;
  }
  const path = str(row.path) ?? str(row.file_path);
  if (path) return path;
  const author = str(row.author);
  if (author) {
    const commits = row.commits;
    return typeof commits === "number" ? `${author} · ${commits} commits` : author;
  }
  const name = str(row.name);
  if (name) return name;
  const text = str(row.text) ?? str(row.fact);
  if (text) return text;
  return JSON.stringify(row).slice(0, 140);
}

/** First ≤3 result rows as display lines, with an honest "more" tail. */
export function summarizeRows(step: AskStep, limit = SAMPLE_LIMIT): string[] {
  const rows = resultRows(step);
  if (rows.length === 0) {
    const result = asRecord(step.result);
    const record = result?.results;
    if (record && typeof record === "object" && !Array.isArray(record)) {
      return Object.entries(record as Record<string, unknown>)
        .slice(0, 3)
        .map(([key, value]) => {
          if (Array.isArray(value)) return `${key}: ${value.length} rows`;
          if (value && typeof value === "object") return `${key}: {…}`;
          return `${key}: ${String(value)}`;
        });
    }
    return [];
  }
  const lines = rows.slice(0, limit).map(rowLine);
  if (rows.length > limit) lines.push(`… ${rows.length - limit} more row(s)`);
  return lines;
}
