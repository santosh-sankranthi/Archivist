import { motion } from "motion/react";
import { useState } from "react";

import { useApi } from "../lib/api";
import { formatInt } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { NAV_SPRING } from "../lib/motion";
import { href, navigate } from "../lib/router";
import type { FindResult, GraphState, Overview } from "../lib/types";
import styles from "./LeftRail.module.css";

interface Props {
  path: string;
  state: GraphState | null;
  overview: Overview | null;
  onOpenPalette: () => void;
}

const NAV = [
  { path: "/overview", label: "Home" },
  { path: "/explorer", label: "Explorer" },
  { path: "/impact", label: "Impact" },
  { path: "/ask", label: "Ask" },
  { path: "/memory", label: "Memory" },
  { path: "/crew", label: "Crew" },
  { path: "/history", label: "Time machine" },
  { path: "/about", label: "About" },
];

/** Left rail: view nav, live search over the graph, hub quick-lists, memory footer. */
export function LeftRail({ path, state, overview, onOpenPalette }: Props) {
  const [query, setQuery] = useState("");
  const debounced = useDebounced(query.trim(), 250);
  const find = useApi<FindResult>(
    debounced.length >= 2
      ? `/api/graph/find?q=${encodeURIComponent(debounced)}&limit=10`
      : null,
  );

  const symbols = overview?.hubs.symbols.slice(0, 6) ?? [];
  const modules = overview?.hubs.modules.slice(0, 6) ?? [];
  const memory = state?.memory ?? null;

  return (
    <aside className={styles.rail}>
      <nav className={styles.nav} aria-label="Views">
        {NAV.map((item) => {
          const active =
            item.path === path || path.startsWith(`${item.path}/`);
          return (
            <a
              key={item.path}
              href={href(item.path)}
              className={`${styles.navItem} ${active ? styles.navItemActive : ""}`}
              aria-current={active ? "page" : undefined}
              onClick={(event) => {
                if (event.metaKey || event.ctrlKey || event.shiftKey) return;
                event.preventDefault();
                navigate(href(item.path));
              }}
            >
              {active ? (
                <motion.span
                  layoutId="rail-nav"
                  className={styles.navPill}
                  transition={NAV_SPRING}
                  aria-hidden="true"
                />
              ) : null}
              <span className={styles.navLabel}>{item.label}</span>
            </a>
          );
        })}
      </nav>

      <div className={styles.block}>
        <div className={styles.blockHeader}>
          <label className={styles.blockLabel} htmlFor="rail-search">
            Search
          </label>
          <button
            type="button"
            className={styles.paletteKbd}
            onClick={onOpenPalette}
            title="Open the command palette — files, symbols, actions"
          >
            ⌘K
          </button>
        </div>
        <input
          id="rail-search"
          className={styles.search}
          type="search"
          placeholder="Files & symbols…"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className={styles.results} aria-live="polite">
          {debounced.length < 2 ? (
            <p className={styles.hint}>
              Search queries the live graph — try <em>url_for</em> or{" "}
              <em>blueprints</em>.
            </p>
          ) : find.loading ? (
            <p className={styles.hint}>searching…</p>
          ) : find.error ? (
            <p className={styles.hintError}>{find.error.message}</p>
          ) : find.data ? (
            <SearchResults data={find.data} />
          ) : null}
        </div>
      </div>

      <div className={styles.block}>
        <span className={styles.blockLabel}>Most called</span>
        <ul className={styles.hubList}>
          {symbols.map((hit) => (
            <li key={`${hit.file}:${hit.qualname}`}>
              <button
                type="button"
                className={styles.hubRow}
                onClick={() =>
                  navigate(
                    href("/explorer", { file: hit.file, qualname: hit.qualname }),
                  )
                }
                title={`${hit.qualname} — ${hit.file}`}
              >
                <span className={styles.hubName}>{hit.name}</span>
                <span className={styles.hubCount}>{formatInt(hit.callers)}</span>
              </button>
            </li>
          ))}
          {symbols.length === 0 ? (
            <li className={styles.hint}>reading the graph…</li>
          ) : null}
        </ul>
      </div>

      <div className={styles.block}>
        <span className={styles.blockLabel}>Most imported</span>
        <ul className={styles.hubList}>
          {modules.map((hit) => (
            <li key={hit.name}>
              <button
                type="button"
                className={styles.hubRow}
                onClick={() => navigate(href("/explorer", { module: hit.name }))}
                title={`${hit.name} — ${hit.importers} importer(s)`}
              >
                <span className={styles.hubName}>{hit.name}</span>
                <span className={styles.hubCount}>{formatInt(hit.importers)}</span>
              </button>
            </li>
          ))}
          {modules.length === 0 ? (
            <li className={styles.hint}>reading the graph…</li>
          ) : null}
        </ul>
      </div>

      <div className={styles.memory}>
        <span className={styles.dotTeal} aria-hidden="true" />
        {memory
          ? `${formatInt(memory.sessions)} sessions · ${formatInt(memory.facts)} facts in memory`
          : "memory —"}
      </div>
    </aside>
  );
}

function SearchResults({ data }: { data: FindResult }) {
  const empty = data.symbols.length === 0 && data.files.length === 0;
  if (empty) {
    return <p className={styles.hint}>No matches in the archive.</p>;
  }
  return (
    <>
      {data.symbols.length > 0 ? (
        <>
          <span className={styles.groupLabel}>Symbols</span>
          <ul className={styles.resultList}>
            {data.symbols.map((hit) => (
              <li key={`${hit.file}:${hit.qualname}`}>
                <button
                  type="button"
                  className={styles.resultRow}
                  onClick={() =>
                    navigate(
                      href("/explorer", { file: hit.file, qualname: hit.qualname }),
                    )
                  }
                >
                  <span className={styles.resultName}>{hit.name}</span>
                  <span className={styles.resultMeta}>
                    {hit.file}:{hit.lineno} · {hit.kind}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {data.files.length > 0 ? (
        <>
          <span className={styles.groupLabel}>Files</span>
          <ul className={styles.resultList}>
            {data.files.map((hit) => (
              <li key={hit.path}>
                <button
                  type="button"
                  className={styles.resultRow}
                  onClick={() => navigate(href("/explorer", { path: hit.path }))}
                >
                  <span className={styles.resultName}>{hit.path}</span>
                  <span className={styles.resultMeta}>
                    {formatInt(hit.loc)} loc
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {data.notes.length > 0 ? (
        <p className={styles.hint}>{data.notes.join(" · ")}</p>
      ) : null}
    </>
  );
}
