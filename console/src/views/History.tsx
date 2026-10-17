import { motion } from "motion/react";
import { useMemo, useRef, useState } from "react";

import { useApi, type ApiState } from "../lib/api";
import { clampText, formatInt, relTime, stamp, stampDate } from "../lib/format";
import {
  barHeightPct,
  bucketTitle,
  clampRange,
  deltaLabel,
  historyPath,
  selectionLabel,
  skylineMax,
  windowFromSelection,
  yearTicks,
  type BucketRange,
} from "../lib/history";
import { EASE_OUT } from "../lib/motion";
import { href, navigate, useLocation } from "../lib/router";
import type { HistoryBucket, HistoryCommit, HistoryResponse } from "../lib/types";
import styles from "./History.module.css";

/** Time machine (M4R runway flagship): the repo's own past as a month
 *  skyline. Drag across the months to lift a window of history out of the
 *  graph; click one month to open it alone. A top file scopes the whole
 *  view to it (`?file=`) — a symbol's evolution, read at the file
 *  granularity the graph actually holds. */
export function History() {
  const { query } = useLocation();
  const file = query.get("file");

  // A new scope is a new skyline — selections and drags never carry over.
  const [scope, setScope] = useState<string | null>(file);
  const [sel, setSel] = useState<BucketRange | null>(null);
  const [anchor, setAnchor] = useState<number | null>(null);
  const [dragTo, setDragTo] = useState<number | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [cursor, setCursor] = useState(0);
  if (scope !== file) {
    setScope(file);
    setSel(null);
    setAnchor(null);
    setDragTo(null);
    setHover(null);
    setCursor(0);
  }

  const base = useApi<HistoryResponse>(historyPath(file));
  const buckets = base.data?.buckets ?? [];
  const totals = base.data?.totals ?? null;

  const windowRange = useMemo(
    () => (sel ? windowFromSelection(buckets, sel.a, sel.b) : null),
    [sel, buckets],
  );
  const win = useApi<HistoryResponse>(
    sel && windowRange ? historyPath(file, windowRange) : null,
  );

  const max = useMemo(() => skylineMax(buckets), [buckets]);
  const ticks = useMemo(() => yearTicks(buckets), [buckets]);
  const n = buckets.length;

  const areaRef = useRef<HTMLDivElement | null>(null);

  function indexFromX(clientX: number): number | null {
    const el = areaRef.current;
    if (!el || n === 0) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return null;
    const x = clientX - rect.left;
    return Math.max(0, Math.min(n - 1, Math.floor((x / rect.width) * n)));
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>): void {
    if (event.button !== 0) return;
    const i = indexFromX(event.clientX);
    if (i === null) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    setAnchor(i);
    setDragTo(i);
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>): void {
    const i = indexFromX(event.clientX);
    if (i === null) return;
    setHover(i);
    if (anchor !== null) setDragTo(i);
  }

  function onPointerUp(event: React.PointerEvent<HTMLDivElement>): void {
    if (anchor === null) return;
    const i = indexFromX(event.clientX) ?? dragTo ?? anchor;
    setSel(clampRange(anchor, i));
    setAnchor(null);
    setDragTo(null);
  }

  function onPointerCancel(): void {
    setAnchor(null);
    setDragTo(null);
  }

  function onBarKey(event: React.KeyboardEvent<HTMLButtonElement>, i: number): void {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const next = Math.max(0, Math.min(n - 1, i + (event.key === "ArrowLeft" ? -1 : 1)));
      setCursor(next);
      setHover(next);
      if (anchor !== null) setDragTo(next);
      areaRef.current
        ?.querySelector<HTMLButtonElement>(`[data-bucket="${next}"]`)
        ?.focus();
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (anchor === null) {
        setAnchor(i);
        setDragTo(null);
      } else {
        setSel(clampRange(anchor, i));
        setAnchor(null);
        setDragTo(null);
      }
    } else if (event.key === "Escape") {
      setAnchor(null);
      setDragTo(null);
      setSel(null);
    }
  }

  const band = anchor !== null ? clampRange(anchor, dragTo ?? anchor) : sel;
  const pending = anchor !== null;

  let readout = "drag across the months to open a window — click one to open it alone";
  if (pending && band) readout = `release to open · ${selectionLabel(buckets, band.a, band.b)}`;
  else if (hover !== null && buckets[hover]) readout = bucketTitle(buckets[hover]);
  else if (sel) readout = selectionLabel(buckets, sel.a, sel.b);

  const peak = buckets.reduce<{ index: number; bucket: HistoryBucket } | null>(
    (best, bucket, index) =>
      !best || bucket.commits > best.bucket.commits ? { index, bucket } : best,
    null,
  );

  const notes = win.data?.notes ?? base.data?.notes ?? [];

  return (
    <div className={styles.history} data-history="" data-settled={base.data ? "1" : "0"}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Time machine</span>
        <h1 className={styles.title}>The repo&rsquo;s past, month by month.</h1>
        <p className={styles.lead}>
          Every commit the archive holds as a skyline of UTC calendar
          months — quiet months kept at zero, merges kept with no diff,
          nothing silently dropped. Drag across it to lift a window of
          history back out of the graph.
        </p>
      </header>

      {file ? (
        <div className={styles.scopeRow}>
          <span className={styles.scopeChip} title={file}>
            {clampText(file, 72)}
          </span>
          <span className={styles.scopeNote}>
            file scope — evolution reads at file granularity, never deeper
          </span>
          <button
            type="button"
            className={styles.scopeBack}
            onClick={() => navigate(href("/history"))}
          >
            whole repo
          </button>
        </div>
      ) : null}

      {totals ? (
        <section className={styles.stats} aria-label="History totals">
          <Stat value={totals.commits} label="Commits" />
          <Stat value={totals.added} label="Lines added" />
          <Stat value={totals.removed} label="Lines removed" />
          <Stat
            value={totals.files}
            label={totals.files === 1 ? "File touched" : "Files touched"}
          />
          <Stat value={buckets.length} label="Months" />
        </section>
      ) : null}

      {base.error && !base.data ? (
        <ErrorState error={base.error} file={file} />
      ) : !base.data ? (
        <p className={styles.loading}>reading the log…</p>
      ) : (
        <>
          <section className={styles.skylineSection} aria-label="Skyline">
            <div className={styles.sectionHead}>
              <h2 className={styles.sectionLabel}>Skyline</h2>
              <span className={styles.sectionNote}>
                {n > 0 ? `${buckets[0].label} → ${buckets[n - 1].label}` : "—"}
              </span>
            </div>
            {n > 0 ? (
              <div className={styles.panel}>
                <div
                  className={styles.skyline}
                  data-skyline=""
                  ref={areaRef}
                  onPointerDown={onPointerDown}
                  onPointerMove={onPointerMove}
                  onPointerUp={onPointerUp}
                  onPointerCancel={onPointerCancel}
                  onPointerLeave={() => setHover(null)}
                >
                  {band ? (
                    <div
                      className={`${styles.band} ${pending ? styles.bandPending : ""}`}
                      style={{
                        left: `${(band.a / n) * 100}%`,
                        width: `${((band.b - band.a + 1) / n) * 100}%`,
                      }}
                      aria-hidden="true"
                    />
                  ) : null}
                  <div className={styles.bars}>
                    {buckets.map((bucket, i) => {
                      const inSel = sel !== null && i >= sel.a && i <= sel.b;
                      const cls = [
                        styles.bar,
                        bucket.commits === 0 ? styles.barZero : "",
                        sel !== null ? (inSel ? styles.barIn : styles.barOut) : "",
                      ]
                        .filter(Boolean)
                        .join(" ");
                      return (
                        <button
                          key={bucket.label}
                          type="button"
                          data-bucket={i}
                          className={cls}
                          style={{ height: `${barHeightPct(bucket.commits, max)}%` }}
                          tabIndex={i === cursor ? 0 : -1}
                          title={bucketTitle(bucket)}
                          aria-label={bucketTitle(bucket)}
                          onFocus={() => {
                            setCursor(i);
                            setHover(i);
                          }}
                          onKeyDown={(event) => onBarKey(event, i)}
                        />
                      );
                    })}
                  </div>
                </div>
                <div className={styles.years} aria-hidden="true">
                  {ticks.map((tick) => (
                    <span
                      key={tick.label}
                      className={styles.yearTick}
                      style={{ left: `${(tick.index / n) * 100}%` }}
                    >
                      {tick.label}
                    </span>
                  ))}
                </div>
                <div className={styles.readout} aria-live="polite">
                  <span>{readout}</span>
                  {sel && !pending ? (
                    <button
                      type="button"
                      className={styles.readoutClear}
                      onClick={() => setSel(null)}
                    >
                      clear window
                    </button>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className={styles.empty}>
                <p className={styles.emptyTitle}>No history here.</p>
                <p className={styles.emptyBody}>
                  {file
                    ? `No commit in the graph touches ${file} — there is no evolution to replay for it.`
                    : "The graph holds no commits yet — ingest a repository first."}
                </p>
              </div>
            )}
          </section>

          {n > 0 ? (
            sel && windowRange ? (
              <WindowPanel
                sel={sel}
                buckets={buckets}
                windowRange={windowRange}
                win={win}
                file={file}
                onClose={() => setSel(null)}
              />
            ) : (
              <EmptyWindow
                peak={peak}
                onPeak={() => peak && setSel({ a: peak.index, b: peak.index })}
              />
            )
          ) : null}

          {notes.length > 0 ? (
            <ul className={styles.notes} aria-label="Notes">
              {notes.map((note) => (
                <li key={note} className={styles.note}>
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      )}
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

function WindowPanel({
  sel,
  buckets,
  windowRange,
  win,
  file,
  onClose,
}: {
  sel: BucketRange;
  buckets: HistoryBucket[];
  windowRange: { from: number; to: number };
  win: ApiState<HistoryResponse>;
  file: string | null;
  onClose: () => void;
}) {
  return (
    <motion.section
      className={styles.windowPanel}
      data-window={win.data ? "ready" : win.error ? "error" : "loading"}
      aria-label="History window"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.28, ease: EASE_OUT }}
    >
      <div className={styles.windowHead}>
        <h2 className={styles.sectionLabel}>Window</h2>
        <span className={styles.windowRange}>{selectionLabel(buckets, sel.a, sel.b)}</span>
        <span className={styles.windowInstants}>
          {stampDate(windowRange.from)} → {stampDate(windowRange.to)}
        </span>
        <button type="button" className={styles.windowClose} onClick={onClose}>
          close
        </button>
      </div>

      {win.error ? (
        <p className={styles.loadingError}>{win.error.message}</p>
      ) : !win.data?.window ? (
        <p className={styles.loading}>lifting the window out of the graph…</p>
      ) : (
        <>
          {file ? (
            <p className={styles.scopeDisclosure}>
              counts below are this file&rsquo;s own diff — the commit may
              touch other files
            </p>
          ) : null}
          <div className={styles.windowGrid}>
            <div className={styles.commitList}>
              <span className={styles.colLabel}>Commits</span>
              {win.data.window.commits.length === 0 ? (
                <p className={styles.loading}>
                  no commits in this window — the months are real, the log is
                  empty here
                </p>
              ) : (
                win.data.window.commits.map((commit) => (
                  <CommitRow key={commit.sha} commit={commit} repoScope={!file} />
                ))
              )}
            </div>
            {!file ? (
              <div className={styles.fileList} aria-label="Top files by churn">
                <span className={styles.colLabel}>Top files by churn</span>
                {win.data.window.top_files.length === 0 ? (
                  <p className={styles.loading}>no files changed in this window</p>
                ) : (
                  win.data.window.top_files.map((row) => (
                    <button
                      key={row.path}
                      type="button"
                      className={styles.fileRow}
                      title={`scope the time machine to ${row.path}`}
                      onClick={() => navigate(href("/history", { file: row.path }))}
                    >
                      <span className={styles.filePath}>{clampText(row.path, 64)}</span>
                      <span className={styles.fileMeta}>
                        {formatInt(row.commits)} commits · {deltaLabel(row.added, row.removed)}
                      </span>
                    </button>
                  ))
                )}
              </div>
            ) : null}
          </div>
        </>
      )}
    </motion.section>
  );
}

function CommitRow({ commit, repoScope }: { commit: HistoryCommit; repoScope: boolean }) {
  return (
    <div className={styles.commit} data-commit={commit.sha}>
      <span className={styles.commitSha} title={commit.sha}>
        {commit.sha}
      </span>
      <div className={styles.commitBody}>
        <span className={styles.commitSummary}>
          {commit.summary ? clampText(commit.summary, 120) : "(no summary)"}
        </span>
        <span className={styles.commitMeta}>
          {commit.author ?? "unknown"} · <span title={stamp(commit.ts)}>{relTime(commit.ts)}</span>
          {repoScope && commit.touches !== undefined
            ? ` · ${formatInt(commit.touches)} file${commit.touches === 1 ? "" : "s"}`
            : ""}
        </span>
      </div>
      <span className={styles.commitDelta}>{deltaLabel(commit.added, commit.removed)}</span>
    </div>
  );
}

function EmptyWindow({
  peak,
  onPeak,
}: {
  peak: { index: number; bucket: HistoryBucket } | null;
  onPeak: () => void;
}) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>No window open.</p>
      <p className={styles.emptyBody}>
        Drag across the skyline to lift a slice of history out of the graph —
        every month tiles exactly, so the band you draw is the window you
        get. Clicking a month opens it alone.
      </p>
      {peak ? (
        <button type="button" className={styles.emptyAction} onClick={onPeak}>
          Open {peak.bucket.label} — {formatInt(peak.bucket.commits)} commits
        </button>
      ) : null}
    </div>
  );
}

function ErrorState({ error, file }: { error: { status: number; message: string }; file: string | null }) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>
        {error.status === 404 ? "No file with that path." : "The log is unreadable."}
      </p>
      <p className={styles.emptyBody}>{error.message}</p>
      {file ? (
        <button
          type="button"
          className={styles.emptyAction}
          onClick={() => navigate(href("/history"))}
        >
          Back to the whole repo
        </button>
      ) : null}
    </div>
  );
}
