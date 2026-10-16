import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { ApiError, postJSON } from "../lib/api";
import type { AskResponse } from "../lib/types";
import styles from "./AskPanel.module.css";

type Phase =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; data: AskResponse }
  | { kind: "error"; error: ApiError };

interface Props {
  /** Prefilled question (from the hero input via ?q=). */
  initialQuestion?: string | null;
  /** Run the prefilled question immediately (Ask view deep links). */
  autoRun?: boolean;
}

/** The ask field + honest result states. No key on the box -> a designed
 *  503 state, never an error dump. */
export function AskPanel({ initialQuestion = null, autoRun = false }: Props) {
  const [draft, setDraft] = useState(initialQuestion ?? "");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  async function run(question: string): Promise<void> {
    setPhase({ kind: "running" });
    try {
      const data = await postJSON<AskResponse>("/api/ask", {
        question,
        max_steps: 6,
      });
      setPhase({ kind: "done", data });
    } catch (error) {
      const apiError =
        error instanceof ApiError ? error : new ApiError(0, String(error));
      setPhase({ kind: "error", error: apiError });
    }
  }

  const ranRef = useRef(false);
  useEffect(() => {
    if (autoRun && initialQuestion && !ranRef.current) {
      ranRef.current = true;
      void run(initialQuestion);
    }
    // run is stable enough for this one-shot; guards prevent re-entry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, initialQuestion]);

  function submit(event: FormEvent): void {
    event.preventDefault();
    const question = draft.trim();
    if (!question || phase.kind === "running") return;
    void run(question);
  }

  return (
    <div className={styles.panel}>
      <form className={styles.form} onSubmit={submit}>
        <input
          className={styles.input}
          type="text"
          placeholder="Ask the archive…"
          aria-label="Ask the archive"
          autoComplete="off"
          spellCheck={false}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button
          type="submit"
          className={styles.submit}
          disabled={phase.kind === "running" || draft.trim().length === 0}
        >
          {phase.kind === "running" ? "Asking…" : "Ask"}
        </button>
      </form>

      {phase.kind === "running" ? (
        <div className={styles.running} aria-live="polite">
          <span>Working the graph</span>
          <span className={styles.pulse} aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
        </div>
      ) : null}

      {phase.kind === "error" && phase.error.status === 503 ? (
        <div className={styles.noKey} role="status">
          <h2 className={styles.noKeyTitle}>The console can&rsquo;t talk yet.</h2>
          <p className={styles.noKeyBody}>
            No LLM key is configured on this box, so conversations are switched
            off — every other surface on this screen reads the live graph. To
            turn answers on, set the environment below and restart the server.
          </p>
          <p className={styles.env}>
            ARCHIVIST_LLM_BASE_URL · ARCHIVIST_LLM_API_KEY · ARCHIVIST_LLM_MODEL
          </p>
        </div>
      ) : null}

      {phase.kind === "error" && phase.error.status !== 503 ? (
        <div className={styles.failure} role="alert">
          <p className={styles.failureTitle}>The ask failed.</p>
          <p className={styles.failureBody}>{phase.error.message}</p>
          {phase.error.status > 0 ? (
            <p className={styles.failureMeta}>HTTP {phase.error.status}</p>
          ) : null}
        </div>
      ) : null}

      {phase.kind === "done" ? (
        <div className={styles.answer} aria-live="polite">
          <p className={styles.answerText}>{phase.data.answer.text}</p>
          <div className={styles.answerMeta}>
            {phase.data.answer.steps.length} steps · {phase.data.answer.llm_calls}{" "}
            llm calls
            {phase.data.answer.partial ? " · partial" : ""}
          </div>
          {phase.data.warnings.map((warning) => (
            <p key={warning} className={styles.warning}>
              {warning}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
