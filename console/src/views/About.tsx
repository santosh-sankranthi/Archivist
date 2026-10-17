import { formatInt } from "../lib/format";
import { href, navigate } from "../lib/router";
import { graphTotals } from "../lib/stats";
import type { GraphState } from "../lib/types";
import styles from "./About.module.css";

interface Props {
  state: GraphState | null;
  stateError: string | null;
}

/** About / judges page — what this is, the pipeline diagram, FalkorDB's
 *  load-bearing role, the track map, and the honest limits. Every claim
 *  here points at a module in the repo or a live number on this console —
 *  the numbers interpolate from `/api/state` (refresh like the shell),
 *  never frozen at authoring time. */
export function About({ state, stateError }: Props) {
  const totals = state ? graphTotals(state) : null;
  const files = totals?.byLabel.get("File") ?? null;
  const commits = totals?.byLabel.get("Commit") ?? null;
  const authors = totals?.byLabel.get("Author") ?? null;
  const num = (value: number | null): string => (value === null ? "—" : formatInt(value));

  return (
    <div className={styles.about}>
      <section className={styles.head}>
        <p className={styles.eyebrow}>About — for judges &amp; the curious</p>
        <h1 className={styles.title}>One page on the whole thing.</h1>
        <p className={styles.lead}>
          Archivist is a FalkorDB-native engineering-intelligence agent. It turns a codebase
          and its git history into a living property graph — then answers questions over that
          graph, showing every step.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="about-what">
        <h2 id="about-what" className={styles.h2}>
          What it is
        </h2>
        <p className={styles.body}>
          Archivist ingests a Python repository — source and full history — into a single
          FalkorDB graph of files, modules, symbols, calls, commits, and authors. This console
          is a window on that graph: browse call neighbourhoods, trace paths between symbols,
          replay the exact queries an answer used, and see what the agent remembers from
          earlier sessions.
        </p>
        <p className={styles.body}>
          The thesis is <em>traceable decisions</em>. Every answer renders as a ribbon of
          steps — tool, result, exact Cypher, milliseconds — so nothing is a black box, and
          every claim on this site can be checked against the live graph.
        </p>
        <div className={styles.jump}>
          <a
            className={styles.jumpLink}
            href={href("/explorer")}
            onClick={(event) => {
              event.preventDefault();
              navigate(href("/explorer"));
            }}
          >
            Open the Explorer
          </a>
          <a
            className={styles.jumpLink}
            href={href("/ask")}
            onClick={(event) => {
              event.preventDefault();
              navigate(href("/ask"));
            }}
          >
            Ask the archive
          </a>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="about-pipeline">
        <h2 id="about-pipeline" className={styles.h2}>
          The pipeline
        </h2>
        <ArchDiagram repo={state?.repo?.name ?? state?.graph ?? "flask"} files={files} commits={commits} />
        <p className={styles.caption}>
          One path in; a single store at the centre. The console, the agent&rsquo;s tools, and
          memory recall all read the same graph — no side databases, no cached copies.
        </p>
      </section>

      <section className={styles.section} aria-labelledby="about-falkordb">
        <h2 id="about-falkordb" className={styles.h2}>
          FalkorDB, load-bearing
        </h2>
        <ul className={styles.claims}>
          <li className={styles.claim}>
            <span className={styles.claimLead}>Single source of truth.</span> Everything the
            agent and this console know is a Cypher query away; the JSON you see is a
            projection over read-only queries (<code>webapp.py</code>, <code>tools.py</code>).
          </li>
          <li className={styles.claim}>
            <span className={styles.claimLead}>Algorithms, not decoration.</span>{" "}
            <code>shortestPath</code> powers the path finder and trace replays; transitive
            call walks power the impact questions (<code>/api/graph/path</code>).
          </li>
          <li className={styles.claim}>
            <span className={styles.claimLead}>Memory lives in-graph.</span> Investigations
            are <code>(:Episode)</code> nodes; facts are bi-temporal <code>(:Fact)</code>{" "}
            nodes with valid/expired windows — superseded, never silently deleted (
            <code>memory.py</code>).
          </li>
          <li className={styles.claim}>
            <span className={styles.claimLead}>Deterministic ingest.</span> AST + git parsing
            with a resolver that flags its own guesses as <code>weak</code>; the console
            counts them instead of hiding them (<code>ingest.py</code>,{" "}
            <code>calls.py</code>).
          </li>
          <li className={styles.claim}>
            <span className={styles.claimLead}>Indexed for the reads we actually make.</span>{" "}
            Range + fulltext indexes are applied at ingest and EXPLAIN-verified
            (<code>ingest.py</code>).
          </li>
        </ul>
      </section>

      <section className={styles.section} aria-labelledby="about-tracks">
        <h2 id="about-tracks" className={styles.h2}>
          Track map
        </h2>
        <div className={styles.tracks}>
          <article className={styles.track}>
            <span className={styles.trackIndex}>01</span>
            <h3 className={styles.trackTitle}>Agents that act on connected data</h3>
            <p className={styles.trackBody}>
              Multi-hop impact and ownership questions answered by a tool-using agent — with
              a read-only Cypher escape hatch for the unscripted ones. The path finder and
              Trace Ribbon make its moves inspectable.
            </p>
          </article>
          <article className={styles.track}>
            <span className={styles.trackIndex}>02</span>
            <h3 className={styles.trackTitle}>Agent memory &amp; coordination</h3>
            <p className={styles.trackBody}>
              Episodes and bi-temporal facts persist investigations across sessions; a
              planner → investigator → verifier crew hands off through the graph itself.
            </p>
          </article>
          <article className={styles.track}>
            <span className={styles.trackIndex}>03</span>
            <h3 className={styles.trackTitle}>Company brain</h3>
            <p className={styles.trackBody}>
              People ↔ code: {num(authors)} authors, ownership by area, and {num(commits)}{" "}
              commits of archaeology — &ldquo;who should review this?&rdquo; answered from
              history, not vibes.
            </p>
          </article>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="about-limits">
        <h2 id="about-limits" className={styles.h2}>
          Honest limits
        </h2>
        <ul className={styles.limits}>
          <li>
            Call resolution is heuristic by design: ambiguous edges carry a <code>weak</code>{" "}
            flag and are surfaced in counts — never guessed silently.
          </li>
          <li>
            {state === null ? (
              <>Ask needs a model key — Explorer and Memory are fully live either way.</>
            ) : state.llm.configured ? (
              <>
                Ask runs on this box&rsquo;s model,{" "}
                <code>{state.llm.model ?? "model set"}</code> — and visitors may bring
                their own key. Explorer and Memory are fully live either way.
              </>
            ) : (
              <>
                Ask needs a model key, and this demo box has none — captured answers run
                on scripted scenarios, and visitors may bring their own key. Explorer and
                Memory are fully live either way.
              </>
            )}
          </li>
          <li>
            One repository per graph (<code>{state?.graph ?? "repo:flask"}</code> here):
            multi-tenant by design, single-repo by scope.
          </li>
        </ul>
      </section>

      <div className={styles.footNote}>
        {state && totals ? (
          <>
            Read live from <code>{state.graph}</code> — {formatInt(totals.nodes)} nodes,{" "}
            {formatInt(totals.edges)} edges, {formatInt(state.memory.sessions)} sessions in
            memory.
          </>
        ) : stateError ? (
          <>Live numbers unavailable right now — {stateError}.</>
        ) : (
          <>Reading the live graph…</>
        )}
      </div>
    </div>
  );
}

