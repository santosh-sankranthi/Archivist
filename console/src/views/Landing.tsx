import type { CSSProperties, MouseEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { HeroCanvas } from "../components/HeroCanvas";
import { Magnetic } from "../components/Magnetic";
import { useApi } from "../lib/api";
import { formatInt } from "../lib/format";
import { href, navigate } from "../lib/router";
import { deriveLandingStats } from "../lib/stats";
import type { GraphState } from "../lib/types";
import styles from "./Landing.module.css";

declare global {
  interface Window {
    /** Entrance timeline token — capture scripts align shots to `stagedAt`
     *  (same contract as the taste-01 spike). Set once, read-only. */
    __archivist?: { stagedAt: number };
  }
}

/** `--d` entrance delay for the staged cascade (DESIGN-BRIEF §P2.1). */
const d = (ms: number) => ({ "--d": `${ms}ms` }) as CSSProperties;

const TRACE_Q = "Who calls url_for — and what would break if its signature changes?";

const STAT_LABELS = ["NODES", "EDGES", "COMMITS", "SYMBOLS"] as const;

const CARDS = [
  {
    key: "trace",
    index: "01 — TRACE",
    title: "Every answer, wired to its proof.",
    body: "Each response ships with the exact Cypher, result rows, and graph steps behind it. Replay any step as its own subgraph.",
  },
  {
    key: "memory",
    index: "02 — MEMORY",
    title: "It remembers the investigation.",
    body: "Episodes, facts, and corrections persist in the graph — bi-temporal and append-only. Ask again next session; it picks up where you left off.",
  },
  {
    key: "crew",
    index: "03 — CREW",
    title: "Three roles, one graph.",
    body: "Planner, investigator, and verifier coordinate through hand-offs recorded as graph edges — scoped, auditable, no side channels.",
  },
];

/** Landing page at `/console/` — the hero, the stat rig, capabilities, footer.
 *  The console shell lives behind it at `/console/overview`. */
export function Landing() {
  const state = useApi<GraphState>("/api/state");
  const [staged, setStaged] = useState(false);
  const clockRef = useRef<HTMLSpanElement>(null);
  const numRefs = useRef<Array<HTMLSpanElement | null>>([]);

  /* Entrance staging — gate on fonts (180ms hard fallback), then set the
     capture timeline token and flip the root `.on` class. */
  useEffect(() => {
    let alive = true;
    let started = false;
    const stage = () => {
      if (started || !alive) return;
      started = true;
      window.__archivist = { stagedAt: performance.now() };
      setStaged(true);
    };
    const fallback = window.setTimeout(stage, 180);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(() => stage());
    } else {
      stage();
    }
    return () => {
      alive = false;
      window.clearTimeout(fallback);
    };
  }, []);

  /* Live UTC clock (cosmetic, real). */
  useEffect(() => {
    const el = clockRef.current;
    if (!el) return;
    const pad = (n: number) => String(n).padStart(2, "0");
    const tick = () => {
      const now = new Date();
      el.textContent = `${pad(now.getUTCHours())}:${pad(now.getUTCMinutes())}:${pad(now.getUTCSeconds())}`;
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, []);

  const stats = useMemo(
    () => (state.data ? deriveLandingStats(state.data) : null),
    [state.data],
  );

  /* Count-ups — easeOutCubic, 420ms each, 44ms stagger, starting 640ms after
     staging (matching the row-rise cascade). Reduced motion: instant values. */
  useEffect(() => {
    if (!staged || !stats) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const targets = [stats.nodes, stats.edges, stats.commits, stats.symbols];
    const cancels: number[] = [];
    let timer = 0;

    const run = () => {
      targets.forEach((target, idx) => {
        const el = numRefs.current[idx];
        if (!el) return;
        if (reduce) {
          el.textContent = formatInt(target);
          return;
        }
        const t0 = performance.now() + idx * 44;
        const dur = 420;
        const tickFrame = (now: number) => {
          const p = Math.min(1, Math.max(0, (now - t0) / dur));
          const v = Math.round((1 - Math.pow(1 - p, 3)) * target);
          el.textContent = formatInt(v);
          if (p < 1) cancels[idx] = requestAnimationFrame(tickFrame);
        };
        cancels[idx] = requestAnimationFrame(tickFrame);
      });
    };

    if (reduce) run();
    else timer = window.setTimeout(run, 640);

    return () => {
      window.clearTimeout(timer);
      cancels.forEach((id) => cancelAnimationFrame(id));
    };
  }, [staged, stats]);

  const stateError = state.error !== null && state.data === null;
  const rigLeft = state.data ? `INDEX — ${state.data.graph.toUpperCase()}` : "INDEX — …";
  const rigRight = state.data
    ? "QUERIED FROM FALKORDB — NOT ESTIMATED"
    : stateError
      ? "GRAPH UNREACHABLE — NO NUMBERS"
      : "READING THE GRAPH…";
  const repoLabel = state.data ? state.data.graph.toUpperCase() : "REPO:…";

  const onLink = (to: string) => (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    navigate(to);
  };

  return (
    <div className={`${styles.root}${staged ? ` ${styles.on}` : ""}`}>
      <HeroCanvas staged={staged} />

      <div className={styles.heroWrap}>
        <div className={`${styles.fx} ${styles.fxEmber}`} aria-hidden="true" />
        <div className={`${styles.fx} ${styles.fxProtect}`} aria-hidden="true" />
        <div className={`${styles.fx} ${styles.fxVignette}`} aria-hidden="true" />
        <div className={`${styles.fx} ${styles.fxGrain}`} aria-hidden="true" />

        <header className={styles.topbar}>
          <div className={`${styles.lockup} ${styles.rv}`} style={d(0)}>
            <span className={styles.lockupMark}>ARCHIVIST</span>
            <span className={styles.lockupKicker}>ENGINEERING INTELLIGENCE, MAPPED</span>
          </div>
          <div className={`${styles.topbarMeta} ${styles.rv}`} style={d(40)}>
            <span className={styles.metaOptional}>
              UTC <span className={styles.metaVal} ref={clockRef}>--:--:--</span>
            </span>
            <span className={styles.metaSep} aria-hidden="true" />
            <span className={styles.metaVal}>{repoLabel}</span>
          </div>
        </header>

        <main className={styles.hero}>
          <div className={styles.heroInner}>
            <p className={`${styles.overline} ${styles.rv}`} style={d(40)}>
              <span className={styles.nodeGlyph} aria-hidden="true" />
              FALKORDB-NATIVE ENGINEERING CONSOLE
            </p>

            <h1 className={styles.display}>
              <span className={styles.w} style={d(120)}>Every</span>{" "}
              <span className={styles.w} style={d(144)}>answer</span>{" "}
              <span className={styles.w} style={d(168)}>has</span>{" "}
              <span className={styles.w} style={d(192)}>a</span>{" "}
              <span className={`${styles.w} ${styles.wPath}`} style={d(216)}>
                <em>path</em>.
                <span className={styles.u} aria-hidden="true">
                  <svg
                    className={styles.uLine}
                    viewBox="0 0 100 8"
                    preserveAspectRatio="none"
                  >
                    <defs>
                      <linearGradient
                        id="emberline"
                        gradientUnits="userSpaceOnUse"
                        x1="1"
                        y1="4"
                        x2="99"
                        y2="4"
                      >
                        <stop offset="0" stopColor="#F0A94E" />
                        <stop offset="1" stopColor="#E06B3C" />
                      </linearGradient>
                    </defs>
                    <line x1="1" y1="4" x2="99" y2="4" stroke="url(#emberline)" />
                  </svg>
                  <span className={styles.uNode} />
                </span>
              </span>
            </h1>

            <p className={`${styles.sub} ${styles.rv}`} style={d(460)}>
              Ask your codebase anything — every answer arrives wired to the code
              that proves it.
            </p>

            <div className={`${styles.ctaRow} ${styles.rv}`} style={d(560)}>
              <Magnetic strength={7}>
                <a
                  className={`${styles.btn} ${styles.btnPrimary}`}
                  href={href("/overview")}
                  onClick={onLink(href("/overview"))}
                >
                  <span>Enter the console</span>
                  <svg
                    className={styles.btnArrow}
                    width="15"
                    height="10"
                    viewBox="0 0 15 10"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path
                      d="M0 5h12.4M9.4 1.4 13 5l-3.6 3.6"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </a>
              </Magnetic>
              <Magnetic strength={4}>
                <a
                  className={`${styles.btn} ${styles.btnGhost}`}
                  href={href("/ask", { q: TRACE_Q })}
                  onClick={onLink(href("/ask", { q: TRACE_Q }))}
                >
                  See a trace
                </a>
              </Magnetic>
            </div>
          </div>

          <div className={styles.rig}>
            <div className={styles.rigRule} aria-hidden="true" />
            <div className={`${styles.rigHead} ${styles.rv}`} style={d(600)}>
              <span>{rigLeft}</span>
              <span>{rigRight}</span>
            </div>
            <div className={styles.rigStats}>
              {STAT_LABELS.map((label, i) => (
                <div
                  key={label}
                  className={`${styles.stat} ${styles.rv}`}
                  style={d(580 + i * 44)}
                >
                  <span
                    className={styles.statNum}
                    ref={(el) => {
                      numRefs.current[i] = el;
                    }}
                  >
                    —
                  </span>
                  <span className={styles.statLab}>{label}</span>
                </div>
              ))}
            </div>
          </div>
        </main>
      </div>

      <section className={styles.below} aria-labelledby="capabilities-title">
        <div className={styles.belowInner}>
          <p className={styles.belowKicker}>WHAT’S INSIDE</p>
          <h2 id="capabilities-title" className={styles.belowTitle}>
            Built to be checked.
          </h2>
          <div className={styles.cards}>
            {CARDS.map((card) => (
              <article key={card.key} className={styles.card}>
                <span className={styles.cardIndex}>{card.index}</span>
                <h3 className={styles.cardTitle}>{card.title}</h3>
                <p className={styles.cardBody}>{card.body}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <footer className={`${styles.foot} ${styles.rv}`} style={d(800)}>
        <span>© 2026 ARCHIVIST — RUNS OFFLINE</span>
        <nav className={styles.footNav} aria-label="Footer">
          <a href={href("/overview")} onClick={onLink(href("/overview"))}>
            CONSOLE
          </a>
          <span className={styles.sep} aria-hidden="true">·</span>
          <a href={href("/explorer")} onClick={onLink(href("/explorer"))}>
            EXPLORER
          </a>
          <span className={styles.sep} aria-hidden="true">·</span>
          <a href={href("/ask")} onClick={onLink(href("/ask"))}>
            ASK
          </a>
          <span className={styles.sep} aria-hidden="true">·</span>
          <a href={href("/about")} onClick={onLink(href("/about"))}>
            ABOUT
          </a>
        </nav>
      </footer>
    </div>
  );
}
