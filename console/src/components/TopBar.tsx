import { formatInt, shortSha } from "../lib/format";
import type { GraphState } from "../lib/types";
import styles from "./TopBar.module.css";

interface Props {
  state: GraphState | null;
  onOpenPalette: () => void;
}

/** Top bar: wordmark lockup, repo chip with live counts, LLM status, ⌘K hint. */
export function TopBar({ state, onOpenPalette }: Props) {
  const isMac =
    typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const counts = state
    ? {
        nodes: state.nodes.reduce((sum, row) => sum + row.count, 0),
        edges: state.edges.reduce((sum, row) => sum + row.count, 0),
      }
    : null;
  const llm = state?.llm ?? null;

  return (
    <header className={styles.bar}>
      <div className={styles.brand}>
        <span className={styles.wordmark}>Archivist</span>
        <span className={styles.tagline}>graph console</span>
      </div>

      <div className={styles.right}>
        <span className={styles.chip}>
          <span className={styles.mono}>{state?.graph ?? "repo:flask"}</span>
          {state?.repo?.head ? (
            <span className={styles.dim}>· head {shortSha(state.repo.head)}</span>
          ) : null}
        </span>

        {counts ? (
          <span className={styles.chip} title="live node / edge counts">
            <span className={styles.mono}>{formatInt(counts.nodes)}</span>
            <span className={styles.dim}>nodes</span>
            <span className={styles.dim}>·</span>
            <span className={styles.mono}>{formatInt(counts.edges)}</span>
            <span className={styles.dim}>edges</span>
          </span>
        ) : null}

        <span
          className={styles.chip}
          title={
            llm?.configured
              ? `answers enabled — ${llm.model ?? "model set"}`
              : "no LLM key on this box — the explorer stays fully live; asking is disabled"
          }
        >
          <span
            className={llm?.configured ? styles.dotOn : styles.dotOff}
            aria-hidden="true"
          />
          {llm === null ? "llm —" : llm.configured ? `llm ${llm.model ?? "ready"}` : "llm not set"}
        </span>

        <button
          type="button"
          className={styles.kbd}
          onClick={onOpenPalette}
          aria-label="Open the command palette (Command or Control K)"
          title="Command palette — files, symbols, actions"
        >
          {isMac ? "⌘K" : "Ctrl K"}
        </button>
      </div>
    </header>
  );
}
