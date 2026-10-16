/** Step detail — everything recorded for one trace step: status, timings,
 *  params, result summary, replay notes, and the exact Cypher (collapsible,
 *  copyable). Rendered inline in the rail and as the canvas overlay in the
 *  stage; nothing here re-runs a query, so it renders offline for any
 *  recorded answer. */

import { useState } from "react";

import { clampText, formatInt } from "../lib/format";
import { summarizeRows, toolLabel, type StepReplay } from "../lib/trace";
import type { AnswerQuery, AskStep } from "../lib/types";
import styles from "./TraceDetail.module.css";

interface Props {
  step: AskStep;
  queries: AnswerQuery[];
  replay: StepReplay | undefined;
  onClose?: () => void;
  className?: string;
}

export function TraceDetail({ step, queries, replay, onClose, className }: Props) {
  const rows = summarizeRows(step);
  const replayNotes = replay?.notes ?? [];

  return (
    <section
      className={`${styles.detail} ${className ?? ""}`}
      aria-label={`Step detail — ${toolLabel(step.tool)}`}
    >
      <header className={styles.head}>
        <span className={styles.kicker}>step {step.round + 1}</span>
        <span className={styles.tool}>{toolLabel(step.tool)}</span>
        <span className={styles.stat}>{step.ms} ms</span>
        {step.count != null ? (
          <span className={styles.stat}>{formatInt(step.count)} rows</span>
        ) : null}
        {onClose ? (
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label="Close step detail"
          >
            ×
          </button>
        ) : null}
      </header>

      {step.status === "error" ? (
        <p className={styles.err}>{step.error ?? "step failed"}</p>
      ) : null}

      {step.notes.length > 0 ? (
        <ul className={styles.notes}>
          {step.notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      ) : null}

      {rows.length > 0 ? (
        <div className={styles.rows}>
          {rows.map((line, index) => (
            <span key={index} className={styles.row}>
              {line}
            </span>
          ))}
        </div>
      ) : null}

      {replayNotes.length > 0 ? (
        <ul className={styles.notes}>
          {replayNotes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      ) : null}

      {queries.length === 0 ? (
        <p className={styles.noQuery}>No query recorded for this step.</p>
      ) : (
        <div className={styles.queries}>
          {queries.map((query, index) => (
            <QueryBlock key={index} query={query} />
          ))}
        </div>
      )}
    </section>
  );
}

function QueryBlock({ query }: { query: AnswerQuery }) {
  const [status, setStatus] = useState<string | null>(null);
  const paramsJson = JSON.stringify(query.params, null, 2);
  const hasParams = Object.keys(query.params).length > 0;

  async function copy(text: string, what: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setStatus(`${what} copied`);
    } catch {
      setStatus("copy failed");
    }
    window.setTimeout(() => setStatus(null), 1400);
  }

  const first = clampText(query.cypher.split("\n")[0].trim(), 64);

  return (
    <details className={styles.query}>
      <summary className={styles.queryHead}>
        <span className={styles.caret} aria-hidden="true">
          ›
        </span>
        <code className={styles.queryFirst}>{first}</code>
        <span className={styles.queryMeta}>
          {query.ms} ms · {formatInt(query.rows)} rows
        </span>
      </summary>
      <div className={styles.queryBody}>
        <pre className={styles.code}>{query.cypher}</pre>
        {hasParams ? <pre className={styles.code}>{paramsJson}</pre> : null}
        <div className={styles.queryActions}>
          <button
            type="button"
            className={styles.copy}
            onClick={() => void copy(query.cypher, "cypher")}
          >
            copy cypher
          </button>
          {hasParams ? (
            <button
              type="button"
              className={styles.copy}
              onClick={() => void copy(paramsJson, "params")}
            >
              copy params
            </button>
          ) : null}
          {status ? <span className={styles.copyStatus}>{status}</span> : null}
        </div>
      </div>
    </details>
  );
}
