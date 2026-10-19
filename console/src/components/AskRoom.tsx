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

import { ApiError, postJSON, postSSE, useApi } from "../lib/api";
import { formatInt } from "../lib/format";
import {
  getLLMKey,
  llmHeadersFor,
  saveLLMKey,
  type LLMKeySettings,
} from "../lib/llmKeys";
import { STARTERS } from "../lib/questions";
import { useSession } from "../lib/session";
import {
  mergeReplays,
  queriesForStep,
  replayRequest,
  spotlightFor,
  type ReplayBundle,
  type StepReplay,
} from "../lib/trace";
import type { ActionItem, ActionsResponse, AskResponse, InducedResponse } from "../lib/types";
import { GraphCanvas } from "./GraphCanvas";
import { Orb } from "./Orb";
import { TraceDetail } from "./TraceDetail";
import { TraceRibbon } from "./TraceRibbon";
import styles from "./AskRoom.module.css";

/** One row of the live feed while a run streams (web ask → SSE). */
interface LiveStep {
  /** Server step index; -1 for stashed pre-tool reasoning. */
  i: number;
  tool: string;
  detail: string;
  status: "running" | "ok" | "error";
  meta: string;
}

type Phase =
  | { kind: "running"; steps: LiveStep[]; thinking: string }
  | { kind: "done"; data: AskResponse }
  | { kind: "error"; error: ApiError };

/** One-line preview of a tool call's arguments for the live feed. */
function argsPreview(args: Record<string, unknown>): string {
  const keys = ["query", "cypher", "path", "text", "question", "name", "pattern"];
  for (const key of keys) {
    const value = args[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim().replace(/\s+/g, " ").slice(0, 140);
    }
  }
  try {
    return JSON.stringify(args).slice(0, 140);
  } catch {
    return "";
  }
}

interface StreamHooks {
  onStepStart: (i: number, tool: string, args: Record<string, unknown>) => void;
  onStepResult: (i: number, patch: Partial<LiveStep>) => void;
  onDelta: (text: string) => void;
}

/** POST /api/ask/stream (SSE): steps + answer deltas in real time, the full
 *  Answer payload on `done`. Refusals (validation, quota, no key) stay
 *  ordinary JSON errors and surface as ApiError — no re-spend on fallback. */
async function streamAsk(
  question: string,
  remembered: boolean,
  hooks: StreamHooks,
): Promise<AskResponse> {
  let answer: AskResponse | null = null;
  let failure: string | null = null;
  await postSSE(
    "/api/ask/stream",
    { question, max_steps: 6, remember: remembered },
    llmHeadersFor(getLLMKey()),
    (event, data) => {
      if (event === "step_start") {
        const i = typeof data.i === "number" ? data.i : -1;
        const tool = typeof data.tool === "string" ? data.tool : "tool";
        const args =
          data.args && typeof data.args === "object"
            ? (data.args as Record<string, unknown>)
            : {};
        hooks.onStepStart(i, tool, args);
      } else if (event === "step_result") {
        const i = typeof data.i === "number" ? data.i : -1;
        const status: LiveStep["status"] = data.status === "error" ? "error" : "ok";
        const bits: string[] = [];
        if (typeof data.count === "number") {
          bits.push(`${formatInt(data.count)} row${data.count === 1 ? "" : "s"}`);
        }
        if (typeof data.ms === "number") bits.push(`${formatInt(data.ms)}ms`);
        const error = typeof data.error === "string" && data.error ? data.error : null;
        const notes = Array.isArray(data.notes)
          ? data.notes.filter((note): note is string => typeof note === "string")
          : [];
        if (error) bits.push(error.slice(0, 90));
        else if (notes.length > 0) bits.push(notes[0].slice(0, 90));
        hooks.onStepResult(i, { status, meta: bits.join(" · ") });
      } else if (event === "answer_delta") {
        if (typeof data.text === "string") hooks.onDelta(data.text);
      } else if (event === "done") {
        answer = data as unknown as AskResponse;
      } else if (event === "error") {
        failure = typeof data.message === "string" ? data.message : "the run failed";
      }
    },
  );
  if (answer) return answer;
  if (failure) throw new ApiError(500, failure);
  throw new ApiError(0, "the stream ended without an answer");
}

interface Turn {
  id: number;
  question: string;
  remembered: boolean;
  phase: Phase;
  replay: ReplayBundle | null;
  replayError: string | null;
  filed: FiledState;
}

