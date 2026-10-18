/** Full-page Ask — the workspace: sessions rail (left), conversation and
 *  trace canvas (right). Deep links (?q=…) run once on arrival; ?new=<ts>
 *  remounts the stage clean (the rail's "new question" signal); ?s=<id>
 *  opens one stored session read-only (the rail's history). */

import { AskRoom } from "../components/AskRoom";
import { SessionPeek } from "../components/SessionPeek";
import { SessionRail } from "../components/SessionRail";
import styles from "./AskView.module.css";

interface Props {
  query: URLSearchParams;
}

export function AskView({ query }: Props) {
  const question = query.get("q");
  const session = query.get("s");
  // `run=0` is the landing CTA's "prefill but don't auto-spend" signal
  // (audit #6): it deep-links with the question preloaded, and the visitor
  // decides when to press Ask. Every other `?q=` source (starters, palette,
  // explorer/impact deep links) auto-runs once on arrival.
  const autoRun =
    question !== null && question.trim().length > 0 && query.get("run") !== "0";
  // `explain=1` opens the room with a canned "how a trace works" explainer —
  // no model call, so it never spends quota.
  const explain = query.get("explain") === "1";
  // Remount key: a distinct question or an explicit "new" reset gets a fresh
  // stage; plain navigations to /ask keep the live conversation intact.
  const roomKey = `${query.get("new") ?? ""}|${session ?? ""}|${question ?? ""}`;

  return (
    <div className={styles.workspace}>
      <SessionRail />
      <div className={styles.wrap}>
        {session ? (
          <SessionPeek key={session} episodeId={session} />
        ) : (
          <AskRoom
            key={roomKey}
            variant="stage"
            initialQuestion={question}
            autoRun={autoRun}
            explain={explain}
          />
        )}
      </div>
    </div>
  );
}
