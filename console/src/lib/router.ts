/** Tiny history router for the /console base. One location atom, no deps.
 *  The server serves index.html for unknown sub-paths (SPA fallback). */

import { useSyncExternalStore } from "react";

const BASE = "/console";

const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((fn) => fn());
}

function snapshot(): string {
  return window.location.pathname + window.location.search;
}

export function navigate(to: string): void {
  window.history.pushState(null, "", to);
  emit();
  window.scrollTo(0, 0);
}

export function href(path: string, params?: Record<string, string>): string {
  const qs = params && Object.keys(params).length ? `?${new URLSearchParams(params)}` : "";
  return `${BASE}${path}${qs}`;
}

export interface Location {
  /** Route path with the /console base stripped: "/", "/explorer", "/ask". */
  path: string;
  query: URLSearchParams;
}

export function useLocation(): Location {
  const raw = useSyncExternalStore(
    (fn) => {
      listeners.add(fn);
      window.addEventListener("popstate", fn);
      return () => {
        listeners.delete(fn);
        window.removeEventListener("popstate", fn);
      };
    },
    snapshot,
    () => `${BASE}/`,
  );

  const qIndex = raw.indexOf("?");
  const pathname = qIndex === -1 ? raw : raw.slice(0, qIndex);
  const search = qIndex === -1 ? "" : raw.slice(qIndex + 1);
  const rest = pathname.startsWith(BASE) ? pathname.slice(BASE.length) : pathname;
  return {
    path: rest === "" ? "/" : rest,
    query: new URLSearchParams(search),
  };
}
