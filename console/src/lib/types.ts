/** JSON shapes of the read-only endpoints in `archivist.webapp`.
 *  These mirror the server projections exactly — nothing invented here. */

export interface RepoInfo {
  name?: string;
  path?: string;
  head?: string;
  updated_at?: number;
}

export interface LabelCount {
  label: string;
  count: number;
}

export interface EdgeCount {
  type: string;
  count: number;
}

export interface EpisodeInfo {
  id: string;
  kind: string;
  source: string;
  ts: number;
  question: string | null;
}

export interface GraphState {
  graph: string;
  repo: RepoInfo | null;
  nodes: LabelCount[];
  edges: EdgeCount[];
  memory: {
    sessions: number;
    facts: number;
    latest: EpisodeInfo | null;
  };
  llm: { configured: boolean; model: string | null };
}

export interface HubSymbol {
  file: string;
  qualname: string;
  name: string;
  kind: string;
  callers: number;
  calls: number;
}

export interface HubModule {
  name: string;
  importers: number;
  sites: number;
}

export interface Overview {
  hubs: {
    symbols: HubSymbol[];
    modules: HubModule[];
  };
}

export interface CommitRow {
  sha: string;
  ts: number;
  summary: string;
  author: string | null;
  files: number;
}

export interface Activity {
  commits: CommitRow[];
}

export interface SymbolHit {
  file: string;
  qualname: string;
  name: string;
  kind: string;
  lineno: number;
}

export interface FileHit {
  path: string;
  loc: number;
}

export interface FindResult {
  query: string;
  symbols: SymbolHit[];
  files: FileHit[];
  notes: string[];
}

export interface AskStep {
  round: number;
  tool: string;
  params: Record<string, unknown>;
  status: string;
  count: number | null;
  ms: number;
  notes: string[];
  error: string | null;
  result: unknown;
}

export interface AskAnswer {
  question: string;
  text: string;
  partial: boolean;
  error: string | null;
  llm_calls: number;
  steps: AskStep[];
  queries: Array<Record<string, unknown>>;
  usage: Record<string, unknown>;
}

export interface AskResponse {
  configured: boolean;
  answer: AskAnswer;
  episode_id: string | null;
  warnings: string[];
}

export interface GraphNode {
  id: number;
  type: string;
  props: Record<string, unknown>;
  focus?: boolean;
}

export interface GraphEdge {
  id: number;
  rel: string;
  source: number;
  target: number;
  props: Record<string, unknown>;
}

export interface SymbolStats {
  callers: number;
  callees: number;
}

export interface FileHistoryRow {
  sha: string;
  ts: number;
  summary: string;
  added: number;
  removed: number;
}

/** Payload of `/api/graph/{symbol,file,module}` — server projections verbatim. */
export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  notes: string[];
  stats?: SymbolStats;
  file?: Record<string, unknown>;
  history?: FileHistoryRow[];
}

/** Payload of `/api/graph/path` — shortest directed call path, or an honest gap. */
export interface PathResult extends GraphPayload {
  found: boolean;
  direction: "forward" | "reverse" | "same" | null;
  hops: number | null;
  from: { file: string; qualname: string };
  to: { file: string; qualname: string };
}
