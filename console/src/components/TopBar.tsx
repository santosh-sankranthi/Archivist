import { formatInt, shortSha } from "../lib/format";
import { href, navigate, useLocation } from "../lib/router";
import { useSession } from "../lib/session";
import { graphTotals } from "../lib/stats";
import type { GraphState } from "../lib/types";
import styles from "./TopBar.module.css";

interface Props {
  state: GraphState | null;
  /** Workspace variant: drop telemetry chips so the stage stays the focus. */
  minimal?: boolean;
  onOpenPalette: () => void;
  onOpenSettings: () => void;
}

/** A person glyph separates the account control from the telemetry chips it
 *  sits beside — same size, but it reads as "this is you / this is your way
 *  in", not another status readout (Gate A W3). */
function PersonGlyph() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden="true"
      className={styles.person}
    >
      <circle cx="6" cy="3.8" r="2.2" stroke="currentColor" strokeWidth="1.2" />
      <path
        d="M1.8 10.4c.5-2 2.2-3.2 4.2-3.2s3.7 1.2 4.2 3.2"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Top bar: wordmark lockup, repo chip with live counts, LLM status, ⌘K hint. */
export function TopBar({ state, minimal = false, onOpenPalette, onOpenSettings }: Props) {
  const isMac =
    typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const totals = state ? graphTotals(state) : null;
  const counts = totals ? { nodes: totals.nodes, edges: totals.edges } : null;
  const llm = state?.llm ?? null;
  const session = useSession();
  const { path } = useLocation();
  // The chip would point at the page you are already on; keep the form clean.
  const onAuthPage = path === "/login" || path === "/signup";

  return (
    <header className={styles.bar}>
      <div className={styles.brand}>
        <span className={styles.wordmark}>Archivist</span>
        {minimal ? null : <span className={styles.tagline}>graph console</span>}
      </div>

      <div className={styles.right}>
        <span className={styles.chip}>
          <span className={styles.mono}>{state?.graph ?? "repo:flask"}</span>
          {state?.repo?.head ? (
            <span className={styles.dim}>· head {shortSha(state.repo.head)}</span>
          ) : null}
        </span>

        {counts && !minimal ? (
          <span className={styles.chip} title="live node / edge counts">
            <span className={styles.mono}>{formatInt(counts.nodes)}</span>
            <span className={styles.dim}>nodes</span>
            <span className={styles.dim}>·</span>
            <span className={styles.mono}>{formatInt(counts.edges)}</span>
            <span className={styles.dim}>edges</span>
          </span>
        ) : null}

        {minimal ? null : (
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
        )}

        {!onAuthPage && session.me?.user ? (
          <button
            type="button"
            className={`${styles.chip} ${styles.account}`}
            onClick={() => navigate(href("/account"))}
            title="Your account"
          >
            <PersonGlyph />
            <span className={styles.mono}>@{session.me.user.username}</span>
          </button>
        ) : !onAuthPage && session.loaded ? (
          <button
            type="button"
            className={`${styles.chip} ${styles.account}`}
            onClick={() => navigate(href("/login"))}
            title="Sign in — or keep browsing the demo"
          >
            <PersonGlyph />
            <span>Sign in</span>
          </button>
        ) : null}

        <button
          type="button"
          className={styles.kbd}
          onClick={onOpenPalette}
          aria-label="Open the command palette (Command or Control K)"
          title="Command palette — files, symbols, actions"
        >
          {isMac ? "⌘K" : "Ctrl K"}
        </button>
        <button
          type="button"
          className={styles.menu}
          onClick={onOpenPalette}
          aria-label="Open menu"
          title="Menu — views and search"
        >
          Menu
        </button>
        <button
          type="button"
          className={styles.settings}
          onClick={onOpenSettings}
          aria-label="Open settings"
          title="Settings — bring your own LLM key"
        >
          settings
        </button>
      </div>
    </header>
  );
}
