/** AskRoom — the Ask + Trace Ribbon experience (M4R step 3).
 *
 *  One conversation, many turns: every answer keeps its ribbon of steps,
 *  the exact queries, and a replay of the subgraph each step touched (via
 *  `/api/graph/induced`, resolved from the *recorded* result rows — the
 *  client never re-runs the agent). The stage variant adds the trace
 *  canvas; the panel variant (right rail) keeps ribbon + detail inline.
 *  The "remember this run" toggle maps to the server's `remember` flag. */

import type { FormEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useRef, useState } from "react";

import { ApiError, postJSON } from "../lib/api";
import { formatInt } from "../lib/format";
import { llmHeadersFor, useLLMKey } from "../lib/llmKeys";
import { useSession } from "../lib/session";
import {
  mergeReplays,
  queriesForStep,
  replayRequest,
  spotlightFor,
  type ReplayBundle,
  type StepReplay,
} from "../lib/trace";
import type { AskResponse, InducedResponse } from "../lib/types";
import { GraphCanvas } from "./GraphCanvas";
import { TraceDetail } from "./TraceDetail";
import { TraceRibbon } from "./TraceRibbon";
import styles from "./AskRoom.module.css";

type Phase =
  | { kind: "running" }
  | { kind: "done"; data: AskResponse }
  | { kind: "error"; error: ApiError };

interface Turn {
  id: number;
  question: string;
  remembered: boolean;
  phase: Phase;
  replay: ReplayBundle | null;
  replayError: string | null;
}

interface Props {
  variant: "stage" | "panel";
  initialQuestion?: string | null;
  autoRun?: boolean;
  /** Show a canned "how a trace works" explainer on arrival (no model call). */
  explain?: boolean;
}

let nextTurnId = 1;

