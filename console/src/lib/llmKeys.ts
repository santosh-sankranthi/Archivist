/** BYO-key settings (M7a): the visitor's own LLM base URL / model / key,
 *  kept in the browser's localStorage and sent per ask — the server never
 *  stores or logs them. One tiny external-store atom so the TopBar chip,
 *  the Ask room, and the settings panel all read the same value. */

import { useSyncExternalStore } from "react";

export interface LLMKeySettings {
  base: string;
  model: string;
  key: string;
}

const KEYS = {
  base: "archivist.llm.base",
  model: "archivist.llm.model",
  key: "archivist.llm.key",
} as const;

const EMPTY: LLMKeySettings = { base: "", model: "", key: "" };

function readOne(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function load(): LLMKeySettings {
  return {
    base: readOne(KEYS.base),
    model: readOne(KEYS.model),
    key: readOne(KEYS.key),
  };
}

let snapshot: LLMKeySettings = load();
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((fn) => fn());
}

export function getLLMKey(): LLMKeySettings {
  return snapshot;
}

export function saveLLMKey(settings: LLMKeySettings): void {
  snapshot = { ...settings };
  try {
    for (const [field, storageKey] of [
      ["base", KEYS.base],
      ["model", KEYS.model],
      ["key", KEYS.key],
    ] as const) {
      const value = snapshot[field].trim();
      if (value) window.localStorage.setItem(storageKey, value);
      else window.localStorage.removeItem(storageKey);
    }
  } catch {
    /* privacy mode: the value still lives for this tab */
  }
  emit();
}

export function clearLLMKey(): void {
  saveLLMKey(EMPTY);
}

export function hasLLMKey(): boolean {
  return Boolean(snapshot.base.trim() || snapshot.model.trim() || snapshot.key.trim());
}

/** Request headers the server reads for `/api/ask` and `/api/llm/test`. */
export function llmHeadersFor(settings: LLMKeySettings): Record<string, string> {
  const headers: Record<string, string> = {};
  if (settings.base.trim()) headers["X-Archivist-LLM-Base"] = settings.base.trim();
  if (settings.model.trim()) headers["X-Archivist-LLM-Model"] = settings.model.trim();
  if (settings.key.trim()) headers["X-Archivist-LLM-Key"] = settings.key.trim();
  return headers;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function useLLMKey(): LLMKeySettings {
  return useSyncExternalStore(subscribe, getLLMKey);
}
