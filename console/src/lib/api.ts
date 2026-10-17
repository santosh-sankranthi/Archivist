/** Fetch helpers + a tiny data hook. Read-only JSON in, typed values out;
 *  failures keep their honest server message and status. */

import { useEffect, useState } from "react";

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

async function parse(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function toError(status: number, body: unknown): ApiError {
  const record =
    body && typeof body === "object" ? (body as Record<string, unknown>) : null;
  const message =
    record && typeof record.message === "string"
      ? record.message
      : `request failed (HTTP ${status})`;
  const code = record && typeof record.error === "string" ? record.error : undefined;
  return new ApiError(status, message, code);
}

export async function getJSON<T>(url: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { headers: { accept: "application/json" } });
  } catch {
    throw new ApiError(0, "the console server is unreachable");
  }
  const body = await parse(res);
  if (!res.ok) throw toError(res.status, body);
  return body as T;
}

export async function postJSON<T>(url: string, payload: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new ApiError(0, "the console server is unreachable");
  }
  const body = await parse(res);
  if (!res.ok) throw toError(res.status, body);
  return body as T;
}

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  /** Re-fetch on demand (retry affordance); no-op while the path is null. */
  reload: () => void;
}

/** Fetch `path` on change; `null` path stays idle. `refreshMs` re-polls
 *  (used for the live counter chip), pausing while the tab is hidden. */
export function useApi<T>(path: string | null, refreshMs?: number): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    error: null,
    loading: path !== null,
    reload: () => {},
  });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (path === null) {
      setState((prev) => ({ ...prev, data: null, error: null, loading: false }));
      return;
    }
    let alive = true;
    const load = () => {
      getJSON<T>(path)
        .then((data) => {
          if (alive) setState((prev) => ({ ...prev, data, error: null, loading: false }));
        })
        .catch((error: ApiError) => {
          if (alive) setState((prev) => ({ ...prev, data: null, error, loading: false }));
        });
    };
    setState((prev) => ({ ...prev, loading: prev.data === null, error: null }));
    load();
    if (!refreshMs) return () => void (alive = false);
    const timer = window.setInterval(() => {
      if (!document.hidden) load();
    }, refreshMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [path, refreshMs, nonce]);

  const reload = () => setNonce((value) => value + 1);
  return { ...state, reload };
}
