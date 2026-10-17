import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { useEffect, useState } from "react";

import { CommandPalette } from "./components/CommandPalette";
import { LeftRail } from "./components/LeftRail";
import { RightRail } from "./components/RightRail";
import { Settings } from "./components/Settings";
import { TopBar } from "./components/TopBar";
import { useApi } from "./lib/api";
import { useMediaQuery } from "./lib/hooks";
import { ROUTE_TRANSITION } from "./lib/motion";
import { href, navigate, useLocation } from "./lib/router";
import type { GraphState, Overview } from "./lib/types";
import { About } from "./views/About";
import { AskView } from "./views/AskView";
import { AccountView, LoginView, SignupView } from "./views/Auth";
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const narrow = useMediaQuery("(max-width: 720px)");
  const authRoute =
    path === "/login" || path === "/signup" || path === "/account";
  // Below 720px the rails step aside for every route (not just auth) so the
  // center column gets the full viewport; the top-bar "Menu" button opens the
  // ⌘K palette as the discoverable mobile replacement (audit #5).
  const showRail = !narrow;

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
        overviewError={overview.error?.message ?? null}
        onRetryOverview={overview.reload}
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
  } else if (path === "/login") {
    view = <LoginView />;
  } else if (path === "/signup") {
    view = <SignupView />;
  } else if (path === "/account") {
    view = <AccountView />;
  } else {
    view = <OffMap />;
  }

  return (
    <div className={styles.app}>
      <TopBar state={state.data} onOpenPalette={() => setPaletteOpen(true)} onOpenSettings={() => setSettingsOpen(true)} />
      <div className={`${styles.main}${authRoute ? ` ${styles.mainAuth}` : ""}`}>
        {showRail ? (
            <LeftRail
              path={path}
              state={state.data}
              overview={overview.data}
              overviewError={overview.error?.message ?? null}
              onOpenPalette={() => setPaletteOpen(true)}
            />
        ) : null}
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
        {authRoute || narrow ? null : (
          <RightRail open={rightOpen} onToggle={() => setRightOpen((open) => !open)} />
        )}
      </div>
      <CommandPalette
        open={paletteOpen}
        graph={state.data?.graph ?? null}
        onClose={() => setPaletteOpen(false)}
      />
      {settingsOpen ? <Settings onClose={() => setSettingsOpen(false)} /> : null}
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
