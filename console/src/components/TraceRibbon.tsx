/** Trace Ribbon — the signature move: an answer's steps as ordered chips
 *  (tool → key fact → ms) joined by a 1px luminous line. Clicking a chip
 *  focuses that step: the canvas replays its subgraph and the exact Cypher
 *  is one tap away (both live in AskRoom). Chips animate in with a 24ms
 *  stagger (capped at 8); reduced motion keeps full parity. */

import type { CSSProperties } from "react";

import { stepFact, toolLabel } from "../lib/trace";
import type { AskStep } from "../lib/types";
import styles from "./TraceRibbon.module.css";

interface Props {
  steps: AskStep[];
  active: number | null;
  onPick: (index: number) => void;
}

export function TraceRibbon({ steps, active, onPick }: Props) {
  if (steps.length === 0) return null;
  return (
    <ol className={styles.ribbon} aria-label="Answer trace">
      {steps.map((step, index) => {
        const bad = step.status === "error";
        const isActive = index === active;
        return (
          <li
            key={`${step.round}-${index}`}
            className={styles.item}
            style={{ "--i": Math.min(index, 8) } as CSSProperties}
          >
            {index > 0 ? <span className={styles.link} aria-hidden="true" /> : null}
            <button
              type="button"
              className={`${styles.chip} ${isActive ? styles.active : ""} ${
                bad ? styles.bad : ""
              }`}
              onClick={() => onPick(index)}
              aria-pressed={isActive}
              title={`${toolLabel(step.tool)} · ${stepFact(step)} · ${step.ms} ms`}
              data-step={index}
            >
              <span className={styles.tool}>{toolLabel(step.tool)}</span>
              <span className={styles.fact}>{stepFact(step)}</span>
              <span className={styles.ms}>{step.ms} ms</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