type FiledState =
  | { kind: "idle" }
  | { kind: "busy" }
  | { kind: "done"; number: number; url: string }
  | { kind: "error"; message: string };

/** POST /api/gh/file-issue response (web -> GitHub action). */
interface FileIssueResponse {
  filed: boolean;
  repo: string;
  number: number;
  url: string;
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
  const anonDemo =
    session.loaded && session.me?.anonymous === true && remember;
  const [active, setActive] = useState<{ turn: number; step: number } | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  // Deep links: remember which ?q= we already ran, so a same-route
  // navigation to a new question consumes it (and a repeat never reruns).
  const ranRef = useRef<string | null>(null);
  // Inline "bring a key" affordance (audit #1/#3): a keyless visitor fills
  // base/model/key right in the refused-ask card instead of being pointed at
  // a sysadmin env-var error. It seeds from the saved Settings value.
  const actions = useApi<ActionsResponse>("/api/graph/actions");
  const [inlineKey, setInlineKey] = useState<LLMKeySettings>(() => getLLMKey());
  const [inlineSaved, setInlineSaved] = useState(false);

  function saveInlineKey(settings: LLMKeySettings): void {
    saveLLMKey(settings);
    setInlineSaved(true);
    window.setTimeout(() => setInlineSaved(false), 1800);
  }

  function retryWithInlineKey(turnId: number, question: string): void {
    // Replace the refused turn with a fresh running turn and re-ask with the
    // just-saved key. The BYO store is read lazily inside `run`, so the save
    // above is already visible here.
    setTurns((prev) => prev.filter((turn) => turn.id !== turnId));
    void run(question);
  }

