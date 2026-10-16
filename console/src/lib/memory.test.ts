import { describe, expect, it } from "vitest";

import {
  chainLinks,
  episodeChips,
  factSpan,
  groupEpisodes,
  memoryDomain,
  memoryIndex,
  MIN_WIDTH_PCT,
  pct,
  widenDomain,
  windowInstants,
} from "./memory";
import type {
  MemoryChainFact,
  MemoryEpisode,
  MemoryFact,
  MemoryResponse,
} from "./types";

function fact(over: Partial<MemoryFact>): MemoryFact {
  return {
    id: "fact:1",
    kind: "note",
    text: "the truth",
    valid_at: null,
    invalid_at: null,
    created_at: null,
    expired_at: null,
    episode_id: null,
    supersedes: [],
    superseded_by: [],
    refs: [],
    ...over,
  };
}

function episode(over: Partial<MemoryEpisode>): MemoryEpisode {
  return {
    id: "ep:1",
    kind: "agent-session",
    source: "webapp",
    ts: 1000,
    created_at: 1000,
    question: "q?",
    answer: "a.",
    role: null,
    run: null,
    props: {},
    handoffs: [],
    handoff_in: [],
    ...over,
  };
}

function response(over: Partial<MemoryResponse>): MemoryResponse {
  return {
    graph: "repo:fixture",
    mode: "now",
    as_of: null,
    range: { earliest: 1000, latest: 2000 },
    counts: { episodes: 0, facts: 0, open_facts: 0, handoff_edges: 0, supersede_edges: 0 },
    episodes: [],
    facts: [],
    chain_facts: [],
    notes: [],
    ...over,
  };
}

describe("memoryDomain", () => {
  it("unions the range with every fact window", () => {
    const domain = memoryDomain(
      response({ facts: [fact({ valid_at: 500, invalid_at: 1500 })] }),
    );
    expect(domain).toEqual({ t0: 500, t1: 2000 });
  });

  it("extends to `now` while any shown fact is open", () => {
    const domain = memoryDomain(
      response({ facts: [fact({ valid_at: 1000, invalid_at: null })] }),
      9000,
    );
    expect(domain.t1).toBe(9000);
  });

  it("does not extend for closed windows", () => {
    const domain = memoryDomain(
      response({ facts: [fact({ valid_at: 1000, invalid_at: 1800 })] }),
      9000,
    );
    expect(domain.t1).toBe(2000);
  });

  it("widens with chain-neighbour windows", () => {
    const chain: MemoryChainFact = {
      id: "fact:old",
      text: "old",
      kind: null,
      valid_at: 400,
      invalid_at: 900,
    };
    const domain = memoryDomain(response({ chain_facts: [chain] }));
    expect(domain.t0).toBe(400);
  });

  it("keeps a positive span for degenerate input", () => {
    const domain = memoryDomain(response({ range: { earliest: null, latest: null } }));
    expect(domain.t1).toBeGreaterThan(domain.t0);
  });
});

describe("pct", () => {
  it("maps instants to clamped percentages", () => {
    const domain = { t0: 100, t1: 200 };
    expect(pct(100, domain)).toBe(0);
    expect(pct(150, domain)).toBe(50);
    expect(pct(999, domain)).toBe(100);
    expect(pct(0, domain)).toBe(0);
  });
});

describe("factSpan", () => {
  it("runs open facts to the right edge", () => {
    const domain = { t0: 0, t1: 100 };
    const span = factSpan(fact({ valid_at: 50, invalid_at: null }), domain);
    expect(span).toEqual({ startPct: 50, endPct: 100, open: true });
  });

  it("takes the display floor for instant windows", () => {
    const domain = { t0: 0, t1: 100 };
    const span = factSpan(fact({ valid_at: 40, invalid_at: 40 }), domain);
    expect(span.endPct - span.startPct).toBeCloseTo(MIN_WIDTH_PCT, 5);
    expect(span.open).toBe(false);
  });

  it("clamps closed windows at the domain edge", () => {
    const domain = { t0: 0, t1: 100 };
    const span = factSpan(fact({ valid_at: 95, invalid_at: 100 }), domain);
    expect(span.endPct).toBeLessThanOrEqual(100);
  });
});

