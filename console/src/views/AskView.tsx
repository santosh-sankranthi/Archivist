/** Full-page Ask — the stage: conversation (left) + trace canvas (right).
 *  Deep links (?q=…) run once on arrival. */

import { AskRoom } from "../components/AskRoom";
import styles from "./AskView.module.css";

interface Props {
  query: URLSearchParams;
}

export function AskView({ query }: Props) {
  const question = query.get("q");
  const autoRun = question !== null && question.trim().length > 0;

  return (
    <div className={styles.wrap}>
      <AskRoom variant="stage" initialQuestion={question} autoRun={autoRun} />
    </div>
  );
}
