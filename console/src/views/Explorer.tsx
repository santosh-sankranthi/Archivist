/** Explorer v2 — canvas drill-down over the graph endpoints, with
 *  breadcrumbs, selection spotlight, an inspector drawer, and a
 *  shortest-path finder.
 *
 *  URL contract (set by the left rail, home hubs, and breadcrumbs):
 *    ?file=&qualname=  → call neighbourhood
 *    ?path=            → file view
 *    ?module=          → importers view
 *  Weak edges are fetched but hidden by default (resolver guesses); the
 *  toggle reveals them dashed, and the meta strip states what is hidden
 *  either way. */

import type { MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { GraphCanvas } from "../components/GraphCanvas";
import { ApiError, getJSON, useApi } from "../lib/api";
import { formatInt, relTime, shortSha } from "../lib/format";
import {
  filterWeakEdges,
  labelFor,
  neighborhood,
  nodeKind,
  questionFor,
  titleFor,
} from "../lib/graph";
import { useDebounced } from "../lib/hooks";
import { href, navigate } from "../lib/router";
import type { FindResult, GraphNode, GraphPayload, PathResult } from "../lib/types";
import styles from "./Explorer.module.css";

interface Props {
  query: URLSearchParams;
}

type Target =
  | { kind: "symbol"; file: string; qualname: string }
  | { kind: "file"; path: string }
  | { kind: "module"; name: string }
  | null;

type SymRef = { file: string; qualname: string; name: string };
type PickRef =
  | { kind: "symbol"; file: string; qualname: string; name: string }
  | { kind: "file"; path: string };

function targetFrom(query: URLSearchParams): Target {
  const file = query.get("file");
  const qualname = query.get("qualname");
  if (file && qualname) return { kind: "symbol", file, qualname };
  const path = query.get("path");
  if (path) return { kind: "file", path };
  const module = query.get("module");
  if (module) return { kind: "module", name: module };
  return null;
}

function linkProps(to: string) {
  return {
    href: to,
    onClick: (event: ReactMouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey) return;
      event.preventDefault();
      navigate(to);
    },
  };
}

