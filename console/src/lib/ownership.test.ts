import { describe, expect, it } from "vitest";

import {
  barWidth,
  mixSegments,
  ownerInitials,
  ownershipPath,
  sharePct,
} from "./ownership";

describe("ownershipPath", () => {
  it("defaults to repo scope with no query", () => {
    expect(ownershipPath()).toBe("/api/graph/ownership");
    expect(ownershipPath({ file: null, symbol: null })).toBe(
      "/api/graph/ownership",
    );
  });

  it("carries a file scope, encoded", () => {
    expect(ownershipPath({ file: "src/flask/app.py" })).toBe(
      "/api/graph/ownership?file=src%2Fflask%2Fapp.py",
    );
  });

  it("carries symbol + file together (file disambiguates the symbol)", () => {
    const url = ownershipPath({ symbol: "url_for", file: "src/flask/helpers.py" });
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("symbol")).toBe("url_for");
    expect(params.get("file")).toBe("src/flask/helpers.py");
  });
});

describe("sharePct", () => {
  it("prints one decimal for every row", () => {
    expect(sharePct(0.633)).toBe("63.3%");
    expect(sharePct(0.11)).toBe("11.0%");
    expect(sharePct(0)).toBe("0.0%");
    expect(sharePct(1)).toBe("100.0%");
  });
});

describe("barWidth", () => {
  it("scales to the widest row (leader = 100)", () => {
    expect(barWidth(0.633, 0.633)).toBe(100);
    expect(barWidth(0.3165, 0.633)).toBe(50);
  });

  it("keeps nonzero shares visible with a small floor", () => {
    expect(barWidth(0.0001, 1)).toBe(2);
  });

  it("is zero when there is nothing to draw", () => {
    expect(barWidth(0, 1)).toBe(0);
    expect(barWidth(0.5, 0)).toBe(0);
  });
});

describe("mixSegments", () => {
  const owners = [
    { name: "Armin", share: 0.408 },
    { name: "David", share: 0.11 },
    { name: "Ron", share: 0.042 },
    { name: "defuz", share: 0.028 },
  ];

  it("tops the list and aggregates the long tail", () => {
    const segments = mixSegments(owners, 3, 107);
    expect(segments).toHaveLength(4);
    const rest = segments[3];
    expect(rest.rest).toBe(true);
    expect(rest.label).toBe("others");
    expect(rest.share).toBe(0.44); // 1 - (0.408 + 0.11 + 0.042)
  });

  it("adds no phantom remainder when coverage is complete", () => {
    const full = [
      { name: "A", share: 0.6 },
      { name: "B", share: 0.4 },
    ];
    const segments = mixSegments(full, 3, 2);
    expect(segments).toHaveLength(2);
    expect(segments.every((segment) => !segment.rest)).toBe(true);
  });

  it("labels a remainder 'unattributed' when no authors are hidden", () => {
    // Shares that do not sum to 1 but everyone is listed: the gap is real
    // (server rounding), and it must not claim a hidden tail that is not
    // there.
    const partial = [
      { name: "A", share: 0.5 },
      { name: "B", share: 0.44 },
    ];
    const segments = mixSegments(partial, 3, 2);
    expect(segments).toHaveLength(3);
    expect(segments[2].label).toBe("unattributed");
    expect(segments[2].share).toBe(0.06);
  });

  it("handles an empty owner list", () => {
    expect(mixSegments([], 3, 0)).toEqual([]);
  });
});

describe("ownerInitials", () => {
  it("takes two initials for multi-word names", () => {
    expect(ownerInitials("Armin Ronacher")).toBe("AR");
    expect(ownerInitials("Markus Unterwaditzer")).toBe("MU");
  });

  it("takes two letters for single-word handles", () => {
    expect(ownerInitials("pgjones")).toBe("PG");
    expect(ownerInitials("dependabot[bot]")).toBe("DE");
  });

  it("never returns empty for odd names", () => {
    expect(ownerInitials("***")).toBe("?");
    expect(ownerInitials("Åsa Öberg")).toBe("ÅÖ");
  });
});
