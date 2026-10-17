import { afterEach, describe, expect, it, vi } from "vitest";

import type { Me } from "./session";

const ME: Me = {
  user: { uid: "ab12cd34", username: "santosh", created_at: 1700000000 },
  anonymous: false,
  signup: "open",
  repos: [{ id: "r1", graph: "user:r1", status: "ready" }],
  demo_graph: "repo:flask",
  graph: "repo:flask",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function freshModule() {
  vi.resetModules();
  return import("./session");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("session requests", () => {
  it("signup posts the credentials then refreshes /api/me", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url === "/api/auth/signup") {
          return jsonResponse({ user: ME.user, repos: [] });
        }
        return jsonResponse(ME);
      }),
    );
    const session = await freshModule();
    const me = await session.signupRequest("santosh", "correct horse battery");

    expect(me.user?.username).toBe("santosh");
    expect(calls.map((call) => call.url)).toEqual(["/api/auth/signup", "/api/me"]);
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      username: "santosh",
      password: "correct horse battery",
    });
    expect(session.getSessionSnapshot()).toEqual({ me: ME, loaded: true });
  });

  it("login posts, logout posts, both refresh the snapshot", async () => {
    const urls: string[] = [];
    let me: Me = { ...ME, user: null, anonymous: true, repos: [] };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        urls.push(url);
        if (url === "/api/auth/login") me = ME;
        if (url === "/api/auth/logout") me = { ...ME, user: null, anonymous: true, repos: [] };
        return jsonResponse(me);
      }),
    );
    const session = await freshModule();
    await session.loginRequest("santosh", "correct horse battery");
    expect(session.getSessionSnapshot().me?.anonymous).toBe(false);

    await session.logoutRequest();
    expect(urls).toEqual(["/api/auth/login", "/api/me", "/api/auth/logout", "/api/me"]);
    expect(session.getSessionSnapshot().me?.anonymous).toBe(true);
  });

  it("keeps the server's honest error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse({ error: "username_taken", message: "that username is taken" }, 409),
      ),
    );
    const session = await freshModule();
    await expect(session.signupRequest("santosh", "correct horse battery")).rejects.toMatchObject({
      status: 409,
      code: "username_taken",
      message: "that username is taken",
    });
  });

  it("deduplicates concurrent refreshes", async () => {
    let resolves = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
        resolves += 1;
        return jsonResponse(ME);
      }),
    );
    const session = await freshModule();
    await Promise.all([session.refreshSession(), session.refreshSession()]);
    expect(resolves).toBe(1);
  });
});
