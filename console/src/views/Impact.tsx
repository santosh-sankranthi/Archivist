import { motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";

import { useApi } from "../lib/api";
import { formatInt } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import {
  chainLabel,
  chainTitle,
  DEPTHS,
  fileTail,
  hopLabel,
  impactPath,
  parseDepth,
  ringStrength,
  rollupFiles,
  shortName,
  visibleRing,
} from "../lib/impact";
import { EASE_OUT } from "../lib/motion";
import { href, navigate, useLocation } from "../lib/router";
import type {
  FindResult,
  ImpactLevel,
  ImpactResponse,
  ImpactSummary,
  ImpactTarget,
  Overview,
} from "../lib/types";
import styles from "./Impact.module.css";

/** Blast radius (M4R runway): the inverse of the Explorer's "what does this
 *  call?" — what calls this, transitively, and who should be warned. The
 *  server walks reverse CALLS edges once per symbol at its shortest
 *  distance; this view renders that cone as rings, an ordered ladder, and a
 *  source/test file rollup. Guessed (weak) edges are excluded by default and
 *  the hidden count is surfaced, never silently dropped. */
export function Impact() {
  const { query } = useLocation();
  const symbol = query.get("symbol");
  const file = query.get("file");
  const [depth, setDepth] = useState(() => parseDepth(query.get("depth")));
  const [includeWeak, setIncludeWeak] = useState(
    () => query.get("include_weak") === "1",
  );
  const [expandedRings, setExpandedRings] = useState<Record<number, boolean>>({});
  const [openChain, setOpenChain] = useState<string | null>(null);

  // A URL is the source of truth on arrival and on back/forward; local
  // depth/weak state only exists so a control tweak does not jump the page.
  const queryDepth = parseDepth(query.get("depth"));
  const queryWeak = query.get("include_weak") === "1";
  useEffect(() => {
    setDepth(queryDepth);
    setIncludeWeak(queryWeak);
    setOpenChain(null);
    setExpandedRings({});
  }, [symbol, file, queryDepth, queryWeak]);

  const result = useApi<ImpactResponse>(
    symbol ? impactPath({ symbol, file, depth, includeWeak }) : null,
  );
  const data = result.data;

  // Depth/weak changes stay on this symbol; the URL is kept shareable but a
  // tweak never scrolls the report back to its header.
  function commit(nextDepth: number, nextWeak: boolean): void {
    setDepth(nextDepth);
    setIncludeWeak(nextWeak);
    if (!symbol) return;
    const params: Record<string, string> = { symbol };
    if (file) params.file = file;
    params.depth = String(nextDepth);
    if (nextWeak) params.include_weak = "1";
    window.history.replaceState(null, "", href("/impact", params));
  }

  return (
    <div className={styles.impact} data-impact={data ? "ready" : "loading"}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Blast radius</span>
        <h1 className={styles.title}>What breaks if this changes.</h1>
        <p className={styles.lead}>
          The reverse call cone above one symbol. Every row is a caller the
          graph resolved at its shortest distance, with the chain that reaches
          the target — no inferred edges, no guessed distances. Guessed edges
          are excluded by default and their callers are counted.
        </p>
      </header>

      {!symbol ? <Gate /> : null}

      {symbol ? (
        <>
          <div className={styles.controls}>
            <span className={styles.controlLabel}>walk depth</span>
            <div className={styles.depthChips} role="group" aria-label="Walk depth">
              {DEPTHS.map((value) => (
                <button
                  key={value}
                  type="button"
                  className={styles.depthChip}
                  aria-pressed={value === depth}
                  onClick={() => commit(value, includeWeak)}
                >
                  {value}
                </button>
              ))}
            </div>
            <button
              type="button"
              className={styles.weakToggle}
              aria-pressed={includeWeak}
              onClick={() => commit(depth, !includeWeak)}
              title="Include call edges the resolver only guessed (flagged weak)"
            >
              {includeWeak ? "guessed edges on" : "guessed edges hidden"}
            </button>
            {data ? (
              <a
                className={styles.explorerLink}
                href={href("/explorer", { file: data.target.file, qualname: data.target.qualname })}
                onClick={(event) => {
                  event.preventDefault();
                  navigate(
                    href("/explorer", {
                      file: data.target.file,
                      qualname: data.target.qualname,
                    }),
                  );
                }}
              >
                open in explorer →
              </a>
            ) : null}
          </div>

          {result.error ? (
            <div className={styles.errorPanel}>
              <h2 className={styles.errorTitle}>No single target.</h2>
              <p className={styles.errorBody}>{result.error.message}</p>
              <p className={styles.errorHint}>
                Pass a file scope or the exact qualname — the search below
                returns both.
              </p>
              <Gate compact />
            </div>
          ) : null}

          {!data && !result.error ? (
            <p className={styles.loading}>walking the call graph…</p>
          ) : null}

          {data ? (
            <>
              <TargetBar target={data.target} />
              <Stats summary={data.summary} depth={data.params.depth} />

              {data.summary.transitive_callers === 0 ? (
                <div className={styles.entry}>
                  <div className={styles.entryDial}>
                    <ConeDial levels={data.levels} depth={data.params.depth} />
                  </div>
                  <div className={styles.entryBody}>
                    <h2 className={styles.entryTitle}>An entry point.</h2>
                    <p className={styles.entryText}>
                      Nothing in this graph resolves a call into{" "}
                      <span className={styles.mono}>{data.target.qualname}</span>{" "}
                      within {hopLabel(data.params.depth)}. Either it is called
                      from outside the archive — a route handler, a CLI entry, a
                      test fixture — or its callers were not resolvable.
                    </p>
                    <p className={styles.entryHint}>
                      Widen the walk, or ask the archive why it is unreachable.
                    </p>
                  </div>
                </div>
              ) : (
                <div className={styles.body}>
                  <aside className={styles.dialPane}>
                    <ConeDial levels={data.levels} depth={data.params.depth} />
                    <p className={styles.dialCaption}>
                      Rings are hops, not time. Counts at each ring are the
                      exact number of affected symbols at that distance.
                    </p>
                  </aside>

                  <div className={styles.rings}>
                    {data.levels.map((level, index) => (
                      <Ring
                        key={level.hops}
                        level={level}
                        index={index}
                        maxHops={data.summary.max_hops}
                        expanded={Boolean(expandedRings[level.hops])}
                        onExpand={() =>
                          setExpandedRings((prev) => ({
                            ...prev,
                            [level.hops]: true,
                          }))
                        }
                        openChain={openChain}
                        onChain={(key) =>
                          setOpenChain((prev) => (prev === key ? null : key))
                        }
                      />
                    ))}
                  </div>
                </div>
              )}

              <Files files={data.files} summary={data.summary} />

              {data.notes.length > 0 ? (
                <ul className={styles.notes} aria-label="Notes">
                  {data.notes.map((note) => (
                    <li key={note} className={styles.note}>
                      {note}
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

/* -- gate --------------------------------------------------------------- */

/** No symbol yet: search the live graph, or start from the most-called
 *  symbols — the questions a visitor actually has. */
function Gate({ compact = false }: { compact?: boolean }) {
  const [text, setText] = useState("");
  const debounced = useDebounced(text.trim(), 220);
  const searching = debounced.length >= 2;
  const find = useApi<FindResult>(
    searching ? `/api/graph/find?q=${encodeURIComponent(debounced)}&limit=8` : null,
  );
  const overview = useApi<Overview>("/api/graph/overview");

  const hits = find.data?.symbols ?? [];

  return (
    <section className={`${styles.gate} ${compact ? styles.gateCompact : ""}`}>
      <label className={styles.gateLabel} htmlFor="impact-search">
        Start from a symbol
      </label>
      <input
        id="impact-search"
        className={styles.gateInput}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="Function or method — url_for, dispatch_request…"
        autoComplete="off"
        spellCheck={false}
      />
      {text.trim().length > 0 && !searching ? (
        <p className={styles.gateHint}>Type at least 2 characters to search.</p>
      ) : null}
      {searching ? (
        <div className={styles.gateResults}>
          {find.loading && hits.length === 0 ? (
            <p className={styles.gateHint}>searching the graph…</p>
          ) : null}
          {searching && find.data && hits.length === 0 ? (
            <p className={styles.gateHint}>No symbol matches in the archive.</p>
          ) : null}
          {hits.map((hit) => (
            <button
              key={`${hit.file}:${hit.qualname}`}
              type="button"
              className={styles.gateRow}
              onClick={() => navigate(href("/impact", { symbol: hit.qualname, file: hit.file }))}
            >
              <span className={styles.gateRowName}>{hit.name}</span>
              <span className={styles.gateRowMeta}>
                {hit.file}:{hit.lineno} · {hit.kind}
              </span>
            </button>
          ))}
        </div>
      ) : null}

      {!compact && overview.data ? (
        <>
          <p className={styles.gateOr}>
            or start from a hub — the most-called symbols in this repo
          </p>
          <div className={styles.hubs}>
            {overview.data.hubs.symbols.slice(0, 6).map((hub) => (
              <button
                key={`${hub.file}:${hub.qualname}`}
                type="button"
                className={styles.hub}
                onClick={() =>
                  navigate(href("/impact", { symbol: hub.qualname, file: hub.file }))
                }
              >
                <span className={styles.hubName} title={hub.qualname}>
                  {hub.qualname}
                </span>
                <span className={styles.hubMeta}>
                  {formatInt(hub.callers)} callers · {fileTail(hub.file)}
                </span>
              </button>
            ))}
          </div>
        </>
      ) : null}
    </section>
  );
}

/* -- target + stats ----------------------------------------------------- */

function TargetBar({ target }: { target: ImpactTarget }) {
  return (
    <div className={styles.target} data-impact-target="">
      <div className={styles.targetMain}>
        <span className={styles.targetKind}>{target.kind}</span>
        <span className={styles.targetName}>{target.qualname}</span>
        <span className={styles.targetFile}>
          {target.file}
          {target.lineno !== null ? `:${target.lineno}` : ""}
        </span>
      </div>
      <div className={styles.targetActions}>
        <button
          type="button"
          className={styles.targetBtn}
          onClick={() =>
            navigate(href("/ask", { q: `What breaks if ${target.qualname} changes?` }))
          }
        >
          ask about this
        </button>
      </div>
    </div>
  );
}

function Stats({ summary, depth }: { summary: ImpactSummary; depth: number }) {
  return (
    <section className={styles.stats} aria-label="Impact summary">
      <Stat value={summary.direct_callers} label="direct callers" />
      <Stat value={summary.transitive_callers} label="transitive callers" />
      <Stat value={summary.affected_files} label="affected files" />
      <Stat value={summary.affected_test_files} label="test files" />
      <Stat value={summary.max_hops} label={`of ${depth} hops`} />
    </section>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue}>{formatInt(value)}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  );
}

/* -- cone dial ---------------------------------------------------------- */

/** Concentric rings around the target. Filled amber rings are distances
 *  with callers (count printed); dashed grey rings are distances the walk
 *  covered but where nothing resolved — the shape of the cone, honestly. */
function ConeDial({ levels, depth }: { levels: ImpactLevel[]; depth: number }) {
  const W = 280;
  const H = 230;
  const cx = 104;
  const cy = 116;
  const maxR = 84;
  const byHop = useMemo(
    () => new Map(levels.map((level) => [level.hops, level.symbols.length])),
    [levels],
  );
  const hops = Array.from({ length: depth }, (_, index) => index + 1);
  const total = levels.reduce((sum, level) => sum + level.symbols.length, 0);
  // A floor for the first ring: the direct callers deserve a readable ring,
  // not a dot. Deeper rings space out evenly to maxR.
  const innerR = 24;

  return (
    <div className={styles.dial}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className={styles.dialSvg}
        role="img"
        aria-label={`Impact cone: ${total} affected symbols across ${depth} hops`}
      >
        {hops.map((hop) => {
          const radius =
            depth === 1
              ? maxR * 0.55
              : innerR + ((hop - 1) / (depth - 1)) * (maxR - innerR);
          const count = byHop.get(hop) ?? 0;
          const strength = ringStrength(hop, depth);
          return (
            <g key={hop}>
              <motion.circle
                cx={cx}
                cy={cy}
                r={radius}
                fill="none"
                stroke={count > 0 ? "var(--amber)" : "var(--hairline-strong)"}
                strokeOpacity={count > 0 ? 0.16 + 0.54 * strength : 0.55}
                strokeWidth={count > 0 ? 1.5 : 1}
                strokeDasharray={count > 0 ? undefined : "2 5"}
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: 1 }}
                transition={{ duration: 0.85, ease: EASE_OUT, delay: 0.07 * hop }}
              />
              {count > 0 ? (
                <text
                  x={cx}
                  y={cy - radius - 5}
                  textAnchor="middle"
                  className={styles.dialCount}
                >
                  {count}
                </text>
              ) : null}
            </g>
          );
        })}
        <circle cx={cx} cy={cy} r={3.5} fill="var(--amber)" />
        <text x={cx} y={cy + 17} textAnchor="middle" className={styles.dialTarget}>
          target
        </text>
      </svg>
      <div className={styles.dialScale} aria-hidden="true">
        {hops.map((hop) => {
          const count = byHop.get(hop) ?? 0;
          return (
            <span
              key={hop}
              className={count > 0 ? styles.scaleItem : styles.scaleItemEmpty}
            >
              <span className={styles.scaleHop}>{hop}h</span>
              <span className={styles.scaleCount}>{count > 0 ? count : "—"}</span>
            </span>
          );
        })}
      </div>
      <div className={styles.dialLegend} aria-hidden="true">
        <span className={styles.legendItem}>
          <span className={styles.legendDash} /> dashed ring — no callers resolved
        </span>
      </div>
    </div>
  );
}

/* -- ring ladder -------------------------------------------------------- */

function Ring({
  level,
  index,
  maxHops,
  expanded,
  onExpand,
  openChain,
  onChain,
}: {
  level: ImpactLevel;
  index: number;
  maxHops: number;
  expanded: boolean;
  onExpand: () => void;
  openChain: string | null;
  onChain: (key: string) => void;
}) {
  const { shown, hidden } = visibleRing(level.symbols, expanded);
  const strength = ringStrength(level.hops, maxHops);
  const adjective =
    level.hops === 1 ? "direct callers" : level.hops === maxHops ? "outermost" : "indirect";

  return (
    <motion.section
      className={styles.ring}
      style={{ "--ring-strength": strength } as React.CSSProperties}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, ease: EASE_OUT, delay: Math.min(index, 6) * 0.05 }}
      aria-label={`${hopLabel(level.hops)} — ${formatInt(level.symbols.length)} callers`}
    >
      <header className={styles.ringHead}>
        <span className={styles.ringHop}>{hopLabel(level.hops)}</span>
        <span className={styles.ringRule} aria-hidden="true" />
        <span className={styles.ringCount}>{formatInt(level.symbols.length)}</span>
        <span className={styles.ringAdj}>{adjective}</span>
      </header>

      <div className={styles.ringRows}>
        {shown.map((symbol) => {
          const key = `${symbol.file}:${symbol.qualname}`;
          const open = openChain === key;
          return (
            <div key={key} className={styles.rowWrap}>
              <button
                type="button"
                className={styles.row}
                aria-expanded={open}
                onClick={() => onChain(key)}
              >
                <span className={styles.rowName}>{symbol.qualname}</span>
                <span className={styles.rowFile}>{fileTail(symbol.file)}</span>
                <span
                  className={styles.rowCount}
                  title={
                    (symbol.counts[0] ?? 0) > 1
                      ? "calls on the first edge of this chain"
                      : undefined
                  }
                >
                  {(symbol.counts[0] ?? 0) > 1
                    ? `×${formatInt(symbol.counts[0] ?? 0)}`
                    : ""}
                </span>
                {symbol.weak ? <span className={styles.weak}>weak</span> : null}
                <span className={styles.rowCaret} aria-hidden="true">
                  {open ? "−" : "+"}
                </span>
              </button>
              {open ? (
                <div className={styles.chain}>
                  <div className={styles.chainPath} title={chainTitle(symbol.chain)}>
                    {chainLabel(symbol.chain)}
                  </div>
                  <button
                    type="button"
                    className={styles.chainLink}
                    onClick={() =>
                      navigate(href("/explorer", { file: symbol.file, qualname: symbol.qualname }))
                    }
                  >
                    open {shortName(symbol.qualname)} in explorer →
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      {hidden > 0 ? (
        <button type="button" className={styles.more} onClick={onExpand}>
          show {formatInt(hidden)} more in this ring
        </button>
      ) : null}
    </motion.section>
  );
}

/* -- file rollup -------------------------------------------------------- */

function Files({
  files,
  summary,
}: {
  files: ImpactResponse["files"];
  summary: ImpactSummary;
}) {
  const rollup = useMemo(() => rollupFiles(files), [files]);
  if (files.length === 0) return null;

  return (
    <section className={styles.files} aria-label="Affected files">
      <header className={styles.filesHead}>
        <span className={styles.sectionLabel}>Affected files</span>
        <span className={styles.sectionNote}>
          {formatInt(summary.affected_files)} total · {formatInt(summary.affected_test_files)}{" "}
          test
        </span>
      </header>
      <div className={styles.fileColumns}>
        <FileColumn
          title="Source"
          files={rollup.source}
          symbols={rollup.sourceSymbols}
          empty="No source file in this cone."
        />
        <FileColumn
          title="Tests (path heuristic)"
          files={rollup.tests}
          symbols={rollup.testSymbols}
          empty="No test file in this cone — check coverage before changing this."
        />
      </div>
    </section>
  );
}

function FileColumn({
  title,
  files,
  symbols,
  empty,
}: {
  title: string;
  files: ImpactResponse["files"];
  symbols: number;
  empty: string;
}) {
  return (
    <div className={styles.fileColumn}>
      <header className={styles.fileColumnHead}>
        <span>{title}</span>
        <span className={styles.fileColumnMeta}>
          {formatInt(files.length)} files · {formatInt(symbols)} symbols
        </span>
      </header>
      {files.length === 0 ? (
        <p className={styles.fileEmpty}>{empty}</p>
      ) : (
        files.map((file) => (
          <button
            key={file.path}
            type="button"
            className={styles.fileRow}
            title={file.path}
            onClick={() => navigate(href("/explorer", { path: file.path }))}
          >
            <span className={styles.filePath}>{fileTail(file.path)}</span>
            <span className={styles.fileMeta}>
              {formatInt(file.symbols)} · {hopLabel(file.min_hops)}
            </span>
          </button>
        ))
      )}
    </div>
  );
}
