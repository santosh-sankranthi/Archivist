/** Orb — Archivist's character: a living gradient sphere with a soft aura
 *  (Meta-AI-inspired presence, in the console's own dark system). Pure CSS:
 *  a slowly turning conic core, an inner highlight, and a breathing halo.
 *  `thinking` speeds the spin and swells the aura while the agent works. */

import styles from "./Orb.module.css";

interface Props {
  size?: "sm" | "md" | "lg";
  thinking?: boolean;
  className?: string;
}

export function Orb({ size = "sm", thinking = false, className }: Props) {
  const classes = [styles.orb, styles[size], thinking ? styles.thinking : "", className ?? ""]
    .join(" ")
    .trim();
  return (
    <span className={classes} aria-hidden="true">
      <span className={styles.core} />
      <span className={styles.sheen} />
    </span>
  );
}
