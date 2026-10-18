/** SessionPeek — one stored session, opened read-only from the rail
 *  (?s=<id> in the ask workspace). Web asks, GitHub answers, and Slack
 *  answers all become browsable here; "Ask again" turns any stored
 *  question back into a live run in the stage. */

import { useMemo } from "react";

import { useApi } from "../lib/api";
import { relTime, stamp } from "../lib/format";
import { href, navigate } from "../lib/router";
import type { MemoryResponse } from "../lib/types";
import styles from "./SessionPeek.module.css";

const KIND_LABEL: Record<string, string> = {
  "agent-session": "WEB",
  "github-answer": "GH",
  "slack-answer": "SL",
};

function propText(props: Record<string, unknown>, key: string): string | null {
  const value = props[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function propNumber(props: Record<string, unknown>, key: string): number | null {
  const value = props[key];
  return typeof value === "number" ? value : null;
}

interface Props {
  episodeId: string;
}

export function SessionPeek({ episodeId }: Props) {
  const memory = useApi<MemoryResponse>("/api/graph/memory");
  const episode = useMemo(
    () =>
      (memory.data?.episodes ?? []).find((item) => item.id === episodeId) ??
      null,
    [memory.data, episodeId],
  );

  if (!episode) {
    return (
      <section className={styles.page} aria-label="Stored session">
        <article className={styles.card}>
          <p className={styles.miss}>
            {memory.error
              ? "Memory is unreachable right now — try another session or ask something new."
              : memory.loading
                ? "Opening session…"
                : "That session has rolled out of the window — it isn't stored anymore."}
          </p>
          <footer className={styles.actions}>
            <button
              type="button"
              className={styles.primary}
              onClick={() => navigate(href("/ask", { new: String(Date.now()) }))}
            >
              + New question
            </button>
          </footer>
        </article>
      </section>
    );
  }

  const label = KIND_LABEL[episode.kind] ?? episode.kind;
  const question = episode.question ?? propText(episode.props, "question");
  const url = propText(episode.props, "url");
  const model = propText(episode.props, "model");
  const graph = propText(episode.props, "graph");
  const channel = propText(episode.props, "channel");
  const steps = propNumber(episode.props, "steps");
  const queries = propNumber(episode.props, "queries");

  return (
    <section className={styles.page} aria-label="Stored session">
      <article className={styles.card}>
        <header className={styles.head}>
          <span
            className={`${styles.badge} ${
              episode.kind === "github-answer"
                ? styles.badgeGh
                : episode.kind === "slack-answer"
                  ? styles.badgeSl
                  : ""
            }`}
          >
            {label}
          </span>
          <span className={styles.when}>
            {relTime(episode.ts)} · {stamp(episode.ts)}
          </span>
          <button
            type="button"
            className={styles.close}
            onClick={() => navigate(href("/ask"))}
          >
            ✕ Close
          </button>
        </header>

        <h2 className={styles.question}>{question ?? "(no stored question)"}</h2>

        {graph || model || steps !== null || queries !== null || channel ? (
          <div className={styles.chips}>
            {graph ? <span className={styles.chip}>{graph}</span> : null}
            {channel ? <span className={styles.chip}>{channel}</span> : null}
            {model ? <span className={styles.chip}>{model}</span> : null}
            {steps !== null ? (
              <span className={styles.chip}>{steps} steps</span>
            ) : null}
            {queries !== null ? (
              <span className={styles.chip}>{queries} queries</span>
            ) : null}
          </div>
        ) : null}

        {episode.answer ? (
          <p className={styles.answer}>{episode.answer}</p>
        ) : (
          <p className={styles.noAnswer}>
            This run didn&apos;t store an answer
            {episode.kind === "agent-session"
              ? " (it may have been interrupted mid-stream)"
              : ""}{" "}
            — ask it again to see the live trace.
          </p>
        )}

        <footer className={styles.actions}>
          {question ? (
            <button
              type="button"
              className={styles.primary}
              onClick={() => navigate(href("/ask", { q: question }))}
            >
              Ask again
            </button>
          ) : null}
          {url ? (
            <a
              className={styles.secondary}
              href={url}
              target="_blank"
              rel="noreferrer"
            >
              Open on GitHub ↗
            </a>
          ) : null}
          <button
            type="button"
            className={styles.secondary}
            onClick={() => navigate(href("/ask", { new: String(Date.now()) }))}
          >
            + New question
          </button>
        </footer>
      </article>
    </section>
  );
}