/** The pipeline diagram — inline SVG, drawn to the Ink & Signal tokens.
 *  Restrained on purpose: hairlines for flow, amber only where the store
 *  sits, mono labels like a schematic. */
function ArchDiagram({
  repo,
  files,
  commits,
}: {
  repo: string;
  files: number | null;
  commits: number | null;
}) {
  const chip = { h: 22, textDy: 15 };
  function Chip({
    x,
    y,
    w,
    label,
  }: {
    x: number;
    y: number;
    w: number;
    label: string;
  }) {
    return (
      <>
        <rect className={styles.dgChip} x={x} y={y} width={w} height={chip.h} rx={4} />
        <text className={styles.dgChipText} x={x + 9} y={y + chip.textDy}>
          {label}
        </text>
      </>
    );
  }

  return (
    <svg
      className={styles.diagram}
      viewBox="0 0 960 300"
      role="img"
      aria-label="Architecture: the codebase and git history flow through deterministic ingest into a FalkorDB property graph; the tool suite, agent loop, memory, and console read from that graph, and episodes and facts are written back to it."
    >
      <defs>
        <marker
          id="arch-arrow"
          markerWidth="8"
          markerHeight="8"
          refX="7"
          refY="4"
          orient="auto"
        >
          <path className={styles.dgArrowHead} d="M0,0 L8,4 L0,8 Z" />
        </marker>
      </defs>

      {/* Stage labels */}
      <text className={styles.dgLabel} x={0} y={14}>
        01 · Sources
      </text>
      <text className={styles.dgLabel} x={250} y={14}>
        02 · Ingest
      </text>
      <text className={styles.dgLabel} x={500} y={14}>
        03 · FalkorDB graph
      </text>
      <text className={styles.dgLabel} x={800} y={14}>
        04 · Consumers
      </text>

      {/* 01 — sources */}
      <rect className={styles.dgBox} x={0} y={28} width={200} height={148} rx={8} />
      <text className={styles.dgSub} x={16} y={52}>
        codebase
      </text>
      <text className={styles.dgValue} x={16} y={70}>
        {repo} — {files === null ? "…" : `${formatInt(files)} files`}
      </text>
      <text className={styles.dgSub} x={16} y={102}>
        git history
      </text>
      <text className={styles.dgValue} x={16} y={120}>
        {commits === null ? "…" : `${formatInt(commits)} commits`}
      </text>

      {/* 02 — ingest */}
      <rect className={styles.dgBox} x={250} y={28} width={200} height={148} rx={8} />
      <text className={styles.dgSub} x={266} y={52}>
        pipeline
      </text>
      <text className={styles.dgValue} x={266} y={70}>
        AST parse + git walk
      </text>
      <text className={styles.dgValue} x={266} y={98}>
        name resolver v1.1
      </text>
      <text className={styles.dgSub} x={266} y={124}>
        weak edges flagged
      </text>

      {/* 03 — the store */}
      <rect className={styles.dgCore} x={500} y={20} width={260} height={164} rx={10} />
      <Chip x={516} y={56} w={48} label="File" />
      <Chip x={572} y={56} w={64} label="Module" />
      <Chip x={644} y={56} w={60} label="Symbol" />
      <Chip x={516} y={88} w={62} label="Commit" />
      <Chip x={586} y={88} w={58} label="Author" />
      <Chip x={516} y={120} w={64} label="Episode" />
      <Chip x={588} y={120} w={44} label="Fact" />
      <line className={styles.dgLine} x1={516} y1={150} x2={744} y2={150} />
      <text className={styles.dgSub} x={516} y={168}>
        7 index definitions · graph.py
      </text>

      {/* 04 — consumers */}
      <rect className={styles.dgBox} x={800} y={28} width={160} height={148} rx={8} />
      <text className={styles.dgValue} x={816} y={56}>
        tool suite
      </text>
      <text className={styles.dgValue} x={816} y={78}>
        agent loop · trace
      </text>
      <text className={styles.dgValue} x={816} y={100}>
        memory · crew
      </text>
      <text className={styles.dgValue} x={816} y={122}>
        this console
      </text>

      {/* Spine */}
      <line
        className={styles.dgLine}
        x1={200}
        y1={102}
        x2={250}
        y2={102}
        markerEnd="url(#arch-arrow)"
      />
      <line
        className={styles.dgLine}
        x1={450}
        y1={102}
        x2={500}
        y2={102}
        markerEnd="url(#arch-arrow)"
      />
      <line
        className={styles.dgLine}
        x1={760}
        y1={102}
        x2={800}
        y2={102}
        markerEnd="url(#arch-arrow)"
      />

      {/* Write-back loop */}
      <path
        className={styles.dgDash}
        d="M880,176 V244 H630 V184"
        markerEnd="url(#arch-arrow)"
        fill="none"
      />
      <text className={styles.dgSub} x={755} y={262} textAnchor="middle">
        episodes &amp; facts written back
      </text>
    </svg>
  );
}
