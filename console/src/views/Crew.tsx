import { motion } from "motion/react";
import { Fragment, useMemo, useState } from "react";

import { useApi } from "../lib/api";
import {
  formatMs,
  handoffBetween,
  phaseChips,
  runWhen,
  spineNodes,
  type SpineNode,
} from "../lib/crew";
import { clampText, formatInt, relTime } from "../lib/format";
import { EASE_OUT } from "../lib/motion";
import type { CrewPhase, CrewResponse, CrewRun } from "../lib/types";
import styles from "./Crew.module.css";

/** Crew (M4R P1): the three-agent run as graph state. The spine shows the
 *  plan order (planner → investigator → verifier); handoff notes are
 *  HANDOFF edges queried back from the graph — never a reconstructed log.
 *  Expected-but-absent roles keep their slot as ghosts, so a run that
 *  never reached its verifier shows exactly that. */
export function Crew() {
  const crew = useApi<CrewResponse>("/api/graph/crew");
  const [openPhase, setOpenPhase] = useState<string | null>(null);
  const data = crew.data;

  return (
    <div className={styles.crew} data-crew-runs={data?.counts.runs ?? "loading"}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Crew</span>
        <h1 className={styles.title}>Three agents, one record.</h1>
        <p className={styles.lead}>
          A crew run moves planner → investigator → verifier. Each phase is an
          episode on the shared record, and every handoff is a HANDOFF edge in
          the graph — the coordination is queried back, not recalled. A run
          that never reached its verifier reads interrupted.
        </p>
      </header>

      {data ? (
        <section className={styles.stats} aria-label="Crew totals">
          <Stat value={data.counts.runs} label="Runs" />
          <Stat value={data.counts.phases} label="Phases" />
          <Stat value={data.counts.handoffs} label="Handoffs" />
        </section>
      ) : null}

      {crew.error && !data ? (
        <p className={styles.loadingError}>{crew.error.message}</p>
      ) : null}

      {data && data.runs.length === 0 ? (
        <div className={styles.empty}>
          <p className={styles.emptyTitle}>No crew runs on the record yet.</p>
          <p className={styles.emptyBody}>
            A run records one episode per phase and links them with HANDOFF
            edges — launch the crew CLI and the whole chain appears here, on
            the record, in order.
          </p>
        </div>
      ) : null}

      <div className={styles.runs}>
        {data?.runs.map((run, i) => (
          <RunCard
            key={run.run}
            run={run}
            delay={Math.min(i, 6) * 0.05}
            openPhase={openPhase}
            onPhase={(id) =>
              setOpenPhase((prev) => (prev === id ? null : id))
            }
          />
        ))}
      </div>

      {!data && !crew.error ? (
        <p className={styles.loading}>reading the crew record…</p>
      ) : null}

      {data && data.notes.length > 0 ? (
        <ul className={styles.notes} aria-label="Notes">
          {data.notes.map((note) => (
            <li key={note} className={styles.note}>
              {note}
            </li>
          ))}
        </ul>
      ) : null}
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

function RunCard({
  run,
  delay,
  openPhase,
  onPhase,
}: {
  run: CrewRun;
  delay: number;
  openPhase: string | null;
  onPhase: (id: string) => void;
}) {
  const nodes = useMemo(() => spineNodes(run), [run]);
  const open = nodes.find((node) => node.phase?.id === openPhase)?.phase ?? null;

  return (
    <motion.article
      className={styles.run}
      data-run={run.run}
      data-status={run.status}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: EASE_OUT, delay }}
    >
      <header className={styles.runHead}>
        <div className={styles.runTitle}>
          <span className={`${styles.status} ${statusClass(run.status)}`}>
            {run.status}
          </span>
          <span className={styles.runId} title={run.run}>
            {run.run}
          </span>
        </div>
        <span
          className={styles.runWhen}
          title={
            run.ended_at !== null
              ? relTime(run.ended_at)
              : run.started_at !== null
                ? relTime(run.started_at)
                : undefined
          }
        >
          {runWhen(run)}
        </span>
      </header>

      {run.question ? <p className={styles.runQ}>{run.question}</p> : null}

      <div className={styles.spine} aria-label="Phase spine">
        {nodes.map((node, i) => (
          <Fragment key={`${run.run}:${node.role}:${i}`}>
            {i > 0 ? (
              <Connector from={nodes[i - 1]} to={node} run={run} />
            ) : null}
            {node.phase ? (
              <PhaseCard
                phase={node.phase}
                index={i + 1}
                open={node.phase.id === openPhase}
                onToggle={() => onPhase(node.phase?.id ?? "")}
              />
            ) : (
              <GhostCard role={node.role} index={i + 1} />
            )}
          </Fragment>
        ))}
      </div>

      {open ? <PhaseDetail phase={open} /> : null}
    </motion.article>
  );
}

function statusClass(status: CrewRun["status"]): string {
  if (status === "complete") return styles.statusComplete;
  if (status === "partial") return styles.statusPartial;
  return styles.statusInterrupted;
}

