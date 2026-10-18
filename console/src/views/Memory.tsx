import type { CSSProperties } from "react";
import { useEffect, useMemo, useState } from "react";
import { motion } from "motion/react";

import { postJSON, useApi } from "../lib/api";
import { clampText, formatInt, relTime, shortSha, stamp } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import {
  chainLinks,
  episodeChips,
  factSpan,
  groupEpisodes,
  memoryDomain,
  memoryIndex,
  pct,
  widenDomain,
  windowInstants,
  type Domain,
} from "../lib/memory";
import { EASE_OUT, prefersReducedMotion } from "../lib/motion";
import { href, navigate, useLocation } from "../lib/router";
import type {
  ActionItem,
  ActionsResponse,
  MemoryChainFact,
  MemoryEpisode,
  MemoryFact,
  MemoryResponse,
} from "../lib/types";
import styles from "./Memory.module.css";

/** Memory (M4R P1): the bi-temporal ledger on live data. Episodes newest
 *  first on a timeline; claims with valid-window bars and supersede chains;
 *  a scrubber that re-asks the server "what was true at T" (as_of). */
export function Memory() {
  const { query } = useLocation();
  const [asOf, setAsOf] = useState<number | null>(() => {
    const raw = query.get("as_of");
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
  });
  const [history, setHistory] = useState(() => query.get("history") === "1");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  // Fetch only the settled scrubber position — dragging must not spam the
  // server; the label stays live, the data arrives when the hand stops.
  const settled = useDebounced(asOf, 340);
  const activeAsOf = asOf === null ? null : settled;
  const path =
    activeAsOf !== null
      ? `/api/graph/memory?as_of=${activeAsOf}`
      : history
        ? "/api/graph/memory?history=1"
        : "/api/graph/memory";
  const mem = useApi<MemoryResponse>(path);
  const actions = useApi<ActionsResponse>("/api/graph/actions");

  // Timeline domain: widened across fetches so bars and the marker never
  // jump when the mode changes (see lib/memory).
  const [domain, setDomain] = useState<Domain | null>(null);
  useEffect(() => {
    const snapshot = mem.data;
    if (snapshot) setDomain((prev) => widenDomain(prev, memoryDomain(snapshot)));
  }, [mem.data]);

  const data = mem.data;
  const facts = data?.facts ?? [];
  const index = useMemo(() => (data ? memoryIndex(data) : new Map()), [data]);
  const groups = useMemo(
    () => groupEpisodes(data?.episodes ?? []),
    [data?.episodes],
  );

  const fillPct = asOf === null || !domain ? 100 : pct(asOf, domain);
  const readout = asOf === null ? "Now" : `As of ${stamp(asOf)}`;
  const emptyAll =
    data !== null && data.episodes.length === 0 && data.facts.length === 0;

  return (
    <div className={styles.memory} data-memory-mode={data?.mode ?? "loading"}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Memory</span>
        <h1 className={styles.title}>What the archive remembers.</h1>
        <p className={styles.lead}>
          Every run records an episode; findings become claims on a
          bi-temporal ledger — superseded, never deleted. Scrub the timeline
          to ask what was true at any point.
        </p>
      </header>

      {data ? (
        <section className={styles.stats} aria-label="Memory totals">
          <Stat value={data.counts.episodes} label="Episodes" />
          <Stat value={data.counts.facts} label="Claims" />
          <Stat value={data.counts.open_facts} label="Open" />
          <Stat value={data.counts.supersede_edges} label="Supersedes" />
          <Stat value={data.counts.handoff_edges} label="Handoffs" />
        </section>
      ) : null}

      <ActionsSection data={actions.data} error={actions.error} onReload={actions.reload} />

      <section className={styles.scrubber} aria-label="Timeline scrubber">
        <div className={styles.scrubberHead}>
          <span className={styles.scrubberLabel}>Timeline</span>
          <span
            className={`${styles.readout} ${asOf === null ? styles.readoutNow : ""}`}
          >
            {readout}
          </span>
        </div>
        {domain ? (
          <>
            <input
              type="range"
              className={styles.slider}
              min={Math.floor(domain.t0)}
              max={Math.ceil(domain.t1)}
              step={30}
              value={Math.round(asOf ?? domain.t1)}
              style={{ "--fill": `${fillPct}%` } as CSSProperties}
              onChange={(event) => {
                const v = Number(event.target.value);
                setAsOf(v >= domain.t1 - 60 ? null : v);
              }}
              aria-label="Timeline scrubber — drag to ask what was true at an earlier instant"
            />
            <div className={styles.scrubberFoot}>
              <span className={styles.bounds}>
                {stamp(domain.t0)} → now
              </span>
              <div className={styles.controls}>
                <button
                  type="button"
                  className={styles.control}
                  aria-pressed={asOf === null}
                  onClick={() => setAsOf(null)}
                >
                  Now
                </button>
                <button
                  type="button"
                  className={styles.control}
                  aria-pressed={history && asOf === null}
                  disabled={asOf !== null}
                  title={
                    asOf !== null
                      ? "clear the scrubber to browse full history"
                      : "include closed windows — invalidate-don't-delete"
                  }
                  onClick={() => setHistory((on) => !on)}
                >
                  Full history
                </button>
              </div>
            </div>
          </>
        ) : (
          <p className={styles.loading}>reading memory…</p>
        )}
      </section>

      {mem.error && !data ? (
        <p className={styles.loadingError}>{mem.error.message}</p>
      ) : null}

      {emptyAll ? (
        <EmptyState
          asOf={asOf}
          onReset={() => setAsOf(null)}
          onHistory={() => setHistory(true)}
          history={history}
        />
      ) : null}

      {data && !emptyAll ? (
        <>
          <section className={styles.section} aria-label="Claims">
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionLabel}>Claims</h2>
              <span className={styles.sectionNote}>{ledgerNote(data)}</span>
            </div>
            {facts.length === 0 ? (
              <p className={styles.loading}>
                {asOf !== null
                  ? "no claims were true at this instant — later claims are outside this frame"
                  : history
                    ? "no claims recorded yet"
                    : "no open claims right now — check full history"}
              </p>
            ) : (
              <div className={styles.ledger}>
                {facts.map((fact, i) => (
                  <FactCard
                    key={fact.id}
                    fact={fact}
                    index={index}
                    domain={domain}
                    asOf={asOf}
                    delay={Math.min(i, 8) * 0.03}
                    onEpisode={(id) => scrollToEpisode(id)}
                  />
                ))}
              </div>
            )}
          </section>

          <section className={styles.section} aria-label="Episodes">
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionLabel}>Episodes</h2>
              <span className={styles.sectionNote}>
                newest first — every ask and crew run records one
              </span>
            </div>
            {groups.length === 0 ? (
              <p className={styles.loading}>
                no episodes at or before this instant
              </p>
            ) : (
              <div className={styles.timeline}>
                {groups.map((group) => (
                  <div key={`${group.day}:${group.episodes[0]?.id ?? ""}`}>
                    <div className={styles.dayLabel}>{group.day}</div>
                    <div className={styles.dayGroup}>
                      {group.episodes.map((episode) => (
                        <EpisodeCard
                          key={episode.id}
                          episode={episode}
                          expanded={expanded.has(episode.id)}
                          onToggle={() =>
                            setExpanded((prev) => {
                              const next = new Set(prev);
                              if (next.has(episode.id)) next.delete(episode.id);
                              else next.add(episode.id);
                              return next;
                            })
                          }
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {data.notes.length > 0 ? (
            <ul className={styles.notes} aria-label="Notes">
              {data.notes.map((note) => (
                <li key={note} className={styles.note}>
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
        </>
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

function ActionsSection({
  data,
  error,
  onReload,
}: {
  data: ActionsResponse | null;
  error: { message: string } | null;
  onReload: () => void;
}) {
  if (error) {
    return (
      <section className={styles.section} aria-label="Actions">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionLabel}>Actions</h2>
        </div>
        <p className={styles.loadingError}>{error.message}</p>
      </section>
    );
  }
  if (!data) {
    return (
      <section className={styles.section} aria-label="Actions">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionLabel}>Actions</h2>
        </div>
        <p className={styles.loading}>reading actions…</p>
      </section>
    );
  }
  const items = data.actions;
  return (
    <section className={styles.section} aria-label="Actions">
      <div className={styles.sectionHead}>
        <h2 className={styles.sectionLabel}>Actions</h2>
        <span className={styles.sectionNote}>
          the agent's recorded next steps — {data.counts.proposed} proposed ·{" "}
          {data.counts.done} done · {data.counts.superseded} superseded
        </span>
      </div>
      {items.length === 0 ? (
        <p className={styles.loading}>
          no actions recorded yet — ask a question and the agent may propose a
          next step
        </p>
      ) : (
        <div className={styles.actionList}>
          {items.map((action) => (
            <ActionRow key={action.id} action={action} onReload={onReload} />
          ))}
        </div>
      )}
      {data.notes.length > 0 ? (
        <ul className={styles.notes} aria-label="Action notes">
          {data.notes.map((note) => (
            <li key={note} className={styles.note}>
              {note}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function ActionRow({
  action,
  onReload,
}: {
  action: ActionItem;
  onReload: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function complete(status: "done" | "superseded"): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await postJSON(`/api/actions/${action.id}/complete`, { status });
      onReload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.action} data-status={action.status}>
      <div className={styles.actionHead}>
        <span className={`${styles.actionType} ${styles[`type_${action.type}`]}`}>
          {action.type ?? "action"}
        </span>
        <span className={styles.actionStatus}>{action.status}</span>
        <p className={styles.actionSummary}>{action.summary}</p>
      </div>
      <div className={styles.actionMeta}>
        {action.refs.map((ref, i) =>
          ref.kind === "File" && ref.label ? (
            <button
              key={`${ref.kind}:${ref.label}:${i}`}
              type="button"
              className={`${styles.ref} ${styles.refLink}`}
              title={`open ${ref.label} in the Explorer`}
              onClick={() => navigate(href("/explorer", { path: ref.label! }))}
            >
              {ref.label}
            </button>
          ) : (
            <span key={`${ref.kind}:${ref.label}:${i}`} className={styles.ref}>
              {ref.label ?? ref.kind}
            </span>
          ),
        )}
        {action.episode_id ? (
          <button
            type="button"
            className={styles.epLink}
            onClick={() => scrollToEpisode(action.episode_id!)}
          >
            episode ↗
          </button>
        ) : null}
        {action.supersedes.length > 0 ? (
          <span className={styles.supersedes} title={action.supersedes.join(", ")}>
            SUPERSEDES {shortSha(action.supersedes[0])}
          </span>
        ) : null}
        {action.status === "proposed" ? (
          <span className={styles.actionControls}>
            <button
              type="button"
              className={styles.actionBtn}
              disabled={busy}
              onClick={() => void complete("done")}
            >
              {busy ? "…" : "Mark done"}
            </button>
            <button
              type="button"
              className={styles.actionBtn}
              disabled={busy}
              onClick={() => void complete("superseded")}
            >
              Superseded
            </button>
          </span>
        ) : null}
        {error ? <span className={styles.actionError}>{error}</span> : null}
      </div>
    </div>
  );
}

function ledgerNote(resp: MemoryResponse): string {
  if (resp.mode === "as_of") {
    return `true at ${stamp(resp.as_of ?? 0)} — event time; windows that intersect that instant`;
  }
  if (resp.mode === "history") {
    return "every window kept — invalidate, never delete";
  }
  return "open windows only — closed claims stay in the graph";
}

function FactCard({
  fact,
  index,
  domain,
  asOf,
  delay,
  onEpisode,
}: {
  fact: MemoryFact;
  index: Map<string, MemoryFact | MemoryChainFact>;
  domain: Domain | null;
  asOf: number | null;
  delay: number;
  onEpisode: (id: string) => void;
}) {
  const span = domain ? factSpan(fact, domain) : null;
  const { from, to } = windowInstants(fact);
  const links = chainLinks(fact, index);
  const validFrom = from ? stamp(from) : "—";
  const validTo = to ? stamp(to) : "open — still true";

  return (
    <motion.div
      className={styles.fact}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: EASE_OUT, delay }}
    >
      <div className={styles.factHead}>
        <span className={styles.kind}>{fact.kind ?? "claim"}</span>
        <p className={styles.factText}>{fact.text}</p>
      </div>

      {span ? (
        <div
          className={styles.barRow}
          title={`Valid ${validFrom} → ${validTo}`}
          aria-hidden="true"
        >
          <div className={styles.barRail} />
          <motion.div
            className={`${styles.barSeg} ${span.open ? styles.barSegOpen : styles.barSegClosed}`}
            style={{ left: `${span.startPct}%`, width: `${span.endPct - span.startPct}%` }}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 0.32, ease: EASE_OUT }}
          />
          {asOf !== null && domain ? (
            <div className={styles.barMarker} style={{ left: `${pct(asOf, domain)}%` }} />
          ) : null}
        </div>
      ) : null}

      <div className={styles.factMeta}>
        <span className={styles.window}>
          {validFrom} → {validTo}
        </span>
        {fact.refs.map((ref, i) =>
          ref.kind === "File" && ref.label ? (
            <button
              key={`${ref.kind}:${ref.label}:${i}`}
              type="button"
              className={`${styles.ref} ${styles.refLink}`}
              title={`open ${ref.label} in the Explorer`}
              onClick={() => navigate(href("/explorer", { path: ref.label! }))}
            >
              {ref.label}
            </button>
          ) : (
            <span key={`${ref.kind}:${ref.label}:${i}`} className={styles.ref}>
              {ref.label ?? ref.kind}
            </span>
          ),
        )}
        {fact.episode_id ? (
          <button
            type="button"
            className={styles.epLink}
            onClick={() => onEpisode(fact.episode_id!)}
          >
            episode ↗
          </button>
        ) : null}
      </div>

      {links.length > 0 ? (
        <div className={styles.chain}>
          {links.map((link) => (
            <div key={`${link.id}:${link.side}`} className={styles.chainRow}>
              <span className={styles.chainTag}>
                {link.side === "older" ? "supersedes" : "superseded by"}
              </span>
              <span className={styles.chainText} title={link.text}>
                {clampText(link.text, 96)}
              </span>
              <span className={styles.chainWhen}>
                {link.validAt ? stamp(link.validAt) : "—"} →{" "}
                {link.invalidAt ? stamp(link.invalidAt) : "open"}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </motion.div>
  );
}

function EpisodeCard({
  episode,
  expanded,
  onToggle,
}: {
  episode: MemoryEpisode;
  expanded: boolean;
  onToggle: () => void;
}) {
  const answer = episode.answer ?? "";
  const long = answer.length > 240;
  const chips = episodeChips(episode);

  return (
    <article className={styles.episode} data-episode={episode.id}>
      <div className={styles.epMeta}>
        <span className={styles.epTime} title={stamp(episode.ts)}>
          {relTime(episode.ts)}
        </span>
        <span
          className={`${styles.epBadge} ${episode.role ? styles.epBadgeRole : ""}`}
        >
          {episode.role ?? episode.kind}
        </span>
        <span className={styles.epMetaMid}>{episode.source}</span>
        {episode.run ? (
          <span className={styles.epMetaMid}>run {shortSha(episode.run)}</span>
        ) : null}
      </div>
      {episode.question ? <p className={styles.epQ}>{episode.question}</p> : null}
      {answer ? (
        <p className={styles.epA}>
          {expanded || !long ? answer : clampText(answer, 240)}
        </p>
      ) : null}
      <div className={styles.epFoot}>
        {chips.map((chip) => (
          <span
            key={chip.key}
            className={`${styles.chip} ${chip.key === "partial" ? styles.chipPartial : ""}`}
          >
            {chip.key} {chip.value}
          </span>
        ))}
        {episode.handoffs.map((h, i) => (
          <span key={`out:${i}`} className={styles.handoff} title={h.to ?? ""}>
            → {h.note ? clampText(h.note, 56) : "handoff"}
          </span>
        ))}
        {episode.handoff_in.map((h, i) => (
          <span key={`in:${i}`} className={styles.handoff} title={h.from ?? ""}>
            ← {h.note ? clampText(h.note, 56) : "handoff in"}
          </span>
        ))}
        {long ? (
          <button type="button" className={styles.expand} onClick={onToggle}>
            {expanded ? "less" : "more"}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function EmptyState({
  asOf,
  history,
  onReset,
  onHistory,
}: {
  asOf: number | null;
  history: boolean;
  onReset: () => void;
  onHistory: () => void;
}) {
  if (asOf !== null) {
    return (
      <div className={styles.empty}>
        <p className={styles.emptyTitle}>Outside the record.</p>
        <p className={styles.emptyBody}>
          The archive holds nothing that was true at {stamp(asOf)}. Pull the
          scrubber forward — claims that arrive later are outside this frame.
        </p>
        <button type="button" className={styles.emptyAction} onClick={onReset}>
          Back to now
        </button>
      </div>
    );
  }
  if (history) {
    return (
      <div className={styles.empty}>
        <p className={styles.emptyTitle}>Nothing on the record yet.</p>
        <p className={styles.emptyBody}>
          Memory accrues as the agent runs — every ask records an episode,
          findings land as claims, and crew runs leave handoffs.
        </p>
      </div>
    );
  }
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>Nothing on the record yet.</p>
      <p className={styles.emptyBody}>
        Memory accrues as the agent runs — every ask records an episode,
        findings land as claims, and crew runs leave handoffs. Ask something
        to start the ledger.
      </p>
      <button
        type="button"
        className={styles.emptyAction}
        onClick={() => navigate(href("/ask"))}
      >
        Ask the archive
      </button>
      <button type="button" className={styles.emptyAction} onClick={onHistory}>
        Check full history
      </button>
    </div>
  );
}

function scrollToEpisode(id: string): void {
  const el = document.querySelector(`[data-episode="${CSS.escape(id)}"]`);
  el?.scrollIntoView({
    behavior: prefersReducedMotion() ? "auto" : "smooth",
    block: "center",
  });
}
