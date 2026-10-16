import { AskRoom } from "./AskRoom";
import styles from "./RightRail.module.css";

interface Props {
  open: boolean;
  onToggle: () => void;
}

/** Right rail: collapsible Ask panel. Collapsed it is a thin spine with a
 *  vertical label — the canvas keeps the width. */
export function RightRail({ open, onToggle }: Props) {
  return (
    <aside className={`${styles.rail} ${open ? styles.open : styles.spine}`}>
      <button
        type="button"
        className={styles.toggle}
        onClick={onToggle}
        aria-expanded={open}
        aria-label={open ? "Collapse the Ask panel" : "Expand the Ask panel"}
        title={open ? "Collapse" : "Ask the archive"}
      >
        {open ? "›" : "‹"}
      </button>
      {open ? (
        <div className={styles.body}>
          <div className={styles.head}>
            <span className={styles.label}>Ask + Trace</span>
            <span className={styles.sub}>answers keep their path</span>
          </div>
          <AskRoom variant="panel" />
        </div>
      ) : (
        <span className={styles.vert} aria-hidden="true">
          ASK
        </span>
      )}
    </aside>
  );
}
