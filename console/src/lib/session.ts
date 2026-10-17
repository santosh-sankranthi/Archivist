/** Session atom: one /api/me snapshot shared by the top bar and the auth
 *  pages, same tiny-store pattern as the router (no context provider needed
 *  for a value that every route either ignores or reads once). Mutations
 *  refresh the snapshot so every chip updates without a reload. */

import { useEffect, useSyncExternalStore } from "react";

import { getJSON, postJSON } from "./api";

export interface MeUser {
  uid: string;
  username: string;
  created_at: number;
}

export interface MeRepo {
  id: string;
  name?: string;
  slug?: string;
  graph: string;
  status?: string;
  [key: string]: unknown;
}

export interface Me {
  user: MeUser | null;
  anonymous: boolean;
  signup: "open" | "closed";
  repos: MeRepo[];
  demo_graph: string;
  graph: string;
}

export interface SessionSnapshot {
  me: Me | null;
  /** True once /api/me has answered; a null `me` before that is "unknown". */
  loaded: boolean;
}

let snapshot: SessionSnapshot = { me: null, loaded: false };
let inflight: Promise<Me | null> | null = null;
const listeners = new Set<() => void>();

function setSnapshot(next: SessionSnapshot): void {
  snapshot = next;
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Fetch /api/me (deduplicated) and broadcast the result. */
export function refreshSession(): Promise<Me | null> {
  inflight ??= getJSON<Me>("/api/me")
    .then((me) => {
      setSnapshot({ me, loaded: true });
      return me;
    })
    .catch((error) => {
      setSnapshot({ me: null, loaded: false });
      throw error;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Sign up, then pick up the cookie session (the server sets it on success). */
export async function signupRequest(username: string, password: string): Promise<Me> {
  await postJSON<{ user: MeUser }>("/api/auth/signup", { username, password });
  const me = await refreshSession();
  if (!me) throw new Error("session did not refresh after signup");
  return me;
}

export async function loginRequest(username: string, password: string): Promise<Me> {
  await postJSON<{ user: MeUser }>("/api/auth/login", { username, password });
  const me = await refreshSession();
  if (!me) throw new Error("session did not refresh after login");
  return me;
}

export async function logoutRequest(): Promise<void> {
  await postJSON("/api/auth/logout", {});
  await refreshSession();
}

export function getSessionSnapshot(): SessionSnapshot {
  return snapshot;
}

/** Read the session; the first mount triggers the (deduplicated) fetch. */
export function useSession(): SessionSnapshot {
  const value = useSyncExternalStore(subscribe, getSessionSnapshot);
  useEffect(() => {
    void refreshSession().catch(() => {
      /* the chips stay quiet; pages show their own error when they need one */
    });
  }, []);
  return value;
}
