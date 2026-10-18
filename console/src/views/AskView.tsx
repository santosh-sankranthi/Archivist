/** Full-page Ask — the stage: conversation (left) + trace canvas (right).
 *  Deep links (?q=…) run once on arrival. */

import { AskRoom } from "../components/AskRoom";
import styles from "./AskView.module.css";

interface Props {
  query: URLSearchParams;
}

export function AskView({ query }: Props) {
  const question = query.get("q");
  // `run=0` is the landing CTA's "prefill but don't auto-spend" signal
  // (audit #6): it deep-links with the question preloaded, and the visitor
  // decides when to press Ask. Every other `?q=` source (starters, palette,
  // explorer/impact deep links) auto-runs once on arrival.
  const autoRun =
    question !== null && question.trim().length > 0 && query.get("run") !== "0";
  // `explain=1` opens the room with a canned "how a trace works" explainer —
  // no model call, so it never spends quota.
  const explain = query.get("explain") === "1";

  return (
    <div className={styles.wrap}>
      <AskRoom
        variant="stage"
        initialQuestion={question}
        autoRun={autoRun}
        explain={explain}
      />
    </div>
  );
}
