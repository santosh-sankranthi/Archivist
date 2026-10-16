/** Motion system vocabulary (DESIGN-BRIEF §P2.2) — one set of curves and
 *  durations for the whole console, so route changes, rails, and canvases
 *  feel like one system. Framer Motion (`motion`) reads these; cytoscape
 *  side helpers check `prefersReducedMotion()` for parity. */

import type { Transition } from "motion/react";

/** easeOutQuint (matches --ease-quint in tokens.css) — entrances, rises. */
export const EASE_OUT: [number, number, number, number] = [0.23, 1, 0.32, 1];

/** Route swap: rise + fade (P2.2: ~180ms). Transform/opacity only. */
export const ROUTE_TRANSITION: Transition = { duration: 0.18, ease: EASE_OUT };

/** Nav indicator / shared-layout motion: a dense, non-wobbly spring. */
export const NAV_SPRING: Transition = {
  type: "spring",
  stiffness: 480,
  damping: 38,
  mass: 0.9,
};

/** True when the user prefers reduced motion (SSR-safe). */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}
