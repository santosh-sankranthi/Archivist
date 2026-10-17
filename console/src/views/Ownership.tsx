import { motion } from "motion/react";
import { useState } from "react";

import { useApi } from "../lib/api";
import { formatInt, relTime, shortSha, stampDate } from "../lib/format";
import { useDebounced } from "../lib/hooks";
import { EASE_OUT } from "../lib/motion";
import {
  barWidth,
  mixSegments,
  ownerInitials,
  ownershipPath,
  sharePct,
} from "../lib/ownership";
import type { MixSegment } from "../lib/ownership";
import { href, navigate, useLocation } from "../lib/router";
import type {
  FindResult,
  OwnershipCommit,
  OwnershipFileMeta,
  OwnershipFileOwner,
  OwnershipFileRow,
  OwnershipFileScope,
  OwnershipPerson,
  OwnershipRelated,
  OwnershipRepo,
  OwnershipResponse,
  OwnershipReviewer,
} from "../lib/types";
import styles from "./Ownership.module.css";

/** Ownership (M4R runway): who knows what. Repo scope ranks the people
 *  with diff-bearing history, the hottest files with their owner mix, and
 *  the single-owner files a bus factor hangs on; file scope ranks one
 *  file's authors, suggests reviewers (heuristic, labeled as such), and
 *  lists its recent commits. Every number comes from the graph's commit
 *  history — inferred, never invented, and caps are echoed from `notes`. */
export function Ownership() {
  const { query } = useLocation();
  const file = query.get("file");
  const symbol = query.get("symbol");
  const result = useApi<OwnershipResponse>(ownershipPath({ file, symbol }));
  const data = result.data;

  return (
    <div className={styles.ownership} data-ownership={data ? "ready" : "loading"}>
      <header className={styles.head}>
        <span className={styles.eyebrow}>Ownership</span>
        <h1 className={styles.title}>Who knows what.</h1>
        <p className={styles.lead}>
          Ownership inferred from the graph&rsquo;s own commit history — who
          has carried each file, who is here now, and which files run on a
          single pair of hands. No CODEOWNERS, no guessed authors: every row
          carries the commit counts it was ranked by.
        </p>
      </header>

      {result.error ? (
        <div className={styles.errorPanel}>
          <h2 className={styles.errorTitle}>No such scope.</h2>
          <p className={styles.errorBody}>{result.error.message}</p>
          <p className={styles.errorHint}>
            <a
              className={styles.errorLink}
              href={href("/ownership")}
              onClick={(event) => {
                event.preventDefault();
                navigate(href("/ownership"));
              }}
            >
              ← the whole repo&rsquo;s ownership
            </a>
          </p>
        </div>
      ) : null}

      {!data && !result.error ? (
        <p className={styles.loading}>reading the commit history…</p>
      ) : null}

      {data?.scope === "repo" ? <RepoScope data={data} /> : null}
      {data?.scope === "file" ? <FileScope data={data} /> : null}
    </div>
  );
}

/* -- repo scope ---------------------------------------------------------- */

