/** Sessions rail — the workspace's history. Recent interactions across all
 *  three surfaces (web asks, GitHub answers, Slack answers), newest first.
 *  Web asks re-open in the stage prefilled (answers aren't stored for web);
 *  GitHub answers link back to their live threads. Refreshes on a slow poll
 *  and instantly when the stage saves a new episode. */

import type { MouseEvent } from "react";
import { useEffect, useMemo, useRef } from "react";

import { useApi } from "../lib/api";
import { clampText, relTime } from "../lib/format";
import { href, navigate } from "../lib/router";
import type { MemoryEpisode, MemoryResponse } from "../lib/types";
import styles from "./SessionRail.module.css";

const KIND_LABEL: Record<string, string> = {
  "agent-session": "WEB",
  "github-answer": "GH",
  "slack-answer": "SL",
};

function questionOf(episode: MemoryEpisode): string | null {
  if (episode.question) return episode.question;
  if (typeof episode.props.question === "string") return episode.props.question;
  return null;
}

function urlOf(episode: MemoryEpisode): string | null {
  const url = episode.props.url;
  return typeof url === "string" && url.startsWith("http") ? url : null;
}

function onNav(to: string) {
  return (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(to);
  };
}

export function SessionRail() {
  const memory = useApi<MemoryResponse>("/api/graph/memory", 60_000);
  // Reload identity changes with every fetch; route through a ref so the
  // listener below subscribes exactly once.
  const reloadRef = useRef(memory.reload);
  reloadRef.current = memory.reload;

  useEffect(() => {
    const onChange = () => reloadRef.current();
    window.addEventListener("archivist:memory-changed", onChange);
    return () => window.removeEventListener("archivist:memory-changed", onChange);
  }, []);

  const episodes = useMemo(() => {
    const list = (memory.data?.episodes ?? []).filter(
      (episode) => KIND_LABEL[episode.kind] !== undefined,
    );
    return [...list].sort((a, b) => b.ts - a.ts).slice(0, 40);
  }, [memory.data]);

  return (
    <aside className={styles.rail} aria-label="Sessions">
      <div className={styles.head}>
        <a className={styles.brand} href={href("/")} onClick={onNav("/")}>
          Archivist
        </a>
        <span className={styles.sub}>
          {memory.data ? memory.data.graph : "reading memory…"}
        </span>
      </div>

      <button
        type="button"
        className={styles.newBtn}
        onClick={() => navigate(href("/ask", { new: String(Date.now()) }))}
      >
        + New question
      </button>

      <div className={styles.listLabel}>SESSIONS</div>
      <div className={styles.list}>
        {episodes.map((episode) => (
          <Row key={episode.id} episode={episode} />
        ))}
        {episodes.length === 0 ? (
          <p className={styles.empty}>
            {memory.error
              ? "memory unreachable — history hidden"
              : memory.loading
                ? "reading memory…"
                : "no sessions yet — ask something"}
          </p>
        ) : null}
      </div>

      <div className={styles.foot}>
        <a href={href("/explorer")} onClick={onNav("/explorer")}>
          Explorer
        </a>
        <span className={styles.footSep} aria-hidden="true">
          ·
        </span>
        <a href={href("/memory")} onClick={onNav("/memory")}>
          Memory
        </a>
        <span className={styles.footSep} aria-hidden="true">
          ·
        </span>
        <a href={href("/")} onClick={onNav("/")}>
          Home
        </a>
      </div>
    </aside>
  );
}

function Row({ episode }: { episode: MemoryEpisode }) {
  const label = KIND_LABEL[episode.kind] ?? episode.kind;
  const question = questionOf(episode);
  const url = urlOf(episode);
  const badgeClass =
    episode.kind === "github-answer"
      ? styles.badgeGh
      : episode.kind === "slack-answer"
        ? styles.badgeSl
        : null;
  const body = (
    <>
      <span className={styles.rowTop}>
        <span className={badgeClass ? `${styles.badge} ${badgeClass}` : styles.badge}>
          {label}
        </span>
        <span className={styles.rowTime}>{relTime(episode.ts)}</span>
      </span>
      <span className={styles.rowText}>
        {clampText(question ?? episode.id, 120)}
      </span>
    </>
  );

  if (episode.kind === "github-answer" && url) {
    return (
      <a
        className={styles.row}
        href={url}
        target="_blank"
        rel="noreferrer"
        title="Open the thread on GitHub"
      >
        {body}
      </a>
    );
  }
  if (episode.kind === "agent-session" && question) {
    return (
      <button
        type="button"
        className={styles.row}
        title="Re-open this question in the ask stage"
        onClick={() => navigate(href("/ask", { q: question, run: "0" }))}
      >
        {body}
      </button>
    );
  }
  return (
    <div className={styles.row} title="Answered on Slack">
      {body}
    </div>
  );
}
