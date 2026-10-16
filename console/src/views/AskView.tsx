import { AskPanel } from "../components/AskPanel";
import shared from "./view.module.css";

interface Props {
  query: URLSearchParams;
}

/** Full-page Ask: same panel as the right rail, center stage. Deep links
 *  (?q=…) run once on arrival. */
export function AskView({ query }: Props) {
  const question = query.get("q");
  const autoRun = question !== null && question.trim().length > 0;

  return (
    <div className={shared.wrap}>
      <div className={shared.card}>
        <h1 className={shared.title}>Ask the archive.</h1>
        <p className={shared.body}>
          One question, one traced path through the graph — the answer keeps
          its steps.
        </p>
        <div className={shared.panel}>
          <AskPanel initialQuestion={question} autoRun={autoRun} />
        </div>
      </div>
    </div>
  );
}
