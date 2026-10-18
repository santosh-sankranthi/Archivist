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

export async function postJSON<T>(
  url: string,
  payload: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        ...headers,
      },
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

/** POST that reads a `text/event-stream` response, frame by frame.
 *
 *  Resolves when the stream ends; every `event:`/`data:` pair is handed to
 *  `onFrame` (comment heartbeats are ignored). HTTP refusals stay ordinary
 *  JSON errors and are raised as `ApiError`, so callers can tell a white
 *  lie refusal from a stream that never opened. */
export async function postSSE(
  url: string,
  body: unknown,
  headers: Record<string, string>,
  onFrame: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
        ...headers,
      },
      body: JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "the console server is unreachable");
  }
  if (!res.ok) {
    throw toError(res.status, await parse(res));
  }
  const contentType = res.headers.get("content-type") ?? "";
  if (!res.body || !contentType.includes("text/event-stream")) {
    throw new ApiError(0, "the server did not open a stream");
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    buffer = buffer.replace(/\r\n/g, "\n");
    let cut = buffer.indexOf("\n\n");
    while (cut !== -1) {
      const frame = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
      }
      if (dataLines.length > 0) {
        let data: Record<string, unknown> = {};
        try {
          const parsed = JSON.parse(dataLines.join("\n"));
          if (parsed && typeof parsed === "object") {
            data = parsed as Record<string, unknown>;
          }
        } catch {
          data = {};
        }
        onFrame(event, data);
      }
      cut = buffer.indexOf("\n\n");
    }
  }
}
