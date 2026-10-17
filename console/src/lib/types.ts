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

/** `/api/graph/history` — the time machine: the repo's own past (or one
 *  file's) as UTC calendar-month buckets, plus an optional commit window
 *  (`?from=&to=`, `[from, to)` epoch seconds; the pair is sent together). */
export interface HistoryBucket {
  label: string;
  t0: number;
  t1: number;
  commits: number;
  touches: number;
  added: number;
  removed: number;
}

export interface HistoryTotals {
  commits: number;
  added: number;
  removed: number;
  files: number;
  first_ts: number | null;
  last_ts: number | null;
}

export interface HistoryCommit {
  sha: string;
  ts: number;
  summary: string | null;
  author: string | null;
  /** Repo scope only; a file-scoped row counts that file's diff alone. */
  touches?: number;
  added: number;
  removed: number;
}

export interface HistoryTopFile {
  path: string;
  commits: number;
  added: number;
  removed: number;
}

export interface HistoryWindow {
  from: number;
  to: number;
  commits: HistoryCommit[];
  top_files: HistoryTopFile[];
}

export interface HistoryResponse {
  scope: { file: string | null };
  buckets: HistoryBucket[];
  totals: HistoryTotals;
  notes: string[];
  window?: HistoryWindow;
}

/** `/api/graph/impact` — the transitive reverse-CALLS cone above one symbol.
 *  One row per affected symbol at its shortest distance, with the chain that
 *  reaches the target; files are rolled up server-side (source/test split). */
export interface ImpactTarget {
  file: string;
  qualname: string;
  name: string;
  kind: string;
  lineno: number | null;
}

export interface ImpactChainRef {
  file: string;
  qualname: string;
}

export interface ImpactSymbol {
  file: string;
  qualname: string;
  name: string;
  kind: string;
  /** Call order, caller first, target last. */
  chain: ImpactChainRef[];
  /** Per-edge call counts along `chain`, same order. */
  counts: number[];
  /** True when any edge in the kept chain was a resolved guess. */
  weak: boolean;
}

export interface ImpactLevel {
  hops: number;
  symbols: ImpactSymbol[];
}

export interface ImpactFile {
  path: string;
  symbols: number;
  min_hops: number;
  tests: boolean;
}

export interface ImpactSummary {
  direct_callers: number;
  transitive_callers: number;
  affected_files: number;
  affected_test_files: number;
  max_hops: number;
  shown: number;
  truncated: boolean;
  walk_capped: boolean;
}

export interface ImpactResponse {
  target: ImpactTarget;
  params: { depth: number; include_weak: boolean; limit: number };
  summary: ImpactSummary;
  levels: ImpactLevel[];
  files: ImpactFile[];
  notes: string[];
}

/** `/api/graph/ownership` — who knows what, inferred from commit history.
 *  Repo scope ranks people, hot files, and single-owner risks; file scope
 *  ranks one file's authors, suggests reviewers, and lists recent commits.
 *  Every cap the server applied rides in `notes`. */
export interface OwnershipOwnerRef {
  name: string;
  email: string;
  commits: number;
  share: number;
  bot: boolean;
}

export interface OwnershipPerson extends OwnershipOwnerRef {
  files: number;
  churn: number;
  last_ts: number | null;
  recent_commits: number;
}

export interface OwnershipFileRow {
  path: string;
  commits: number;
  churn: number;
  authors: number;
  last_ts: number | null;
  tests: boolean;
  owners: OwnershipOwnerRef[];
  /** Present on single-owner (risk) rows only. */
  primary_share?: number;
}

export interface OwnershipRepo {
  scope: "repo";
  clock: number | null;
  recent_days: number;
  totals: {
    commits: number;
    commits_with_diff: number;
    files: number;
    authors: number;
    file_changes: number;
  };
  people: OwnershipPerson[];
  people_total: number;
  hot_files: OwnershipFileRow[];
  hot_files_total: number;
  risks: OwnershipFileRow[];
  risks_total: number;
  notes: string[];
}

export interface OwnershipFileMeta {
  path: string;
  lang: string | null;
  loc: number | null;
  commits: number;
  authors: number;
  churn: number;
  added: number;
  removed: number;
  first_ts: number | null;
  last_ts: number | null;
  symbols: number;
  tests: boolean;
}

export interface OwnershipFileOwner extends OwnershipOwnerRef {
  added: number;
  removed: number;
  churn: number;
  first_ts: number | null;
  last_ts: number | null;
  recent_commits: number;
}

export interface OwnershipReviewer {
  name: string;
  email: string;
  commits: number;
  recent_commits: number;
  last_ts: number | null;
  why: string;
}

export interface OwnershipCommit {
  sha: string;
  summary: string;
  ts: number | null;
  author: string;
  email: string;
  added: number;
  removed: number;
}

export interface OwnershipRelated {
  path: string;
  commits: number;
  first_ts: number | null;
  last_ts: number | null;
  relation: string;
}

export interface OwnershipFileScope {
  scope: "file";
  path: string;
  clock: number | null;
  recent_days: number;
  file: OwnershipFileMeta;
  owners: OwnershipFileOwner[];
  owners_total: number;
  reviewers: OwnershipReviewer[];
  recent_commits: OwnershipCommit[];
  recent_total: number;
  related_paths: OwnershipRelated[];
  notes: string[];
}

export type OwnershipResponse = OwnershipRepo | OwnershipFileScope;
