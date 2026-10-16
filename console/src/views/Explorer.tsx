import styles from "./Explorer.module.css";
import shared from "./view.module.css";

interface Props {
  query: URLSearchParams;
}

/** Explorer — queued for the next build tick (step 2 of M4R). The wiring is
 *  already real: left-rail search navigates here with a target. */
export function Explorer({ query }: Props) {
  const file = query.get("file");
  const qualname = query.get("qualname");
  const path = query.get("path");
  const module = query.get("module");

  const queued: Array<[string, string]> = [];
  if (qualname) queued.push(["symbol", file ? `${qualname} — ${file}` : qualname]);
  else if (file) queued.push(["file", file]);
  if (path) queued.push(["file", path]);
  if (module) queued.push(["module", module]);

  return (
    <div className={shared.wrap}>
      <div className={shared.card}>
        <h1 className={shared.title}>The explorer is next.</h1>
        <p className={shared.body}>
          Search in the left rail already queries the live graph, and results
          land here. The full canvas — call neighbourhoods, path finder,
          inspector — arrives in the next build tick.
        </p>
        {queued.length > 0 ? (
          <div className={styles.queued}>
            <span className={styles.queuedLabel}>Queued for the canvas</span>
            {queued.map(([kind, value]) => (
              <div key={`${kind}:${value}`} className={styles.queuedRow}>
                <span className={styles.queuedKind}>{kind}</span>
                <span className={styles.queuedValue}>{value}</span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