export function AskRoom({
  variant,
  initialQuestion = null,
  autoRun = false,
  explain = false,
}: Props) {
  const stage = variant === "stage";
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState(initialQuestion ?? "");
  const [remember, setRemember] = useState(true);
  const session = useSession();
  const byoKey = useLLMKey();
  const anonDemo =
    session.loaded && session.me?.anonymous === true && remember;
  const [active, setActive] = useState<{ turn: number; step: number } | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  // Deep links: remember which ?q= we already ran, so a same-route
  // navigation to a new question consumes it (and a repeat never reruns).
  const ranRef = useRef<string | null>(null);

  const busy = turns.some((turn) => turn.phase.kind === "running");
  const last = turns[turns.length - 1];

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, last?.phase.kind]);

  useEffect(() => {
    if (!autoRun || initialQuestion === null) return;
    const question = initialQuestion.trim();
    if (!question || ranRef.current === question) return;
    ranRef.current = question;
    void run(question);
    // run is defined per render; the guard dedupes by question *value* —
    // a fresh deep link (⌘K ask, starter, ?q= change) runs once. The landing
    // "See a trace" CTA instead links with `run=0`, so AskView passes
    // autoRun=false and this never fires (audit #6).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, initialQuestion]);

  async function run(question: string): Promise<void> {
    const id = nextTurnId++;
    const remembered = remember;
    setTurns((prev) => [
      ...prev,
      {
        id,
        question,
        remembered,
        phase: { kind: "running" },
        replay: null,
        replayError: null,
      },
    ]);
    setDraft("");
    let data: AskResponse;
    try {
      data = await postJSON<AskResponse>(
        "/api/ask",
        {
          question,
          max_steps: 6,
          remember: remembered,
        },
        llmHeadersFor(byoKey),
      );
    } catch (error) {
      const apiError =
        error instanceof ApiError ? error : new ApiError(0, String(error));
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === id ? { ...turn, phase: { kind: "error", error: apiError } } : turn,
        ),
      );
      return;
    }
    setTurns((prev) =>
      prev.map((turn) =>
        turn.id === id ? { ...turn, phase: { kind: "done", data } } : turn,
      ),
    );
    await fetchReplay(id, data);
  }

  async function fetchReplay(turnId: number, data: AskResponse): Promise<void> {
    const request = replayRequest(data.answer);
    if (!request) return;
    try {
      const response = await postJSON<InducedResponse>("/api/graph/induced", request);
      const byStep = new Map<number, StepReplay>();
      for (const item of response.steps) {
        const index = Number(item.id);
        if (Number.isInteger(index)) {
          byStep.set(index, { nodes: item.nodes, edges: item.edges, notes: item.notes });
        }
      }
      const replay: ReplayBundle = { byStep, merged: mergeReplays(byStep) };
      setTurns((prev) =>
        prev.map((turn) => (turn.id === turnId ? { ...turn, replay } : turn)),
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId ? { ...turn, replayError: message } : turn,
        ),
      );
    }
  }

  function submit(event: FormEvent): void {
    event.preventDefault();
    const question = draft.trim();
    if (!question || busy) return;
    void run(question);
  }

  function onComposerKey(event: ReactKeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const question = draft.trim();
      if (question && !busy) void run(question);
    }
  }

  function pick(turnId: number, stepIndex: number): void {
    setActive((prev) =>
      prev && prev.turn === turnId && prev.step === stepIndex
        ? null
        : { turn: turnId, step: stepIndex },
    );
  }

  // Canvas subject: the active turn's replay, else the most recent one.
  let latestReplay: ReplayBundle | null = null;
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (turns[index].replay) {
      latestReplay = turns[index].replay;
      break;
    }
  }
  const activeTurn = active
    ? (turns.find((turn) => turn.id === active.turn) ?? null)
    : null;
  const activeStep =
    active && activeTurn && activeTurn.phase.kind === "done"
      ? (activeTurn.phase.data.answer.steps[active.step] ?? null)
      : null;
  const activeReplay = active ? activeTurn?.replay?.byStep.get(active.step) : undefined;
  const canvas = activeTurn?.replay?.merged ?? latestReplay?.merged ?? null;
  // True when the active chip has no subgraph of its own: no replay bundle
  // (nothing to resolve) or an empty one. The canvas then says so instead
  // of dimming silently (Gate A W1).
  const activeHasNoSubgraph =
    active != null && (activeReplay == null || activeReplay.nodes.length === 0);

  const thread = (
    <div className={stage ? styles.stageThread : styles.panelThread} role="log">
      {turns.length === 0
        ? stage
          ? (
            <div className={styles.intro}>
              {explain ? (
                <>
                  <h1 className={styles.introTitle}>How an answer is traced.</h1>
                  <div className={styles.explain}>
                    <p className={styles.explainLead}>
                      Every answer is a path through the graph — and every step is
                      shown, never hidden.
                    </p>
                    <ol className={styles.explainSteps}>
                      <li>
                        <strong>Ask.</strong> Type a question and press Ask — one
                        turn, one trace.
                      </li>
                      <li>
                        <strong>The agent plans.</strong> It picks which graph tools
                        to call: callers, owners, history, shortest path.
                      </li>
                      <li>
                        <strong>Each step is recorded.</strong> The exact Cypher, the
                        rows it returned, and the milliseconds it took are kept
                        beside the answer.
                      </li>
                      <li>
                        <strong>Replay any step.</strong> Tap a chip in the ribbon and
                        its subgraph lights up on the canvas — the proof, not a
                        screenshot.
                      </li>
                    </ol>
                    <p className={styles.explainCta}>
                      Type a question below to see it happen — or head back to Home
                      and tap a starter.
                    </p>
                  </div>
                </>
              ) : (
                <>
                  <h1 className={styles.introTitle}>Ask the archive.</h1>
                  <p className={styles.introBody}>
                    One question, one traced path through the graph — every step keeps
                    its Cypher, its timing, and the subgraph it touched.
                  </p>
                </>
              )}
            </div>
          )
          : (
            <p className={styles.railHint}>
              One question, one traced path — the answer keeps its steps.
            </p>
          )
        : null}

      {turns.map((turn) => {
        const isActiveTurn = active?.turn === turn.id;
        const activeIndex = isActiveTurn ? active!.step : null;
        return (
          <article key={turn.id} className={styles.turn}>
            <h2 className={styles.question}>{turn.question}</h2>

            {turn.phase.kind === "running" ? (
              <div className={styles.running} aria-live="polite">
                <span>Working the graph</span>
                <span className={styles.pulse} aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            ) : null}

            {turn.phase.kind === "error" && turn.phase.error.status === 503 ? (
              <div className={styles.noKey} role="status">
                <h4 className={styles.noKeyTitle}>The console can&rsquo;t talk yet.</h4>
                <p className={styles.noKeyBody}>
                  No LLM key is configured on this box, so conversations are switched
                  off — every other surface on this screen reads the live graph. Bring
                  your own key in Settings to turn answers on, or the box owner can set
                  the environment below and restart the server.
                </p>
                <p className={styles.env}>
                  ARCHIVIST_LLM_BASE_URL · ARCHIVIST_LLM_API_KEY · ARCHIVIST_LLM_MODEL
                </p>
                {byoKey.base || byoKey.model || byoKey.key ? (
                  <p className={styles.noKeyNote}>
                    A key is set in Settings but the ask was still refused — check that
                    the base URL, model, and key are complete, then Test key.
                  </p>
                ) : null}
              </div>
            ) : null}

            {turn.phase.kind === "error" &&
            turn.phase.error.code === "demo_quota" ? (
              <div className={styles.quota} role="alert">
                <h4 className={styles.failureTitle}>Demo quota reached.</h4>
                <p className={styles.failureBody}>{turn.phase.error.message}</p>
                <p className={styles.quotaNote}>
                  Bring your own key in Settings to keep asking, or wait for the hourly
                  window to reset.
                </p>
              </div>
            ) : null}

            {turn.phase.kind === "error" &&
            turn.phase.error.status !== 503 &&
            turn.phase.error.code !== "demo_quota" ? (
              <div className={styles.failure} role="alert">
                <p className={styles.failureTitle}>The ask failed.</p>
                <p className={styles.failureBody}>{turn.phase.error.message}</p>
                {turn.phase.error.status > 0 ? (
                  <p className={styles.failureMeta}>HTTP {turn.phase.error.status}</p>
                ) : null}
              </div>
            ) : null}

            {turn.phase.kind === "done" ? (
              <>
                <div className={styles.answer}>
                  <p className={styles.answerText}>{turn.phase.data.answer.text}</p>
                </div>
                <TraceRibbon
                  steps={turn.phase.data.answer.steps}
                  active={activeIndex}
                  onPick={(stepIndex) => pick(turn.id, stepIndex)}
                />
                <div className={styles.metaRow}>
                  <span>
                    {turn.phase.data.answer.steps.length} step
                    {turn.phase.data.answer.steps.length === 1 ? "" : "s"} ·{" "}
                    {turn.phase.data.answer.llm_calls} llm call
                    {turn.phase.data.answer.llm_calls === 1 ? "" : "s"}
                    {turn.phase.data.answer.partial ? " · partial" : ""}
                  </span>
                  <span
                    className={
                      turn.remembered && turn.phase.data.episode_id
                        ? styles.memSaved
                        : styles.memOff
                    }
                    title={turn.phase.data.episode_id ?? undefined}
                  >
                    {turn.remembered
                      ? turn.phase.data.episode_id
                        ? "remembered"
                        : "not saved"
                      : "not remembered"}
                  </span>
                </div>
                {turn.replayError ? (
                  <p className={styles.softNote}>
                    replay unavailable — {turn.replayError}
                  </p>
                ) : null}
                {turn.phase.data.answer.error ? (
                  <p className={styles.softNote}>{turn.phase.data.answer.error}</p>
                ) : null}
                {turn.phase.data.warnings.map((warning) => (
                  <p key={warning} className={styles.softNote}>
                    {warning}
                  </p>
                ))}

                {!stage && activeStep && isActiveTurn ? (
                  <TraceDetail
                    step={activeStep}
                    queries={queriesForStep(turn.phase.data.answer, activeStep)}
                    replay={activeReplay}
                    onClose={() => setActive(null)}
                  />
                ) : null}
              </>
            ) : null}
          </article>
        );
      })}
      <div ref={endRef} />
    </div>
  );

  const form = (
    <form className={styles.form} onSubmit={submit}>
      {stage ? (
        <textarea
          className={styles.textarea}
          rows={2}
          placeholder="Ask the archive… (Enter to send, Shift+Enter for a new line)"
          aria-label="Ask the archive"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onComposerKey}
          spellCheck={false}
        />
      ) : (
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
      )}
      <div className={styles.formRow}>
        <label className={styles.remember} title="Records this run as an episode in graph memory">
          <input
            type="checkbox"
            checked={remember}
            onChange={(event) => setRemember(event.target.checked)}
          />
          remember this run
        </label>
        {anonDemo ? (
          <span className={styles.memoryNote}>
            saved to the shared demo memory
          </span>
        ) : null}
        <button
          type="submit"
          className={styles.submit}
          disabled={busy || draft.trim().length === 0}
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </div>
    </form>
  );

  if (!stage) {
    return (
      <div className={styles.panel}>
        {thread}
        {form}
      </div>
    );
  }

  return (
    <div className={styles.stage}>
      <section className={styles.convo} aria-label="Conversation">
        {thread}
        {form}
      </section>
      <aside className={styles.tracePane} aria-label="Trace canvas">
        {canvas && canvas.nodes.length > 0 ? (
          <>
            <GraphCanvas
              payload={canvas}
              spread="wide"
              ripple
              spotlight={active && activeTurn?.replay ? spotlightFor(activeReplay) : null}
            />
            <div className={styles.canvasMeta}>
              {activeHasNoSubgraph ? (
                <>
                  this step&rsquo;s evidence has no subgraph to draw — its rows are in
                  the step panel
                  {active ? " · click the chip again to release" : null}
                </>
              ) : (
                <>
                  {formatInt(canvas.nodes.length)} nodes · {formatInt(canvas.edges.length)}{" "}
                  edges
                  {active ? " · click the chip again to release" : " · light a step via its chip"}
                </>
              )}
            </div>
          </>
        ) : (
          <div className={styles.traceEmpty}>
            <p className={styles.traceEmptyTitle}>The evidence draws here.</p>
            <p className={styles.traceEmptyBody}>
              Run a question — each step&rsquo;s subgraph replays on this canvas.
              Click a chip to light its part.
            </p>
          </div>
        )}
        {active && activeStep && activeTurn && activeTurn.phase.kind === "done" ? (
          <TraceDetail
            className={styles.overlay}
            step={activeStep}
            queries={queriesForStep(activeTurn.phase.data.answer, activeStep)}
            replay={activeReplay}
            onClose={() => setActive(null)}
          />
        ) : null}
      </aside>
    </div>
  );
}