describe("widenDomain", () => {
  it("unions two domains", () => {
    expect(widenDomain({ t0: 10, t1: 20 }, { t0: 0, t1: 15 })).toEqual({ t0: 0, t1: 20 });
  });

  it("passes through single-sided values", () => {
    expect(widenDomain(null, { t0: 1, t1: 2 })).toEqual({ t0: 1, t1: 2 });
    expect(widenDomain({ t0: 1, t1: 2 }, null)).toEqual({ t0: 1, t1: 2 });
    expect(widenDomain(null, null)).toBeNull();
  });
});

describe("memoryIndex + chainLinks", () => {
  const hidden: MemoryChainFact = {
    id: "fact:old",
    text: "older claim",
    kind: "reviewer",
    valid_at: 100,
    invalid_at: 200,
  };

  it("lets shown facts override chain placeholders", () => {
    const index = memoryIndex(
      response({ facts: [fact({ id: "fact:old", text: "full row" })], chain_facts: [hidden] }),
    );
    expect(index.get("fact:old")?.text).toBe("full row");
  });

  it("resolves a hidden older claim by name", () => {
    const current = fact({ supersedes: ["fact:old"] });
    const links = chainLinks(current, memoryIndex(response({ chain_facts: [hidden] })));
    expect(links).toEqual([
      {
        id: "fact:old",
        text: "older claim",
        validAt: 100,
        invalidAt: 200,
        side: "older",
      },
    ]);
  });

  it("orders links chronological, older before newer", () => {
    const current = fact({ supersedes: ["fact:old"], superseded_by: ["fact:new"] });
    const index = memoryIndex(
      response({
        chain_facts: [hidden, { ...hidden, id: "fact:new", valid_at: 300, invalid_at: null }],
      }),
    );
    const links = chainLinks(current, index);
    expect(links.map((l) => l.side)).toEqual(["older", "newer"]);
  });

  it("falls back to the id when a neighbour is unknown", () => {
    const links = chainLinks(fact({ superseded_by: ["ghost"] }), new Map());
    expect(links[0]).toMatchObject({ id: "ghost", text: "ghost", validAt: null });
  });
});

describe("windowInstants", () => {
  it("prefers valid_at and keeps nulls intact", () => {
    expect(windowInstants(fact({ valid_at: 10, invalid_at: null }))).toEqual({
      from: 10,
      to: null,
    });
    expect(windowInstants(fact({ created_at: 20 }))).toEqual({ from: 20, to: null });
  });
});

describe("groupEpisodes + episodeChips", () => {
  it("groups contiguous newest-first runs by day", () => {
    const dayA = Date.UTC(2026, 9, 16, 21, 0) / 1000;
    const dayB = dayA - 86400;
    const groups = groupEpisodes([
      episode({ id: "a", ts: dayA }),
      episode({ id: "b", ts: dayA - 60 }),
      episode({ id: "c", ts: dayB }),
    ]);
    expect(groups.map((g) => g.day)).toEqual(["Oct 16", "Oct 15"]);
    expect(groups[0].episodes.map((e) => e.id)).toEqual(["a", "b"]);
  });

  it("keeps the same day contiguous if interleaved (server order is authority)", () => {
    const t = Date.UTC(2026, 9, 16, 12, 0) / 1000;
    const groups = groupEpisodes([
      episode({ id: "a", ts: t }),
      episode({ id: "b", ts: t - 86400 }),
      episode({ id: "c", ts: t - 3600 }),
    ]);
    expect(groups.map((g) => g.day)).toEqual(["Oct 16", "Oct 15", "Oct 16"]);
  });

  it("builds mono chips in render order, skipping non-scalars", () => {
    const chips = episodeChips(
      episode({
        props: {
          model: "deepseek-v4-pro",
          steps: 2,
          queries: 4,
          llm_calls: 3,
          partial: false,
          head: "d086db85",
        },
      }),
    );
    expect(chips).toEqual([
      { key: "model", value: "deepseek-v4-pro" },
      { key: "steps", value: "2" },
      { key: "queries", value: "4" },
      { key: "llm calls", value: "3" },
    ]);
  });

  it("flags partial runs", () => {
    const chips = episodeChips(episode({ props: { partial: true } }));
    expect(chips).toEqual([{ key: "partial", value: "yes" }]);
  });
});