/** The boundary between two spine slots. Three honest readings: the
 *  recorded note (graph state), a missing edge where one is expected
 *  (degraded bookkeeping), or a dashed gap into a slot that never ran. */
function Connector({
  from,
  to,
  run,
}: {
  from: SpineNode;
  to: SpineNode;
  run: CrewRun;
}) {
  const both = from.phase !== null && to.phase !== null;
  const handoff = both ? handoffBetween(run, from.role, to.role) : null;
  const gap = both && handoff === null;

  return (
    <div
      className={`${styles.link} ${gap || !both ? styles.linkGap : ""}`}
      data-link={`${from.role}->${to.role}`}
    >
      <div className={styles.linkLine} aria-hidden="true">
        <span className={styles.linkArrow}>→</span>
      </div>
      {handoff ? (
        <NoteLine note={handoff.note} />
      ) : gap ? (
        <span className={styles.linkNote}>handoff edge missing</span>
      ) : null}
    </div>
  );
}

/** A handoff note — the record's own voice, so it reads as a quote. Three
 *  lines at rest; click for the full note (never truncated for display). */
function NoteLine({ note }: { note: string | null }) {
  const [open, setOpen] = useState(false);
  if (!note) {
    return <span className={styles.linkNote}>no note on this handoff</span>;
  }
  return (
    <button
      type="button"
      className={`${styles.noteQuote} ${open ? styles.noteQuoteOpen : ""}`}
      onClick={() => setOpen((prev) => !prev)}
      aria-expanded={open}
      title={open ? "collapse note" : "expand note"}
    >
      “{note}”
    </button>
  );
}

function PhaseCard({
  phase,
  index,
  open,
  onToggle,
}: {
  phase: CrewPhase;
  index: number;
  open: boolean;
  onToggle: () => void;
}) {
  const meta = phaseMeta(phase);
  return (
    <button
      type="button"
      className={`${styles.phase} ${open ? styles.phaseOpen : ""}`}
      onClick={onToggle}
      aria-expanded={open}
      data-phase={phase.id}
    >
      <span className={styles.phaseTop}>
        <span className={styles.phaseRole}>{phase.role}</span>
        <span className={styles.phaseIndex}>{String(index).padStart(2, "0")}</span>
      </span>
      <span className={styles.phaseMeta}>{meta}</span>
      {phase.error ? (
        <span className={styles.phaseFlagError}>error</span>
      ) : phase.partial ? (
        <span className={styles.phaseFlagPartial}>partial</span>
      ) : null}
    </button>
  );
}

function phaseMeta(phase: CrewPhase): string {
  const parts: string[] = [];
  if (phase.steps !== null) {
    parts.push(`${formatInt(phase.steps)} step${phase.steps === 1 ? "" : "s"}`);
  }
  if (phase.duration_ms !== null) parts.push(formatMs(phase.duration_ms));
  return parts.length ? parts.join(" · ") : "no trace recorded";
}

function GhostCard({ role, index }: { role: string; index: number }) {
  return (
    <div className={styles.ghost} data-ghost={role}>
      <span className={styles.phaseTop}>
        <span className={styles.ghostRole}>{role}</span>
        <span className={styles.phaseIndex}>{String(index).padStart(2, "0")}</span>
      </span>
      <span className={styles.ghostMeta}>no episode recorded</span>
    </div>
  );
}

function PhaseDetail({ phase }: { phase: CrewPhase }) {
  const chips = phaseChips(phase);
  return (
    <motion.section
      className={styles.detail}
      aria-label={`Phase detail — ${phase.role}`}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: EASE_OUT }}
    >
      <div className={styles.detailHead}>
        <span className={styles.detailRole}>{phase.role}</span>
        <span className={styles.detailWhen}>
          {phase.ts !== null ? relTime(phase.ts) : "unstamped"}
        </span>
        <span className={styles.detailId} title={phase.id}>
          {phase.id}
        </span>
      </div>

      {phase.question ? (
        <div className={styles.detailBlock}>
          <span className={styles.detailLabel}>
            Input{phase.input_truncated ? " · truncated for display" : ""}
          </span>
          <p className={styles.detailText}>{phase.question}</p>
        </div>
      ) : null}

      <div className={styles.detailBlock}>
        <span className={styles.detailLabel}>
          Answer{phase.answer_truncated ? " · truncated for display" : ""}
        </span>
        {phase.answer ? (
          <p className={styles.detailText}>{phase.answer}</p>
        ) : (
          <p className={styles.detailEmpty}>no answer recorded</p>
        )}
      </div>

      <div className={styles.detailFoot}>
        {chips.map((chip) => (
          <span key={chip.key} className={styles.chip}>
            {chip.key} {chip.value}
          </span>
        ))}
        {phase.error ? (
          <span className={styles.chipError} title={phase.error}>
            error: {clampText(phase.error, 64)}
          </span>
        ) : null}
      </div>
    </motion.section>
  );
}
