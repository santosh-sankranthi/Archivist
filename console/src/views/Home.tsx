import type { FormEvent } from "react";
import { useState } from "react";

import { useApi } from "../lib/api";
import { clampText, formatInt, relTime, shortSha } from "../lib/format";
import { STARTERS } from "../lib/questions";
import { href, navigate } from "../lib/router";
import type { Activity, GraphState, Overview } from "../lib/types";
import styles from "./Home.module.css";

interface Props {
  state: GraphState | null;
  stateError: string | null;
  overview: Overview | null;
}

/** Home: hero ask line, live stat strip, hubs, recent commits, starters. */
export function Home({ state, stateError, overview }: Props) {
  const activity = useApi<Activity>("/api/graph/activity");
  const [draft, setDraft] = useState("");

  const stats = state
    ? (() => {
        const byLabel = new Map(state.nodes.map((row) => [row.label, row.count]));
        return {
          nodes: state.nodes.reduce((sum, row) => sum + row.count, 0),
          edges: state.edges.reduce((sum, row) => sum + row.count, 0),
          files: byLabel.get("File") ?? 0,
          symbols: byLabel.get("Symbol") ?? 0,
          commits: byLabel.get("Commit") ?? 0,
        };
      })()
    : null;

  function ask(event: FormEvent): void {
    event.preventDefault();
    const question = draft.trim();
    if (!question) return;
    navigate(href("/ask", { q: question }));
  }

  return (
    <div className={styles.home}>
      <section className={styles.hero}>
        <h1 className={styles.heroTitle}>Ask the archive.</h1>
        <p className={styles.heroSub}>
          Flask&rsquo;s code, commits, and memory — one graph. Every answer
          shows its path.
        </p>
        <form className={styles.askRow} onSubmit={ask}>
          <input
            className={styles.askInput}
            type="text"
            placeholder="Ask the archive…"
            aria-label="Ask the archive"
            autoComplete="off"
            spellCheck={false}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button type="submit" className={styles.askBtn}>
            Ask
          </button>
        </form>
        <div className={styles.starters}>
          <span className={styles.starterLabel}>Start with</span>
          {STARTERS.map((question) => (
            <button
              key={question}
              type="button"
              className={styles.starter}
              onClick={() => {
                setDraft(question);
                navigate(href("/ask", { q: question }));
              }}
            >
              {clampText(question, 56)}
            </button>
          ))}
        </div>
      </section>

      {stats ? (
        <section className={styles.stats} aria-label="Graph totals">
          <Stat value={stats.nodes} label="Nodes" />
          <Stat value={stats.edges} label="Edges" />
          <Stat value={stats.files} label="Files" />
          <Stat value={stats.symbols} label="Symbols" />
          <Stat value={stats.commits} label="Commits" />
        </section>
      ) : stateError ? (
        <p className={styles.loadingError}>{stateError}</p>
      ) : (
        <p className={styles.loading}>reading the graph…</p>
      )}

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionLabel}>Hubs</h2>
          <span className={styles.sectionNote}>
            called and imported, live from the graph
          </span>
        </div>
        <div className={styles.hubs}>
          <div className={styles.card}>
            <div className={styles.cardHead}>
              <span className={styles.cardTitle}>Most called</span>
              <span className={styles.cardNote}>fan-in over CALLS</span>
            </div>
            {(overview?.hubs.symbols ?? []).slice(0, 8).map((hit) => (
              <button
                key={`${hit.file}:${hit.qualname}`}
                type="button"
                className={styles.hubRow}
                onClick={() =>
                  navigate(
                    href("/explorer", { file: hit.file, qualname: hit.qualname }),
                  )
                }
              >
                <span className={styles.hubName}>{hit.name}</span>
                <span className={styles.hubFile} title={`${hit.file}`}>
                  {hit.file}
                </span>
                <span className={styles.hubCount}>
                  {formatInt(hit.callers)} callers
                </span>
              </button>
            ))}
          </div>
          <div className={styles.card}>
            <div className={styles.cardHead}>
              <span className={styles.cardTitle}>Most imported</span>
              <span className={styles.cardNote}>fan-in over IMPORTS</span>
            </div>
            {(overview?.hubs.modules ?? []).slice(0, 8).map((hit) => (
              <button
                key={hit.name}
                type="button"
                className={styles.hubRow}
                onClick={() => navigate(href("/explorer", { module: hit.name }))}
              >
                <span className={styles.hubName}>{hit.name}</span>
                <span className={styles.hubFile}>
                  {formatInt(hit.sites)} import sites
                </span>
                <span className={styles.hubCount}>
                  {formatInt(hit.importers)} importers
                </span>
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className={styles.section}>
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionLabel}>Recent commits</h2>
          <span className={styles.sectionNote}>
            newest first, straight from history
          </span>
        </div>
        <div className={styles.card}>
          {activity.data
            ? activity.data.commits.slice(0, 8).map((commit) => (
                <div key={commit.sha} className={styles.commitRow}>
                  <span className={styles.sha}>{shortSha(commit.sha)}</span>
                  <span className={styles.commitSummary} title={commit.summary}>
                    {commit.summary}
                  </span>
                  <span className={styles.commitMeta}>
                    {commit.author ?? "unknown"} · {relTime(commit.ts)} ·{" "}
                    {formatInt(commit.files)} files
                  </span>
                </div>
              ))
            : activity.error
              ? <p className={styles.loading}>{activity.error.message}</p>
              : <p className={styles.loading}>reading history…</p>}
        </div>
      </section>
    </div>
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