  const busy = turns.some((turn) => turn.phase.kind === "running");
  const last = turns[turns.length - 1];

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "nearest" });
    // `phase` (not just its kind) so a streaming run keeps the caret in view.
  }, [turns.length, last?.phase]);

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
        phase: { kind: "running", steps: [], thinking: "" },
        replay: null,
        replayError: null,
        filed: { kind: "idle" },
      },
    ]);
    setDraft("");
    // Live feed: mutated by stream events, flushed into the turn each time so
    // the running card re-renders with every step and delta.
    const live: { steps: LiveStep[]; thinking: string } = {
      steps: [],
      thinking: "",
    };
    const flushLive = (): void => {
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === id && turn.phase.kind === "running"
            ? {
                ...turn,
                phase: {
                  kind: "running",
                  steps: [...live.steps],
                  thinking: live.thinking,
                },
              }
            : turn,
        ),
      );
    };
    let gotFrames = false;
    let data: AskResponse | null = null;
    try {
      data = await streamAsk(question, remembered, {
        onStepStart: (i, tool, args) => {
          gotFrames = true;
          if (live.thinking) {
            // The model's pre-tool reasoning stays visible in the transcript.
            live.steps.push({
              i: -1,
              tool: "thinking",
              detail: live.thinking.replace(/\s+/g, " ").slice(0, 240),
              status: "ok",
              meta: "",
            });
            live.thinking = "";
          }
          live.steps.push({
            i,
            tool,
            detail: argsPreview(args),
            status: "running",
            meta: "",
          });
          flushLive();
        },
        onStepResult: (i, patch) => {
          gotFrames = true;
          const step = live.steps.find(
            (entry) => entry.i === i && entry.status === "running",
          );
          if (step) Object.assign(step, patch);
          flushLive();
        },
        onDelta: (text) => {
          gotFrames = true;
          live.thinking += text;
          flushLive();
        },
      });
    } catch (error) {
      const apiError =
        error instanceof ApiError ? error : new ApiError(0, String(error));
      if (!gotFrames && apiError.status === 0) {
        // The stream route is unavailable here (old server, buffering
        // proxy): fall back to the classic JSON ask, unchanged.
        try {
          data = await postJSON<AskResponse>(
            "/api/ask",
            {
              question,
              max_steps: 6,
              remember: remembered,
            },
            // Read the BYO key at call time (not from the render closure) so a
            // save-then-retry in the refused-ask card always sends the fresh
            // key.
            llmHeadersFor(getLLMKey()),
          );
        } catch (fallbackError) {
          const fallbackApi =
            fallbackError instanceof ApiError
              ? fallbackError
              : new ApiError(0, String(fallbackError));
          setTurns((prev) =>
            prev.map((turn) =>
              turn.id === id
                ? { ...turn, phase: { kind: "error", error: fallbackApi } }
                : turn,
            ),
          );
          return;
        }
      } else {
        setTurns((prev) =>
          prev.map((turn) =>
            turn.id === id ? { ...turn, phase: { kind: "error", error: apiError } } : turn,
          ),
        );
        return;
      }
    }
    if (!data) return;
    const result = data;
    setTurns((prev) =>
      prev.map((turn) =>
        turn.id === id ? { ...turn, phase: { kind: "done", data: result } } : turn,
      ),
    );
    // New episode landed — the sessions rail refetches instantly.
    window.dispatchEvent(new Event("archivist:memory-changed"));
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

  async function fileIssue(
    turnId: number,
    question: string,
    answer: string,
  ): Promise<void> {
    setTurns((prev) =>
      prev.map((turn) =>
        turn.id === turnId ? { ...turn, filed: { kind: "busy" } } : turn,
      ),
    );
    try {
      const response = await postJSON<FileIssueResponse>("/api/gh/file-issue", {
        question,
        answer,
      });
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId
            ? {
                ...turn,
                filed: { kind: "done", number: response.number, url: response.url },
              }
            : turn,
        ),
      );
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setTurns((prev) =>
        prev.map((turn) =>
          turn.id === turnId ? { ...turn, filed: { kind: "error", message } } : turn,
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
                  <div className={styles.introOrb}>
                    <Orb size="lg" />
                  </div>
                  <h1 className={styles.introTitle}>Ask the archive.</h1>
                  <p className={styles.introBody}>
                    One question, one traced path through the graph — every step keeps
                    its Cypher, its timing, and the subgraph it touched.
                  </p>
                  <div className={styles.chips}>
                    {STARTERS.map((starter) => (
                      <button
                        key={starter}
                        type="button"
                        className={styles.chip}
                        disabled={busy}
                        onClick={() => void run(starter)}
                      >
                        {starter}
                      </button>
                    ))}
                  </div>
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
              <div className={styles.live} aria-live="polite">
                <div className={styles.running}>
                  <Orb size="sm" thinking />
                  <span>
                    {turn.phase.thinking ? "Composing the answer" : "Working the graph"}
                  </span>
                  <span className={styles.pulse} aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
                {turn.phase.steps.length > 0 ? (
                  <ol className={styles.liveSteps}>
                    {turn.phase.steps.map((step, index) => (
                      <li key={`${step.i}-${index}`} className={styles.liveStep}>
                        <span
                          className={
                            step.status === "error"
                              ? styles.liveFlagError
                              : step.status === "running"
                                ? styles.liveFlagRunning
                                : styles.liveFlagOk
                          }
                          aria-hidden="true"
                        />
                        <span className={styles.liveTool}>{step.tool}</span>
                        {step.detail ? (
                          <span className={styles.liveDetail}>{step.detail}</span>
                        ) : null}
                        {step.meta ? (
                          <span className={styles.liveMeta}>{step.meta}</span>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                ) : null}
                {turn.phase.thinking ? (
                  <p className={styles.liveThink}>
                    {turn.phase.thinking}
                    <span className={styles.liveCaret} aria-hidden="true" />
                  </p>
                ) : null}
              </div>
            ) : null}

            {turn.phase.kind === "error" && turn.phase.error.status === 503 ? (
              <div className={styles.noKey} role="status">
                <h4 className={styles.noKeyTitle}>Bring a key to turn answers on.</h4>
                <p className={styles.noKeyBody}>
                  No LLM key is configured here, so conversations are switched off —
                  everything else on this screen still reads the live graph. Add your
                  own OpenAI-compatible endpoint below to start asking. It stays in
                  your browser and is sent only with your asks.
                </p>
                <form
                  className={styles.inlineKey}
                  onSubmit={(event) => {
                    event.preventDefault();
                    saveInlineKey(inlineKey);
                  }}
                >
                  <label className={styles.inlineField}>
                    <span className={styles.inlineLabel}>Base URL</span>
                    <input
                      className={styles.inlineInput}
                      type="text"
                      value={inlineKey.base}
                      placeholder="https://api.openai.com/v1"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) =>
                        setInlineKey((prev) => ({ ...prev, base: event.target.value }))
                      }
                    />
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineLabel}>Model</span>
                    <input
                      className={styles.inlineInput}
                      type="text"
                      value={inlineKey.model}
                      placeholder="gpt-4o-mini"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) =>
                        setInlineKey((prev) => ({ ...prev, model: event.target.value }))
                      }
                    />
                  </label>
                  <label className={styles.inlineField}>
                    <span className={styles.inlineLabel}>API key</span>
                    <input
                      className={styles.inlineInput}
                      type="password"
                      value={inlineKey.key}
                      placeholder="sk-…"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) =>
                        setInlineKey((prev) => ({ ...prev, key: event.target.value }))
                      }
                    />
                  </label>
                  <div className={styles.inlineActions}>
                    <button
                      type="submit"
                      className={styles.inlineSave}
                      disabled={!inlineKey.base.trim() || !inlineKey.model.trim()}
                    >
                      {inlineSaved ? "saved ✓" : "save key"}
                    </button>
                    <button
                      type="button"
                      className={styles.inlineRetry}
                      disabled={
                        busy || !inlineKey.base.trim() || !inlineKey.model.trim()
                      }
                      onClick={() => {
                        saveInlineKey(inlineKey);
                        retryWithInlineKey(turn.id, turn.question);
                      }}
                    >
                      {busy ? "asking…" : "save & retry"}
                    </button>
                  </div>
                </form>
                <p className={styles.noKeyNote}>
                  Prefer the full panel? It&rsquo;s under Settings — same fields, plus a
                  key test.
                </p>
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
              (() => {
                const answer = turn.phase.data.answer;
                const episodeId = turn.phase.data.episode_id;
                const warnings = turn.phase.data.warnings;
                const turnActions = actions.data?.actions.filter(
                  (a: ActionItem) => episodeId != null && a.episode_id === episodeId,
                );
                return (
                  <>
                    <div className={styles.buddyAnswer}>
                      <Orb size="sm" />
                      <div className={styles.answer}>
                        <p className={styles.answerText}>{answer.text}</p>
                      </div>
                    </div>
                    <TraceRibbon
                      steps={answer.steps}
                      active={activeIndex}
                      onPick={(stepIndex) => pick(turn.id, stepIndex)}
                      actions={turnActions}
                    />
                    <div className={styles.metaRow}>
                      <span>
                        {answer.steps.length} step
                        {answer.steps.length === 1 ? "" : "s"} ·{" "}
                        {answer.llm_calls} llm call
                        {answer.llm_calls === 1 ? "" : "s"}
                        {answer.partial ? " · partial" : ""}
                      </span>
                      <span
                        className={
                          turn.remembered && episodeId
                            ? styles.memSaved
                            : styles.memOff
                        }
                        title={episodeId ?? undefined}
                      >
                        {turn.remembered
                          ? episodeId
                            ? "remembered"
                            : "not saved"
                          : "not remembered"}
                      </span>
                    </div>
                    <div className={styles.fileRow}>
                      {turn.filed.kind === "done" ? (
                        <a
                          className={styles.filedLink}
                          href={turn.filed.url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          filed as #{turn.filed.number} on GitHub ↗
                        </a>
                      ) : (
                        <button
                          type="button"
                          className={styles.fileBtn}
                          disabled={turn.filed.kind === "busy"}
                          onClick={() => {
                            if (turn.phase.kind !== "done") return;
                            void fileIssue(turn.id, turn.question, turn.phase.data.answer.text);
                          }}
                        >
                          {turn.filed.kind === "busy" ? "filing…" : "File as GitHub issue"}
                        </button>
                      )}
                      {turn.filed.kind === "error" ? (
                        <span className={styles.fileError}>{turn.filed.message}</span>
                      ) : null}
                    </div>
                    {turn.replayError ? (
                      <p className={styles.softNote}>
                        replay unavailable — {turn.replayError}
                      </p>
                    ) : null}
                    {answer.error ? (
                      <p className={styles.softNote}>{answer.error}</p>
                    ) : null}
                    {warnings.map((warning: string) => (
                      <p key={warning} className={styles.softNote}>
                        {warning}
                      </p>
                    ))}

                    {!stage && activeStep && isActiveTurn ? (
                      <TraceDetail
                        step={activeStep}
                        queries={queriesForStep(answer, activeStep)}
                        replay={activeReplay}
                        onClose={() => setActive(null)}
                      />
                    ) : null}
                  </>
                );
              })()
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
