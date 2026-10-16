/** ⌘K palette item model — pure logic, no React (unit-tested).
 *
 *  Three sources: fixed navigation actions, live graph search
 *  (`/api/graph/find`), and the pinned starter asks. Order is deliberate —
 *  actions → symbols → files → asks — so Enter on a top search hit never
 *  silently turns into a question; the free-text "ask" catch-all sits last,
 *  where it reads as "or just ask". Rows are never fabricated: when the
 *  server returns nothing, only fixtures we hold locally (actions, pinned
 *  starters) are shown. */

import { STARTERS } from "./questions";
import { href } from "./router";
import type { FindResult } from "./types";

export type PaletteKind = "action" | "ask" | "symbol" | "file";

export interface PaletteItem {
  id: string;
  kind: PaletteKind;
  label: string;
  hint: string;
  to: string;
}

interface ActionDef {
  label: string;
  keywords: string;
  to: string;
}

/** Navigation actions. Keywords exist so "graph" finds Explorer and
 *  "judges" finds About — the words a user would actually type. */
const ACTIONS: ActionDef[] = [
  {
    label: "Go to Home",
    keywords: "overview stats hubs activity landing console",
    to: href("/overview"),
  },
  {
    label: "Go to Explorer",
    keywords: "graph canvas nodes search drill-down symbol file",
    to: href("/explorer"),
  },
  {
    label: "Go to Ask",
    keywords: "question agent trace ribbon cypher",
    to: href("/ask"),
  },
  {
    label: "Go to Memory",
    keywords: "episodes facts claims timeline bi-temporal scrubber history superseded",
    to: href("/memory"),
  },
  {
    label: "Go to About",
    keywords: "judges architecture falkordb tracks why",
    to: href("/about"),
  },
];

/** Token-subset match: every whitespace-separated token of `q` must appear
 *  somewhere in `text`. Case-insensitive; an empty q matches everything. */
export function tokensMatch(text: string, q: string): boolean {
  const hay = text.toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => hay.includes(token));
}

function actionItems(q: string): PaletteItem[] {
  return ACTIONS.filter((action) => tokensMatch(`${action.label} ${action.keywords}`, q)).map(
    (action) => ({
      id: `action:${action.to}`,
      kind: "action" as const,
      label: action.label,
      hint: "go",
      to: action.to,
    }),
  );
}

function symbolItems(find: FindResult): PaletteItem[] {
  return find.symbols.map((hit) => ({
    id: `symbol:${hit.file}:${hit.qualname}`,
    kind: "symbol" as const,
    label: hit.qualname,
    hint: `${hit.file}:${hit.lineno}`,
    to: href("/explorer", { file: hit.file, qualname: hit.qualname }),
  }));
}

function fileItems(find: FindResult): PaletteItem[] {
  return find.files.map((hit) => ({
    id: `file:${hit.path}`,
    kind: "file" as const,
    label: hit.path,
    hint: `${hit.loc} lines`,
    to: href("/explorer", { path: hit.path }),
  }));
}

function starterAsk(question: string): PaletteItem {
  return {
    id: `ask:${question}`,
    kind: "ask",
    label: question,
    hint: "ask",
    to: href("/ask", { q: question }),
  };
}

function askItems(q: string): PaletteItem[] {
  if (!q) return STARTERS.map(starterAsk);
  const items = STARTERS.filter((question) => tokensMatch(question, q)).map(starterAsk);
  // Free-text fallback, unless the typed query already IS one of the
  // matched starters (no duplicate rows for identical asks).
  const normalized = q.toLowerCase();
  const duplicate = items.some((item) => item.label.toLowerCase() === normalized);
  if (!duplicate) {
    items.push({
      id: `ask:free:${normalized}`,
      kind: "ask",
      label: `Ask the archive: “${q}”`,
      hint: "ask",
      to: href("/ask", { q }),
    });
  }
  return items;
}

/** Build the full, ordered palette list for a query and the latest find
 *  response (pass `null` while it is stale or unavailable). `find` rows are
 *  only surfaced once the query is ≥2 chars — the same floor the server
 *  and the rail search use. */
export function buildPaletteItems(q: string, find: FindResult | null): PaletteItem[] {
  const query = q.trim();
  const found = query.length >= 2 && find ? [...symbolItems(find), ...fileItems(find)] : [];
  return [...actionItems(query), ...found, ...askItems(query)];
}

/** Group consecutive items for display. Returns runs with their start
 *  index into the flat list (keyboard navigation is index-based). */
export interface PaletteGroup {
  kind: PaletteKind;
  label: string;
  start: number;
  items: PaletteItem[];
}

const GROUP_LABELS: Record<PaletteKind, string> = {
  action: "Actions",
  symbol: "Symbols",
  file: "Files",
  ask: "Asks",
};

export function groupPaletteItems(items: PaletteItem[]): PaletteGroup[] {
  const groups: PaletteGroup[] = [];
  items.forEach((item, index) => {
    const last = groups[groups.length - 1];
    if (last && last.kind === item.kind) {
      last.items.push(item);
    } else {
      groups.push({ kind: item.kind, label: GROUP_LABELS[item.kind], start: index, items: [item] });
    }
  });
  return groups;
}