export function Explorer({ query }: Props) {
  const target = useMemo(() => targetFrom(query), [query]);
  const [depth, setDepth] = useState(1);
  const [weakOn, setWeakOn] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);

  const [pathOpen, setPathOpen] = useState(false);
  const [fromRef, setFromRef] = useState<SymRef | null>(null);
  const [toRef, setToRef] = useState<SymRef | null>(null);
  const [pathDepth, setPathDepth] = useState(8);
  const [pathResult, setPathResult] = useState<PathResult | null>(null);
  const [pathBusy, setPathBusy] = useState(false);
  const [pathError, setPathError] = useState<string | null>(null);

  const apiPath =
    target?.kind === "symbol"
      ? `/api/graph/symbol?file=${encodeURIComponent(target.file)}` +
        `&qualname=${encodeURIComponent(target.qualname)}&depth=${depth}&weak=1`
      : target?.kind === "file"
        ? `/api/graph/file?path=${encodeURIComponent(target.path)}`
        : target?.kind === "module"
          ? `/api/graph/module?name=${encodeURIComponent(target.name)}`
          : null;
  const graph = useApi<GraphPayload>(apiPath);

  // A new drill-down target (or retune) resets selection and any traced path.
  useEffect(() => {
    setSelectedId(null);
    setPathResult(null);
    setPathError(null);
    setDrawerOpen(true);
  }, [apiPath]);

  const shownPath = pathResult?.found === true ? pathResult : null;

  const weakInfo = useMemo(() => {
    const base = shownPath ?? graph.data;
    if (!base || target?.kind !== "symbol" || shownPath) {
      return { payload: base, hiddenWeak: 0 };
    }
    const { edges, hidden } = filterWeakEdges(base.edges, weakOn);
    return { payload: { ...base, edges }, hiddenWeak: hidden };
  }, [graph.data, shownPath, target?.kind, weakOn]);
  const canvasPayload = weakInfo.payload;

  const selectedNode = useMemo(
    () => canvasPayload?.nodes.find((node) => node.id === selectedId) ?? null,
    [canvasPayload, selectedId],
  );

  const spotlight = useMemo(() => {
    if (!canvasPayload) return null;
    if (selectedId != null) return neighborhood(canvasPayload, selectedId);
    if (shownPath) {
      // The whole path is the subject: light its edges, nothing dims.
      return {
        nodes: new Set(canvasPayload.nodes.map((node) => node.id)),
        edges: new Set(canvasPayload.edges.map((edge) => edge.id)),
      };
    }
    return null;
  }, [canvasPayload, selectedId, shownPath]);

  const notes = useMemo(() => {
    const list: string[] = [];
    if (shownPath) {
      const hops = shownPath.hops ?? 0;
      list.push(`shortest path · ${hops} hop${hops === 1 ? "" : "s"}`);
    }
    if (weakInfo.hiddenWeak > 0 && !weakOn) {
      list.push(
        `${weakInfo.hiddenWeak} weak edge(s) hidden — resolver guesses, not ` +
          `verified (toggle weak to show)`,
      );
    }
    if (canvasPayload) list.push(...canvasPayload.notes);
    return list;
  }, [shownPath, weakInfo.hiddenWeak, weakOn, canvasPayload]);

  const counts = canvasPayload
    ? `${formatInt(canvasPayload.nodes.length)} nodes · ${formatInt(canvasPayload.edges.length)} edges` +
      (target?.kind === "symbol" && !shownPath && graph.data?.stats
        ? ` · callers ${formatInt(graph.data.stats.callers)} · callees ${formatInt(graph.data.stats.callees)}`
        : "")
    : null;

  function handleSelect(node: GraphNode | null): void {
    setSelectedId(node ? node.id : null);
    if (node) setDrawerOpen(true);
  }

  function clearPath(): void {
    setPathResult(null);
    setPathError(null);
  }

  function findPathFrom(ref: SymRef): void {
    setFromRef(ref);
    setPathOpen(true);
  }

  async function tracePath(): Promise<void> {
    if (!fromRef || !toRef || pathBusy) return;
    setPathBusy(true);
    setPathError(null);
    setPathResult(null);
    try {
      const qs = new URLSearchParams({
        from_file: fromRef.file,
        from_qualname: fromRef.qualname,
        to_file: toRef.file,
        to_qualname: toRef.qualname,
        max_depth: String(pathDepth),
      });
      setPathResult(await getJSON<PathResult>(`/api/graph/path?${qs}`));
    } catch (error) {
      setPathError(error instanceof ApiError ? error.message : String(error));
    } finally {
      setPathBusy(false);
    }
  }

  let stage: ReactNode;
  if (pathResult && pathResult.found === false) {
    stage = (
      <div className={styles.centerCard}>
        <h2 className={styles.centerTitle}>No call path.</h2>
        <p className={styles.centerBody}>
          Nothing connects {pathResult.from.qualname} and {pathResult.to.qualname}{" "}
          in the call graph within {pathDepth} hops. Try more hops, or check the
          direction — impact usually flows callee → caller.
        </p>
        {pathResult.notes.length > 0 ? (
          <p className={styles.centerNote}>{pathResult.notes.join(" · ")}</p>
        ) : null}
        <div className={styles.centerActions}>
          <button type="button" className={styles.btn} onClick={clearPath}>
            clear path
          </button>
        </div>
      </div>
    );
  } else if (graph.error) {
    stage = (
      <div className={styles.centerCard}>
        <h2 className={styles.centerTitle}>
          {graph.error.code === "not_found"
            ? "Not in the archive."
            : "The graph is out of reach."}
        </h2>
        <p className={styles.centerBody}>{graph.error.message}</p>
        <p className={styles.centerNote}>
          Search the live graph from the left rail, or from the explorer gate.
        </p>
        <div className={styles.centerActions}>
          <a className={styles.btn} {...linkProps(href("/explorer"))}>
            search instead
          </a>
        </div>
      </div>
    );
  } else if (!canvasPayload) {
    stage = target ? (
      <p className={styles.reading}>reading the graph…</p>
    ) : (
      <Gate
        onPick={(pick) => {
          if (pick.kind === "symbol") {
            navigate(href("/explorer", { file: pick.file, qualname: pick.qualname }));
          } else {
            navigate(href("/explorer", { path: pick.path }));
          }
        }}
      />
    );
  } else {
    stage = (
      <>
        <GraphCanvas
          payload={canvasPayload}
          spotlight={spotlight}
          selectedId={selectedId}
          onSelect={handleSelect}
        />
        <Legend />
        {drawerOpen && (selectedNode || target) ? (
          <Inspector
            node={selectedNode}
            target={target}
            payload={canvasPayload}
            onClose={() => {
              setDrawerOpen(false);
              setSelectedId(null);
            }}
            onFindPath={findPathFrom}
          />
        ) : null}
      </>
    );
  }

  return (
    <div className={styles.explorer}>
      <header className={styles.bar}>
        <Crumbs target={target} />
        <span className={styles.spacer} />
        <div className={styles.controls}>
          {!drawerOpen && canvasPayload ? (
            <button
              type="button"
              className={styles.btn}
              onClick={() => setDrawerOpen(true)}
            >
              inspector
            </button>
          ) : null}
          {target?.kind === "symbol" && !shownPath ? (
            <>
              <button
                type="button"
                className={styles.btn}
                aria-pressed={weakOn}
                title="resolver guesses — dashed edges"
                onClick={() => setWeakOn((value) => !value)}
              >
                weak
              </button>
              <label className={styles.depthLabel}>
                depth
                <select
                  className={styles.select}
                  value={depth}
                  onChange={(event) => setDepth(Number(event.target.value))}
                  aria-label="Traversal depth"
                >
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                  <option value={3}>3</option>
                </select>
              </label>
            </>
          ) : null}
          {shownPath ? (
            <button type="button" className={styles.btn} onClick={clearPath}>
              clear path
            </button>
          ) : null}
          <button
            type="button"
            className={styles.btn}
            aria-pressed={pathOpen}
            onClick={() => setPathOpen((value) => !value)}
          >
            path finder
          </button>
        </div>
      </header>

      {pathOpen ? (
        <div className={styles.tray}>
          <span className={styles.trayLabel}>Path finder</span>
          {fromRef ? (
            <RefChip value={fromRef} onClear={() => setFromRef(null)} />
          ) : (
            <FindField
              placeholder="From — symbol…"
              symbolsOnly
              onPick={(pick) => {
                if (pick.kind === "symbol") {
                  setFromRef({ file: pick.file, qualname: pick.qualname, name: pick.name });
                }
              }}
            />
          )}
          <span className={styles.arrow}>→</span>
          {toRef ? (
            <RefChip value={toRef} onClear={() => setToRef(null)} />
          ) : (
            <FindField
              placeholder="To — symbol…"
              symbolsOnly
              onPick={(pick) => {
                if (pick.kind === "symbol") {
                  setToRef({ file: pick.file, qualname: pick.qualname, name: pick.name });
                }
              }}
            />
          )}
          <label className={styles.depthLabel}>
            max hops
            <select
              className={styles.select}
              value={pathDepth}
              onChange={(event) => setPathDepth(Number(event.target.value))}
              aria-label="Maximum path length"
            >
              <option value={6}>6</option>
              <option value={8}>8</option>
              <option value={12}>12</option>
            </select>
          </label>
          <button
            type="button"
            className={styles.btnAccent}
            disabled={!fromRef || !toRef || pathBusy}
            onClick={() => void tracePath()}
          >
            {pathBusy ? "tracing…" : "trace path"}
          </button>
          <button
            type="button"
            className={styles.btn}
            onClick={() => setPathOpen(false)}
          >
            close
          </button>
          {pathError ? <p className={styles.trayErr}>{pathError}</p> : null}
        </div>
      ) : null}

      {notes.length > 0 || counts ? (
        <div className={styles.meta}>
          <span className={styles.notes}>{notes.join("  ·  ")}</span>
          {counts ? <span className={styles.counts}>{counts}</span> : null}
        </div>
      ) : null}

      <div className={styles.stage}>{stage}</div>
    </div>
  );
}

