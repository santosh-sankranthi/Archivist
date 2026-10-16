import { useEffect, useRef, useState } from "react";

import { LeftRail } from "./components/LeftRail";
import { RightRail } from "./components/RightRail";
import { TopBar } from "./components/TopBar";
import { useApi } from "./lib/api";
import { href, navigate, useLocation } from "./lib/router";
import type { GraphState, Overview } from "./lib/types";
import { AskView } from "./views/AskView";
import { Explorer } from "./views/Explorer";
import { Home } from "./views/Home";
import styles from "./App.module.css";

/** Shell: top bar, left rail, center canvas, collapsible Ask rail. */
export function App() {
  const { path, query } = useLocation();
  const state = useApi<GraphState>("/api/state", 60_000);
  const overview = useApi<Overview>("/api/graph/overview");
  const searchRef = useRef<HTMLInputElement>(null);
  const [rightOpen, setRightOpen] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "k" &&
        !event.altKey
      ) {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  let view: React.ReactNode;
  if (path === "/") {
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
  } else {
    view = <OffMap />;
  }

  return (
    <div className={styles.app}>
      <TopBar state={state.data} onFocusSearch={() => searchRef.current?.focus()} />
      <div className={styles.main}>
        <LeftRail
          path={path}
          state={state.data}
          overview={overview.data}
          searchRef={searchRef}
        />
        <main className={styles.center}>{view}</main>
        <RightRail open={rightOpen} onToggle={() => setRightOpen((open) => !open)} />
      </div>
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
        href={href("/")}
        onClick={(event) => {
          event.preventDefault();
          navigate(href("/"));
        }}
      >
        Back to the archive
      </a>
    </div>
  );
}
