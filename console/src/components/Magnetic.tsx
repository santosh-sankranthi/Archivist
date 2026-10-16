/** Magnetic primaries (P2.2): the control leans a few px toward the
 *  cursor, springing back on leave. Transform-only, capped, and fully
 *  inert under reduced motion (the child renders identically, just still). */

import { motion, useMotionValue, useReducedMotion, useSpring } from "motion/react";
import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";

import styles from "./Magnetic.module.css";

interface Props {
  children: ReactNode;
  /** Maximum lean in px toward the cursor. Keep it small: 4–8. */
  strength?: number;
  className?: string;
}

export function Magnetic({ children, strength = 6, className }: Props) {
  const reduce = useReducedMotion();
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const x = useSpring(mx, { stiffness: 300, damping: 26, mass: 0.5 });
  const y = useSpring(my, { stiffness: 300, damping: 26, mass: 0.5 });

  function onMove(event: ReactPointerEvent<HTMLDivElement>): void {
    if (reduce) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const dx = (event.clientX - (rect.left + rect.width / 2)) * 0.18;
    const dy = (event.clientY - (rect.top + rect.height / 2)) * 0.18;
    const clamp = (value: number) =>
      Math.max(-strength, Math.min(strength, value));
    mx.set(clamp(dx));
    my.set(clamp(dy));
  }

  function onLeave(): void {
    mx.set(0);
    my.set(0);
  }

  return (
    <motion.div
      className={className ? `${styles.magnet} ${className}` : styles.magnet}
      style={{ x, y }}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
    >
      {children}
    </motion.div>
  );
}