/* -- breadcrumbs -------------------------------------------------------- */

function Crumbs({ target }: { target: Target }) {
  const parts: ReactNode[] = [
    <a key="root" className={styles.crumbLink} {...linkProps(href("/"))}>
      Archive
    </a>,
  ];
  const sep = (key: string) => (
    <span key={key} className={styles.crumbSep}>
      /
    </span>
  );
  if (target?.kind === "symbol") {
    parts.push(sep("s1"));
    parts.push(
      <a
        key="file"
        className={styles.crumbLink}
        title={target.file}
        {...linkProps(href("/explorer", { path: target.file }))}
      >
        {target.file}
      </a>,
    );
    parts.push(sep("s2"));
    parts.push(
      <span key="cur" className={styles.crumbCurrent}>
        {target.qualname}
      </span>,
    );
  } else if (target?.kind === "file") {
    parts.push(sep("s1"));
    parts.push(
      <span key="cur" className={styles.crumbCurrent} title={target.path}>
        {target.path}
      </span>,
    );
  } else if (target?.kind === "module") {
    parts.push(sep("s1"));
    parts.push(
      <span key="cur" className={styles.crumbCurrent}>
        module {target.name}
      </span>,
    );
  }
  return (
    <nav className={styles.crumbs} aria-label="Breadcrumb">
      {parts}
    </nav>
  );
}

