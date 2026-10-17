import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import { useApi } from "../lib/api";
import { useDebounced } from "../lib/hooks";
import { EASE_OUT, PALETTE_SPRING } from "../lib/motion";
import {
  buildPaletteItems,
  groupPaletteItems,
  type PaletteItem,
} from "../lib/palette";
import { navigate } from "../lib/router";
import type { FindResult } from "../lib/types";
import styles from "./CommandPalette.module.css";

interface Props {
  open: boolean;
  graph: string | null;
  onClose: () => void;
}

/** ⌘K command palette — navigation actions, live symbol/file search, and
 *  the pinned asks, one keystroke from anywhere in the shell. Search hits
 *  are guarded against staleness: rows only render when the find response
 *  echoes the current query, so a fast typist never sees yesterday's
 *  results masquerading as theirs. */
export function CommandPalette({ open, graph, onClose }: Props) {
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  const debounced = useDebounced(q.trim(), 160);
  const find = useApi<FindResult>(
    debounced.length >= 2
      ? `/api/graph/find?q=${encodeURIComponent(debounced)}&limit=8`
      : null,
  );

  const freshFind =
    find.data && find.data.query.trim().toLowerCase() === debounced.toLowerCase()
      ? find.data
      : null;
  const items = useMemo(() => buildPaletteItems(q.trim(), freshFind), [q, freshFind]);
  const groups = useMemo(() => groupPaletteItems(items), [items]);
  const activeIndex = Math.min(active, Math.max(0, items.length - 1));
  // No graph matches: the search floor has been crossed, the response has
  // landed for the current query, and it returned no symbols or files. In
  // that case the only "ask" row is the free-text fallback, so we say so
  // instead of implying an empty archive.
  const noMatches =
    q.trim().length >= 2 &&
    find.data != null &&
    !find.loading &&
    find.data.query.trim().toLowerCase() === debounced.toLowerCase() &&
    find.data.symbols.length === 0 &&
    find.data.files.length === 0;

  // Fresh open: empty query, top selection, focus the input; remember who
  // had focus so Escape returns it where it was.
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    setQ("");
    setActive(0);
    inputRef.current?.focus();
    return () => restoreRef.current?.focus?.();
  }, [open]);

  // Typing re-ranks from the top (standard palette behaviour).
  useEffect(() => {
    setActive(0);
  }, [debounced]);

  // Keep the selected row visible while arrowing through a long list.
  useEffect(() => {
    if (!open) return;
    inputRef.current
      ?.closest(`.${styles.panel}`)
      ?.querySelector(`[data-idx="${activeIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  // Escape also works when focus has drifted off the input.
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  function select(item: PaletteItem | undefined): void {
    if (!item) return;
    navigate(item.to);
    onClose();
  }

  function onInputKey(event: ReactKeyboardEvent<HTMLInputElement>): void {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!items.length) return;
      const delta = event.key === "ArrowDown" ? 1 : -1;
      setActive((activeIndex + delta + items.length) % items.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      select(items[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          className={styles.overlay}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.12, ease: EASE_OUT } }}
          transition={{ duration: 0.16, ease: EASE_OUT }}
          onClick={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            className={styles.panel}
            initial={{ opacity: 0, y: -10, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{
              opacity: 0,
              y: -6,
              scale: 0.99,
              transition: { duration: 0.12, ease: EASE_OUT },
            }}
            transition={PALETTE_SPRING}
          >
            <div className={styles.inputRow}>
              <span className={styles.lead} aria-hidden="true">
                &gt;
              </span>
              <input
                ref={inputRef}
                className={styles.input}
                type="text"
                role="combobox"
                aria-expanded="true"
                aria-autocomplete="list"
                aria-controls="palette-list"
                aria-activedescendant={items.length ? `palette-opt-${activeIndex}` : undefined}
                aria-label="Search symbols, files, and actions"
                placeholder="Search symbols, files, actions — or type a question…"
                autoComplete="off"
                spellCheck={false}
                value={q}
                onChange={(event) => setQ(event.target.value)}
                onKeyDown={onInputKey}
              />
              <kbd className={styles.kbd}>esc</kbd>
            </div>

            <div className={styles.list} id="palette-list" role="listbox" aria-label="Results">
              {noMatches ? (
                <p className={styles.noMatches}>
                  No symbol or file matches — press Enter to ask the archive instead.
                </p>
              ) : null}
              {groups.map((group) => (
                <div key={`${group.kind}:${group.start}`} className={styles.group}>
                  <p className={styles.groupLabel}>{group.label}</p>
                  {group.items.map((item, index) => {
                    const idx = group.start + index;
                    return (
                      <button
                        key={item.id}
                        id={`palette-opt-${idx}`}
                        type="button"
                        role="option"
                        aria-selected={idx === activeIndex}
                        data-idx={idx}
                        className={styles.option}
                        onMouseMove={() => {
                          if (idx !== activeIndex) setActive(idx);
                        }}
                        onClick={() => select(item)}
                      >
                        <span className={styles.optLabel}>{item.label}</span>
                        <span className={styles.optHint}>{item.hint}</span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>

            <div className={styles.foot}>
              <span>↑↓ move · ↵ open · esc close</span>
              <span>{graph ? `graph · ${graph}` : "graph search"}</span>
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