function RepoScope({ data }: { data: OwnershipRepo }) {
  const { totals } = data;
  return (
    <>
      <section className={styles.stats} aria-label="Ownership totals">
        <Stat value={totals.authors} label="authors" />
        <Stat value={totals.files} label="files with history" />
        <Stat
          value={totals.commits_with_diff}
          label={`attributed commits of ${formatInt(totals.commits)}`}
        />
        <Stat value={totals.file_changes} label="file changes" />
        <Stat value={data.risks_total} label="single-owner files" />
      </section>

      <Gate />

      <People
        people={data.people}
        total={data.people_total}
        recentDays={data.recent_days}
      />
      <HotFiles files={data.hot_files} total={data.hot_files_total} />
      <RiskFiles files={data.risks} total={data.risks_total} />
      <Notes notes={data.notes} />
    </>
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

/** No scope yet: jump straight to any file's or symbol's ownership. */
function Gate() {
  const [text, setText] = useState("");
  const debounced = useDebounced(text.trim(), 220);
  const searching = debounced.length >= 2;
  const find = useApi<FindResult>(
    searching
      ? `/api/graph/find?q=${encodeURIComponent(debounced)}&limit=8`
      : null,
  );
  const files = find.data?.files ?? [];
  const symbols = find.data?.symbols ?? [];
  const empty =
    searching && find.data && files.length === 0 && symbols.length === 0;

  return (
    <section className={styles.gate}>
      <label className={styles.gateLabel} htmlFor="ownership-search">
        Start from a file or symbol
      </label>
      <input
        id="ownership-search"
        className={styles.gateInput}
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder="File or function — src/flask/app.py, url_for…"
        autoComplete="off"
        spellCheck={false}
      />
      {text.trim().length > 0 && !searching ? (
        <p className={styles.gateHint}>Type at least 2 characters to search.</p>
      ) : null}
      {searching ? (
        <div className={styles.gateResults}>
          {find.loading && !find.data ? (
            <p className={styles.gateHint}>searching the graph…</p>
          ) : null}
          {empty ? (
            <p className={styles.gateHint}>No matches in the archive.</p>
          ) : null}
          {files.map((hit) => (
            <button
              key={hit.path}
              type="button"
              className={styles.gateRow}
              onClick={() => navigate(href("/ownership", { file: hit.path }))}
            >
              <span className={styles.gateRowName}>{hit.path}</span>
              <span className={styles.gateRowMeta}>{formatInt(hit.loc)} loc</span>
            </button>
          ))}
          {symbols.map((hit) => (
            <button
              key={`${hit.file}:${hit.qualname}`}
              type="button"
              className={styles.gateRow}
              onClick={() =>
                navigate(
                  href("/ownership", { symbol: hit.qualname, file: hit.file }),
                )
              }
            >
              <span className={styles.gateRowName}>{hit.name}</span>
              <span className={styles.gateRowMeta}>
                {hit.file}:{hit.lineno} · {hit.kind}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function People({
  people,
  total,
  recentDays,
}: {
  people: OwnershipPerson[];
  total: number;
  recentDays: number;
}) {
  const maxShare = Math.max(...people.map((person) => person.share), 0);
  return (
    <section className={styles.section} data-section="people" aria-label="People">
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>People</h2>
        <span className={styles.sectionMeta}>
          {formatInt(total)} with diff-bearing history · ranked by commits ·
          bars relative to the top author
        </span>
      </header>
      {people.length === 0 ? (
        <p className={styles.empty}>No attributed authors in this graph yet.</p>
      ) : (
        <ol className={styles.barList}>
          {people.map((person, index) => (
            <BarRow
              key={person.email}
              rank={index + 1}
              name={person.name}
              email={person.email}
              bot={person.bot}
              share={person.share}
              maxShare={maxShare}
              meta={`${formatInt(person.commits)} commits · ${formatInt(person.files)} files · last touch ${
                person.last_ts ? relTime(person.last_ts) : "—"
              }`}
              tags={
                person.recent_commits > 0 ? (
                  <span className={styles.recentTag}>
                    {formatInt(person.recent_commits)} in last {recentDays}d
                  </span>
                ) : null
              }
            />
          ))}
        </ol>
      )}
    </section>
  );
}

/** Shared ranked row: rank, initials, name + meta, share bar, printed share. */
function BarRow({
  rank,
  name,
  email,
  bot,
  share,
  maxShare,
  meta,
  tags,
}: {
  rank: number;
  name: string;
  email: string;
  bot: boolean;
  share: number;
  maxShare: number;
  meta: string;
  tags?: React.ReactNode;
}) {
  return (
    <li className={styles.barRow}>
      <span className={styles.rank}>{String(rank).padStart(2, "0")}</span>
      <span className={styles.avatar} aria-hidden="true">
        {ownerInitials(name)}
      </span>
      <span className={styles.barRowMain}>
        <span className={styles.nameLine}>
          <span className={styles.barRowName} title={email}>
            {name}
          </span>
          {bot ? <span className={styles.botTag}>bot</span> : null}
          {tags}
        </span>
        <span className={styles.barRowMeta}>{meta}</span>
      </span>
      <span className={styles.barWrap}>
        <motion.span
          className={styles.barFill}
          initial={{ width: 0 }}
          animate={{ width: `${barWidth(share, maxShare)}%` }}
          transition={{
            duration: 0.55,
            ease: EASE_OUT,
            delay: Math.min(0.03 * rank, 0.3),
          }}
        />
      </span>
      <span className={styles.shareValue}>{sharePct(share)}</span>
    </li>
  );
}

function HotFiles({
  files,
  total,
}: {
  files: OwnershipFileRow[];
  total: number;
}) {
  return (
    <section
      className={styles.section}
      data-section="hot-files"
      aria-label="Hot files"
    >
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>Hot files</h2>
        <span className={styles.sectionMeta}>
          {total > files.length
            ? `${files.length} most-touched of ${formatInt(total)} files with history`
            : `${formatInt(total)} files ranked by commits`}{" "}
          · owner mix over all their commits
        </span>
      </header>
      <div className={styles.fileList}>
        {files.map((fileRow) => (
          <FileRow key={fileRow.path} file={fileRow} />
        ))}
      </div>
    </section>
  );
}

function FileRow({ file }: { file: OwnershipFileRow }) {
  const segments = mixSegments(file.owners, 3, file.authors);
  const legend = file.owners.slice(0, 3);
  const hidden = Math.max(0, file.authors - legend.length);
  return (
    <button
      type="button"
      className={styles.fileRow}
      onClick={() => navigate(href("/ownership", { file: file.path }))}
      title={`Owners of ${file.path}`}
    >
      <span className={styles.fileRowHead}>
        <span className={styles.filePath}>{file.path}</span>
        {file.tests ? <span className={styles.testTag}>test</span> : null}
        <span className={styles.fileMeta}>
          {formatInt(file.commits)} commits · {formatInt(file.authors)} authors
          · churn {formatInt(file.churn)}
        </span>
      </span>
      <span className={styles.fileMix}>
        <MixBar segments={segments} />
        <span className={styles.mixLegend}>
          {legend.map((owner) => (
            <span key={owner.email} className={styles.mixEntry}>
              {owner.name} {sharePct(owner.share)}
            </span>
          ))}
          {hidden > 0 ? (
            <span className={styles.mixEntry}>+{formatInt(hidden)} more</span>
          ) : null}
        </span>
      </span>
    </button>
  );
}

/** Stacked owner-mix bar; segments carry their exact shares as widths, the
 *  legend next to it carries the numbers. The sweep is one draw-in. */
function MixBar({ segments }: { segments: MixSegment[] }) {
  const sum = segments.reduce((total, segment) => total + segment.share, 0) || 1;
  return (
    <motion.span
      className={styles.mixBar}
      initial={{ scaleX: 0 }}
      animate={{ scaleX: 1 }}
      transition={{ duration: 0.55, ease: EASE_OUT, delay: 0.08 }}
    >
      {segments.map((segment) => (
        <span
          key={segment.key}
          className={
            segment.rest ? `${styles.mixSeg} ${styles.mixSegRest}` : styles.mixSeg
          }
          style={{ width: `${(segment.share / sum) * 100}%` }}
          title={`${segment.label} — ${sharePct(segment.share)}`}
        />
      ))}
    </motion.span>
  );
}

function RiskFiles({
  files,
  total,
}: {
  files: OwnershipFileRow[];
  total: number;
}) {
  return (
    <section
      className={styles.section}
      data-section="risks"
      aria-label="Single-owner files"
    >
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>Single-owner files</h2>
        <span className={styles.sectionMeta}>
          {total > files.length
            ? `${files.length} of ${formatInt(total)} signals · ranked by commits`
            : `${formatInt(total)} signals · ranked by commits`}{" "}
          · a watch list, not a policy
        </span>
      </header>
      {files.length === 0 ? (
        <p className={styles.empty}>
          No file carries the threshold; ownership is spread evenly.
        </p>
      ) : (
        <div className={styles.riskList}>
          {files.map((file) => {
            const top = file.owners[0];
            const primary = file.primary_share ?? (top ? top.share : 0);
            return (
              <button
                key={file.path}
                type="button"
                className={styles.riskRow}
                onClick={() => navigate(href("/ownership", { file: file.path }))}
                title={`Owners of ${file.path}`}
              >
                <span className={styles.riskShare}>{sharePct(primary)}</span>
                <span className={styles.riskMain}>
                  <span className={styles.riskStory}>
                    <span className={styles.filePath}>{file.path}</span>
                    {file.tests ? (
                      <span className={styles.testTag}>test</span>
                    ) : null}
                  </span>
                  <span className={styles.riskMeta}>
                    {top
                      ? `${top.name} carries ${formatInt(top.commits)} of ${formatInt(file.commits)} commits`
                      : ""}
                    {" · "}
                    {formatInt(file.authors)} authors
                    {file.last_ts ? ` · last touch ${relTime(file.last_ts)}` : ""}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

/* -- file scope ---------------------------------------------------------- */

function FileScope({ data }: { data: OwnershipFileScope }) {
  const file = data.file;
  return (
    <>
      <div className={styles.scopeBar}>
        <a
          className={styles.backLink}
          href={href("/ownership")}
          onClick={(event) => {
            event.preventDefault();
            navigate(href("/ownership"));
          }}
        >
          ← all people
        </a>
        <div className={styles.scopeActions}>
          <a
            className={styles.actionLink}
            href={href("/explorer", { path: file.path })}
            onClick={(event) => {
              event.preventDefault();
              navigate(href("/explorer", { path: file.path }));
            }}
          >
            open in explorer →
          </a>
          <a
            className={styles.actionLink}
            href={href("/history", { file: file.path })}
            onClick={(event) => {
              event.preventDefault();
              navigate(href("/history", { file: file.path }));
            }}
          >
            time machine →
          </a>
        </div>
      </div>

      <FileCard
        file={file}
        recentTotal={data.recent_total}
        recentDays={data.recent_days}
      />

      <FileOwners
        owners={data.owners}
        total={data.owners_total}
        recentDays={data.recent_days}
      />
      <Reviewers reviewers={data.reviewers} />
      <RecentCommits
        rows={data.recent_commits}
        total={data.recent_total}
        recentDays={data.recent_days}
      />
      <Related paths={data.related_paths} />
      <Notes notes={data.notes} />
    </>
  );
}

function FileCard({
  file,
  recentTotal,
  recentDays,
}: {
  file: OwnershipFileMeta;
  recentTotal: number;
  recentDays: number;
}) {
  return (
    <>
      <div className={styles.fileCard} data-file-scope="">
        <div className={styles.fileCardMain}>
          <span className={styles.fileCardKind}>file</span>
          <span className={styles.fileCardPath}>{file.path}</span>
          <span className={styles.fileCardChips}>
            {file.lang ?? "—"} · {formatInt(file.loc ?? 0)} loc ·{" "}
            {formatInt(file.symbols)} symbols{file.tests ? " · test path" : ""}
          </span>
        </div>
      </div>
      <section className={styles.stats} aria-label="File totals">
        <Stat value={file.commits} label="commits" />
        <Stat value={file.authors} label="authors" />
        <div className={styles.stat} title={`plus ${formatInt(file.added)}, minus ${formatInt(file.removed)}`}>
          <span className={styles.statValue}>{formatInt(file.churn)}</span>
          <span className={styles.statLabel}>
            churn (+{formatInt(file.added)} −{formatInt(file.removed)})
          </span>
        </div>
        <div className={styles.stat}>
          <span className={styles.statValueSmall}>
            {file.first_ts && file.last_ts
              ? `${stampDate(file.first_ts)} → ${stampDate(file.last_ts)}`
              : "—"}
          </span>
          <span className={styles.statLabel}>first → last commit</span>
        </div>
        <Stat
          value={recentTotal}
          label={`commits in last ${recentDays}d`}
        />
      </section>
    </>
  );
}

function FileOwners({
  owners,
  total,
  recentDays,
}: {
  owners: OwnershipFileOwner[];
  total: number;
  recentDays: number;
}) {
  const maxShare = Math.max(...owners.map((owner) => owner.share), 0);
  return (
    <section
      className={styles.section}
      data-section="owners"
      aria-label="Owners"
    >
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>Owners</h2>
        <span className={styles.sectionMeta}>
          {total > owners.length
            ? `${owners.length} of ${formatInt(total)} owners · shares over all commits`
            : `${formatInt(total)} owners · shares over all commits`}
        </span>
      </header>
      {owners.length === 0 ? (
        <p className={styles.empty}>
          No diff-bearing commits touch this path — ownership is unknown (a
          new file, or only renamed into place).
        </p>
      ) : (
        <ol className={styles.barList}>
          {owners.map((owner, index) => (
            <BarRow
              key={owner.email}
              rank={index + 1}
              name={owner.name}
              email={owner.email}
              bot={owner.bot}
              share={owner.share}
              maxShare={maxShare}
              meta={`${formatInt(owner.commits)} commits · +${formatInt(
                owner.added,
              )} −${formatInt(owner.removed)} · last touch ${
                owner.last_ts ? relTime(owner.last_ts) : "—"
              }`}
              tags={
                owner.recent_commits > 0 ? (
                  <span className={styles.recentTag}>
                    {formatInt(owner.recent_commits)} in last {recentDays}d
                  </span>
                ) : null
              }
            />
          ))}
        </ol>
      )}
    </section>
  );
}

function Reviewers({ reviewers }: { reviewers: OwnershipReviewer[] }) {
  if (reviewers.length === 0) {
    return null;
  }
  return (
    <section
      className={styles.section}
      data-section="reviewers"
      aria-label="Suggested reviewers"
    >
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>If this file changes, ask</h2>
        <span className={styles.sectionMeta}>
          2× recent + all-time commits — a heuristic, not policy
        </span>
      </header>
      <ol className={styles.reviewers}>
        {reviewers.map((reviewer, index) => (
          <li key={reviewer.email} className={styles.reviewer}>
            <span className={styles.rank}>0{index + 1}</span>
            <span className={styles.avatar} aria-hidden="true">
              {ownerInitials(reviewer.name)}
            </span>
            <span className={styles.barRowMain}>
              <span className={styles.barRowName} title={reviewer.email}>
                {reviewer.name}
              </span>
              <span className={styles.barRowMeta}>{reviewer.why}</span>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function RecentCommits({
  rows,
  total,
  recentDays,
}: {
  rows: OwnershipCommit[];
  total: number;
  recentDays: number;
}) {
  const overflow = total > rows.length;
  return (
    <section
      className={styles.section}
      data-section="commits"
      aria-label="Recent commits"
    >
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>Recent commits</h2>
        <span className={styles.sectionMeta}>
          {formatInt(total)} in the last {recentDays} days
          {overflow ? ` · newest ${rows.length} shown` : ""}
        </span>
      </header>
      {rows.length === 0 ? (
        <p className={styles.empty}>
          No diff-bearing commits in the last {recentDays} days.
        </p>
      ) : (
        <div className={styles.commitList}>
          {rows.map((row) => (
            <div key={row.sha} className={styles.commitRow}>
              <span className={styles.commitSha}>{shortSha(row.sha)}</span>
              <span className={styles.commitSummary} title={row.summary}>
                {row.summary}
              </span>
              <span className={styles.commitAuthor}>{row.author}</span>
              <span className={styles.commitMeta}>
                {row.ts ? relTime(row.ts) : "—"} · +{formatInt(row.added)} −
                {formatInt(row.removed)}
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function Related({ paths }: { paths: OwnershipRelated[] }) {
  if (paths.length === 0) {
    return null;
  }
  return (
    <section
      className={styles.section}
      data-section="related"
      aria-label="Likely predecessors"
    >
      <header className={styles.sectionHead}>
        <h2 className={styles.sectionTitle}>Likely predecessors</h2>
        <span className={styles.sectionMeta}>
          same path with/without the src/ prefix · not merged into the totals
          above
        </span>
      </header>
      <div className={styles.relatedList}>
        {paths.map((related) => (
          <button
            key={`${related.path}:${related.relation}`}
            type="button"
            className={styles.relatedRow}
            onClick={() =>
              navigate(href("/ownership", { file: related.path }))
            }
          >
            <span className={styles.filePath}>{related.path}</span>
            <span className={styles.relatedMeta}>
              {formatInt(related.commits)} commits ·{" "}
              {related.first_ts && related.last_ts
                ? `${stampDate(related.first_ts)} → ${stampDate(related.last_ts)}`
                : "—"}{" "}
              · {related.relation} window · inspect →
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function Notes({ notes }: { notes: string[] }) {
  if (notes.length === 0) {
    return null;
  }
  return (
    <ul className={styles.notes} aria-label="Notes">
      {notes.map((note) => (
        <li key={note} className={styles.note}>
          {note}
        </li>
      ))}
    </ul>
  );
}