/* -- path finder chip --------------------------------------------------- */

function RefChip({ value, onClear }: { value: SymRef; onClear: () => void }) {
  return (
    <span className={styles.chip}>
      {value.name}
      <span className={styles.chipPath}>{value.file}</span>
      <button
        type="button"
        className={styles.chipClear}
        onClick={onClear}
        aria-label={`Remove ${value.name}`}
      >
        ×
      </button>
    </span>
  );
}

/* -- find field (gate search + path pickers) ---------------------------- */

function FindField({
  placeholder,
  symbolsOnly = false,
  filters = false,
  onPick,
}: {
  placeholder: string;
  symbolsOnly?: boolean;
  filters?: boolean;
  onPick: (pick: PickRef) => void;
}) {
  const [text, setText] = useState("");
  const [kindFilter, setKindFilter] = useState<"all" | "symbols" | "files">("all");
  const debounced = useDebounced(text.trim(), 220);
  const searching = debounced.length >= 2;
  const find = useApi<FindResult>(
    searching
      ? `/api/graph/find?q=${encodeURIComponent(debounced)}&limit=${filters ? 10 : 8}`
      : null,
  );

  const inline = filters;
  const data = find.data;
  const symbols = kindFilter === "files" ? [] : (data?.symbols ?? []);
  const files = symbolsOnly || kindFilter === "symbols" ? [] : (data?.files ?? []);
  const empty =
    searching && data !== null && symbols.length === 0 && files.length === 0;

  function pick(pickRef: PickRef): void {
    setText("");
    onPick(pickRef);
  }

  return (
    <div className={`${styles.findField} ${inline ? styles.findFieldInline : ""}`}>
      <input
        className={styles.findInput}
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setText("");
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        autoComplete="off"
        spellCheck={false}
      />
      {searching ? (
        <>
          {inline ? (
            <div className={styles.filters}>
              {(["all", "symbols", "files"] as const).map((key) => (
                <button
                  key={key}
                  type="button"
                  className={styles.filterChip}
                  aria-pressed={kindFilter === key}
                  onClick={() => setKindFilter(key)}
                >
                  {key}
                </button>
              ))}
            </div>
          ) : null}
          <div className={inline ? styles.findList : styles.findPanel}>
            {find.loading && !data ? (
              <p className={styles.findHint}>searching…</p>
            ) : null}
            {find.error ? <p className={styles.findHint}>{find.error.message}</p> : null}
            {empty ? <p className={styles.findHint}>No matches in the archive.</p> : null}
            {symbols.map((hit) => (
              <button
                key={`s:${hit.file}:${hit.qualname}`}
                type="button"
                className={inline ? styles.gateRow : styles.pickerRow}
                onClick={() =>
                  pick({
                    kind: "symbol",
                    file: hit.file,
                    qualname: hit.qualname,
                    name: hit.name,
                  })
                }
              >
                <span className={styles.pickerName}>{hit.name}</span>
                <span className={styles.pickerMeta}>
                  {hit.file}:{hit.lineno} · {hit.kind}
                </span>
              </button>
            ))}
            {files.map((hit) => (
              <button
                key={`f:${hit.path}`}
                type="button"
                className={inline ? styles.gateRow : styles.pickerRow}
                onClick={() => pick({ kind: "file", path: hit.path })}
              >
                <span className={styles.pickerName}>{hit.path}</span>
                <span className={styles.pickerMeta}>{formatInt(hit.loc)} loc</span>
              </button>
            ))}
            {data && data.notes.length > 0 ? (
              <p className={styles.findHint}>{data.notes.join(" · ")}</p>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

/* -- gate (nothing selected yet) ---------------------------------------- */

function Gate({ onPick }: { onPick: (pick: PickRef) => void }) {
  return (
    <div className={styles.gate}>
      <div className={styles.gateCard}>
        <h2 className={styles.gateTitle}>Explore the archive.</h2>
        <p className={styles.gateBody}>
          Fuzzy search over every file and symbol in the live graph. Pick a hit
          to draw its neighbourhood; drill down with the breadcrumbs and
          inspect any node on the canvas.
        </p>
        <FindField placeholder="Files & symbols…" filters onPick={onPick} />
        <p className={styles.gateHint}>
          Try <em>url_for</em>, <em>blueprints</em>, or <em>helpers</em>.
        </p>
      </div>
    </div>
  );
}

/* -- inspector ---------------------------------------------------------- */

function kindLabel(node: GraphNode): string {
  switch (nodeKind(node)) {
    case "symbol":
      return typeof node.props.kind === "string" ? node.props.kind : "symbol";
    case "file":
      return "file";
    case "module":
      return "module";
    default:
      return node.type.toLowerCase();
  }
}

function propRows(node: GraphNode): Array<[string, string]> {
  const p = node.props;
  switch (nodeKind(node)) {
    case "symbol":
      return [
        ["file", String(p.file ?? "?")],
        ["qualname", String(p.qualname ?? "?")],
        ["line", String(p.lineno ?? "?")],
      ];
    case "file":
      return [
        ["path", String(p.path ?? "?")],
        ["loc", String(p.loc ?? "?")],
      ];
    case "module":
      return [["name", String(p.name ?? "?")]];
    default:
      return [];
  }
}

function Inspector({
  node,
  target,
  payload,
  onClose,
  onFindPath,
}: {
  node: GraphNode | null;
  target: Target;
  payload: GraphPayload;
  onClose: () => void;
  onFindPath: (ref: SymRef) => void;
}) {
  const statsRow = payload.stats ? (
    <div className={styles.statRow}>
      <div className={styles.stat}>
        <span className={styles.statValue}>{formatInt(payload.stats.callers)}</span>
        <span className={styles.statLabel}>callers</span>
      </div>
      <div className={styles.stat}>
        <span className={styles.statValue}>{formatInt(payload.stats.callees)}</span>
        <span className={styles.statLabel}>callees</span>
      </div>
    </div>
  ) : null;

  if (node) {
    const p = node.props;
    const isSymbol = nodeKind(node) === "symbol";
    const file = typeof p.file === "string" ? p.file : "";
    const qualname = typeof p.qualname === "string" ? p.qualname : "";
    const name = typeof p.name === "string" && p.name ? p.name : qualname || String(node.id);
    const isCurrentFocus =
      target?.kind === "symbol" && target.file === file && target.qualname === qualname;
    return (
      <aside className={styles.inspector} aria-label="Inspector">
        <div className={styles.inspectorHead}>
          <div>
            <div className={styles.inspectorKicker}>{kindLabel(node)}</div>
            <div className={styles.inspectorTitle} title={titleFor(node)}>
              {labelFor(node)}
            </div>
          </div>
          <button
            type="button"
            className={styles.inspectorClose}
            onClick={onClose}
            aria-label="Close inspector"
          >
            ×
          </button>
        </div>
        <div className={styles.inspectorBody}>
          {node.focus ? statsRow : null}
          {propRows(node).map(([key, value]) => (
            <div key={key} className={styles.propRow}>
              <span className={styles.propKey}>{key}</span>
              <span className={styles.propValue}>{value}</span>
            </div>
          ))}
          <div className={styles.actions}>
            {isSymbol && !isCurrentFocus ? (
              <button
                type="button"
                className={styles.actionBtn}
                onClick={() => navigate(href("/explorer", { file, qualname }))}
              >
                Focus call graph
              </button>
            ) : null}
            {isSymbol ? (
              <button
                type="button"
                className={styles.actionBtn}
                onClick={() => onFindPath({ file, qualname, name })}
              >
                Find path from here…
              </button>
            ) : null}
            <button
              type="button"
              className={styles.actionBtn}
              onClick={() => navigate(href("/ask", { q: questionFor(node) }))}
            >
              Ask about this
            </button>
          </div>
        </div>
      </aside>
    );
  }

  // At rest: the drill-down target itself.
  return (
    <aside className={styles.inspector} aria-label="Inspector">
      <div className={styles.inspectorHead}>
        <div>
          <div className={styles.inspectorKicker}>{target?.kind ?? "explorer"}</div>
          <div className={styles.inspectorTitle}>
            {target?.kind === "symbol"
              ? target.qualname
              : target?.kind === "file"
                ? target.path
                : target?.kind === "module"
                  ? target.name
                  : ""}
          </div>
        </div>
        <button
          type="button"
          className={styles.inspectorClose}
          onClick={onClose}
          aria-label="Close inspector"
        >
          ×
        </button>
      </div>
      <div className={styles.inspectorBody}>
        {target?.kind === "symbol" ? statsRow : null}
        {target?.kind === "symbol" ? (
          <p className={styles.gateHint}>Select a node to inspect its coordinates.</p>
        ) : null}
        {target?.kind === "file" && payload.history && payload.history.length > 0 ? (
          <>
            <span className={styles.sectionLabel}>Recent history</span>
            <div className={styles.historyList}>
              {payload.history.map((row) => (
                <div key={row.sha} className={styles.historyRow}>
                  <span className={styles.historySha}>{shortSha(row.sha)}</span>
                  <span>{row.summary}</span>
                  <span className={styles.historyMeta}>
                    {relTime(row.ts)} · +{formatInt(row.added)} −{formatInt(row.removed)}
                  </span>
                </div>
              ))}
            </div>
          </>
        ) : null}
        {target?.kind === "module" ? (
          <p className={styles.gateHint}>
            {formatInt(payload.nodes.filter((n) => n.type === "File").length)}{" "}
            importer file(s) plotted. Select one to inspect it.
          </p>
        ) : null}
      </div>
    </aside>
  );
}

/* -- legend ------------------------------------------------------------- */

function Legend() {
  return (
    <div className={styles.legend} aria-hidden="true">
      <span className={styles.legendItem}>
        <span className={styles.mark} /> symbol
      </span>
      <span className={styles.legendItem}>
        <span className={`${styles.mark} ${styles.markFile}`} /> file
      </span>
      <span className={styles.legendItem}>
        <span className={`${styles.mark} ${styles.markModule}`} /> module
      </span>
      <span className={styles.legendItem}>
        <span className={styles.markSolid} /> calls
      </span>
      <span className={styles.legendItem}>
        <span className={styles.markDotted} /> imports
      </span>
      <span className={styles.legendItem}>
        <span className={styles.markDashed} /> weak
      </span>
    </div>
  );
}
