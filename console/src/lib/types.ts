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
  queries: AnswerQuery[];
  usage: Record<string, unknown>;
}

/** One executed Cypher statement from the agent trace (answer.queries). */
export interface AnswerQuery {
  round: number;
  tool: string;
  cypher: string;
  params: Record<string, unknown>;
  ms: number;
  rows: number;
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

/** One step's subgraph from `POST /api/graph/induced` (Trace Ribbon replay). */
export interface ReplayStepPayload extends GraphPayload {
  id: string;
}

export interface InducedResponse {
  steps: ReplayStepPayload[];
}

/** `/api/graph/memory` — the memory timeline (episodes + bi-temporal facts).
 *  Three modes over the same state: "now" (open windows), "history"
 *  (invalidate-don't-delete), "as_of" (what was true at epoch T). */
export interface MemoryHandoff {
  note: string | null;
  ts: number | null;
  to?: string;
  from?: string;
}

export interface MemoryEpisode {
  id: string;
  kind: string;
  source: string;
  ts: number;
  created_at: number | null;
  question: string | null;
  answer: string | null;
  role: string | null;
  run: string | null;
  props: Record<string, unknown>;
  handoffs: MemoryHandoff[];
  handoff_in: MemoryHandoff[];
}

export interface MemoryFactRef {
  kind: string;
  label: string | null;
}

export interface MemoryFact {
  id: string;
  kind: string | null;
  text: string;
  valid_at: number | null;
  invalid_at: number | null;
  created_at: number | null;
  expired_at: number | null;
  episode_id: string | null;
  supersedes: string[];
  superseded_by: string[];
  refs: MemoryFactRef[];
}

/** Minimal fields for chain neighbours the current view hides (server keeps
 *  these tiny so a chain chip can name an invisible claim without refetch). */
export interface MemoryChainFact {
  id: string;
  text: string;
  kind: string | null;
  valid_at: number | null;
  invalid_at: number | null;
}

export interface MemoryCounts {
  episodes: number;
  facts: number;
  open_facts: number;
  handoff_edges: number;
  supersede_edges: number;
}

export interface MemoryResponse {
  graph: string;
  mode: "now" | "history" | "as_of";
  as_of: number | null;
  range: { earliest: number | null; latest: number | null };
  counts: MemoryCounts;
  episodes: MemoryEpisode[];
  facts: MemoryFact[];
  chain_facts: MemoryChainFact[];
  notes: string[];
}

/** `/api/graph/crew` — crew runs as graph state: one phase episode per
 *  role, grouped into runs, handoff notes read back from
 *  `(:Episode)-[:HANDOFF]->(:Episode)` edges. `status` is honest: only a
 *  full planner→investigator→verifier spine with no phase errors reads
 *  `complete`; anything else is `partial` or `interrupted`. */
export interface CrewPhase {
  id: string;
  role: string;
  ts: number | null;
  question: string | null;
  input_truncated: boolean;
  answer: string | null;
  answer_truncated: boolean;
  duration_ms: number | null;
  steps: number | null;
  queries: number | null;
  llm_calls: number | null;
  partial: boolean | null;
  error: string | null;
}

export interface CrewHandoff {
  from: string;
  to: string;
  note: string | null;
  ts: number | null;
}

export interface CrewRun {
  run: string;
  question: string | null;
  started_at: number | null;
  ended_at: number | null;
  status: "complete" | "partial" | "interrupted";
  phases: CrewPhase[];
  handoffs: CrewHandoff[];
}

export interface CrewResponse {
  graph: string;
  counts: { runs: number; phases: number; handoffs: number };
  runs: CrewRun[];
  notes: string[];
}
