import { describe, expect, it } from "vitest";

import {
  barHeightPct,
  bucketTitle,
  clampRange,
  deltaLabel,
  historyPath,
  selectionLabel,
  skylineMax,
  windowFromSelection,
  yearTicks,
} from "./history";
import type { HistoryBucket } from "./types";

function bucket(over: Partial<HistoryBucket>): HistoryBucket {
  return {
    label: "2010-04",
    t0: 100,
    t1: 200,
    commits: 10,
    touches: 5,
    added: 100,
    removed: 50,
    ...over,
  };
}

describe("skylineMax", () => {
  it("returns the largest commit count", () => {
    expect(skylineMax([bucket({ commits: 3 }), bucket({ commits: 169 })])).toBe(169);
  });

  it("never returns less than 1 (empty skyline stays safe)", () => {
    expect(skylineMax([])).toBe(1);
  });
});

describe("barHeightPct", () => {
  it("keeps zero months at zero", () => {
    expect(barHeightPct(0, 100)).toBe(0);
  });

  it("scales the peak to the full height", () => {
    expect(barHeightPct(169, 169)).toBe(100);
  });

  it("is monotonic and floors tiny months above zero", () => {
    const small = barHeightPct(1, 169);
    const mid = barHeightPct(25, 169);
    expect(small).toBeGreaterThan(0);
    expect(small).toBeLessThan(mid);
    expect(mid).toBeLessThan(100);
  });
});

describe("clampRange", () => {
  it("orders either drag direction", () => {
    expect(clampRange(7, 2)).toEqual({ a: 2, b: 7 });
    expect(clampRange(2, 7)).toEqual({ a: 2, b: 7 });
  });
});

describe("windowFromSelection", () => {
  const buckets = [
    bucket({ label: "2010-04", t0: 100, t1: 200 }),
    bucket({ label: "2010-05", t0: 200, t1: 300 }),
    bucket({ label: "2010-06", t0: 300, t1: 400 }),
  ];

  it("tiles exactly across a multi-month run", () => {
    expect(windowFromSelection(buckets, 0, 1)).toEqual({ from: 100, to: 300 });
    expect(windowFromSelection(buckets, 1, 0)).toEqual({ from: 100, to: 300 });
  });

  it("a single month is that month's own bounds", () => {
    expect(windowFromSelection(buckets, 2, 2)).toEqual({ from: 300, to: 400 });
  });

  it("returns null for an empty skyline or out-of-range indices", () => {
    expect(windowFromSelection([], 0, 0)).toBeNull();
    expect(windowFromSelection(buckets, 0, 9)).toBeNull();
  });
});

describe("selectionLabel", () => {
  const buckets = [
    bucket({ label: "2010-04", commits: 169 }),
    bucket({ label: "2010-05", commits: 12 }),
    bucket({ label: "2010-06", commits: 0 }),
  ];

  it("names the run, counts months, sums commits", () => {
    expect(selectionLabel(buckets, 0, 2)).toBe(
      "2010-04 → 2010-06 · 3 months · 181 commits",
    );
  });

  it("keeps singular for a one-month window", () => {
    expect(selectionLabel(buckets, 1, 1)).toBe("2010-05 → 2010-05 · 1 month · 12 commits");
  });
});

describe("bucketTitle", () => {
  it("carries every exact number", () => {
    expect(bucketTitle(bucket({ commits: 169, touches: 42, added: 1327, removed: 294 }))).toBe(
      "2010-04 · 169 commits · 42 files touched · +1,327 / −294",
    );
  });
});

describe("yearTicks", () => {
  it("marks the first bucket of each calendar year", () => {
    const ticks = yearTicks([
      bucket({ label: "2010-04" }),
      bucket({ label: "2010-05" }),
      bucket({ label: "2011-01" }),
      bucket({ label: "2011-02" }),
    ]);
    expect(ticks).toEqual([
      { index: 0, label: "2010" },
      { index: 2, label: "2011" },
    ]);
  });

  it("is empty for an empty skyline", () => {
    expect(yearTicks([])).toEqual([]);
  });
});

describe("historyPath", () => {
  it("builds the bare endpoint without params", () => {
    expect(historyPath(null)).toBe("/api/graph/history");
  });

  it("encodes a file scope and adds a window when asked", () => {
    expect(historyPath("src/flask/app.py")).toBe(
      "/api/graph/history?file=src%2Fflask%2Fapp.py",
    );
    expect(historyPath(null, { from: 1, to: 2 })).toBe(
      "/api/graph/history?from=1&to=2",
    );
    expect(historyPath("a b", { from: 10, to: 20 })).toBe(
      "/api/graph/history?file=a+b&from=10&to=20",
    );
  });
});

describe("deltaLabel", () => {
  it("formats additions and deletions", () => {
    expect(deltaLabel(1327, 294)).toBe("+1,327 / −294");
    expect(deltaLabel(0, 0)).toBe("+0 / −0");
  });
});
