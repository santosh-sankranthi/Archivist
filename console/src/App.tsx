import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { useEffect, useState } from "react";

import { CommandPalette } from "./components/CommandPalette";
import { LeftRail } from "./components/LeftRail";
import { RightRail } from "./components/RightRail";
import { TopBar } from "./components/TopBar";
import { useApi } from "./lib/api";
import { ROUTE_TRANSITION } from "./lib/motion";
import { href, navigate, useLocation } from "./lib/router";
import type { GraphState, Overview } from "./lib/types";
import { About } from "./views/About";
import { AskView } from "./views/AskView";
import { Crew } from "./views/Crew";
import { Explorer } from "./views/Explorer";
import { History } from "./views/History";
import { Home } from "./views/Home";
import { Impact } from "./views/Impact";
import { Landing } from "./views/Landing";
import { Memory } from "./views/Memory";
import { Ownership } from "./views/Ownership";
import styles from "./App.module.css";

/** Route split: `/console/` is the landing; everything else runs in the shell.
 *  `MotionConfig reducedMotion="user"` keeps every Framer Motion animation
 *  on the reduced-motion parity path automatically (P2.2). */
export function App() {
  const { path } = useLocation();
  return (
    <MotionConfig reducedMotion="user">
      {path === "/" ? <Landing /> : <Shell />}
    </MotionConfig>
  );
}

/** Shell: top bar, left rail, center canvas, collapsible Ask rail, ⌘K palette. */
function Shell() {
  const { path, query } = useLocation();
  const state = useApi<GraphState>("/api/state", 60_000);
  const overview = useApi<Overview>("/api/graph/overview");
  const [rightOpen, setRightOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "k" &&
        !event.altKey
      ) {
        event.preventDefault();
        setPaletteOpen(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  let view: React.ReactNode;
  if (path === "/overview") {
    view = (
      <Home
        state={state.data}
        stateError={state.error?.message ?? null}
        overview={overview.data}
      />
    );
  } else if (path === "/explorer") {
    view = <Explorer query={query} />;
  } else if (path === "/ask") {
    view = <AskView query={query} />;
  } else if (path === "/memory") {
    view = <Memory />;
  } else if (path === "/crew") {
    view = <Crew />;
  } else if (path === "/history") {
    view = <History />;
  } else if (path === "/impact") {
    view = <Impact />;
  } else if (path === "/ownership") {
    view = <Ownership />;
  } else if (path === "/about") {
    view = <About state={state.data} stateError={state.error?.message ?? null} />;
  } else {
    view = <OffMap />;
  }

  return (
    <div className={styles.app}>
      <TopBar state={state.data} onOpenPalette={() => setPaletteOpen(true)} />
      <div className={styles.main}>
        <LeftRail
          path={path}
          state={state.data}
          overview={overview.data}
          onOpenPalette={() => setPaletteOpen(true)}
        />
        <main className={styles.center}>
          <AnimatePresence mode="wait">
            <motion.div
              key={path}
              className={styles.view}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={ROUTE_TRANSITION}
            >
              {view}
            </motion.div>
          </AnimatePresence>
        </main>
        <RightRail open={rightOpen} onToggle={() => setRightOpen((open) => !open)} />
      </div>
      <CommandPalette
        open={paletteOpen}
        graph={state.data?.graph ?? null}
        onClose={() => setPaletteOpen(false)}
      />
    </div>
  );
}

function OffMap() {
  return (
    <div className={styles.offMap}>
      <h1 className={styles.offMapTitle}>Off the map.</h1>
      <p className={styles.offMapBody}>No view lives at this address.</p>
      <a
        className={styles.offMapLink}
        href={href("/overview")}
        onClick={(event) => {
          event.preventDefault();
          navigate(href("/overview"));
        }}
      >
        Back to the archive
      </a>
    </div>
  );
}
