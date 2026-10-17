import { describe, expect, it } from "vitest";

import {
  chainLabel,
  chainTitle,
  fileTail,
  hopLabel,
  impactPath,
  parseDepth,
  ringStrength,
  rollupFiles,
  shortName,
  visibleRing,
} from "./impact";
import type { ImpactFile, ImpactSymbol } from "./types";

function sym(hops: number, over: Partial<ImpactSymbol> = {}): ImpactSymbol {
  return {
    file: "src/flask/helpers.py",
    qualname: `flask.helpers.caller_${hops}`,
    name: `caller_${hops}`,
    kind: "function",
    chain: [
      { file: "src/flask/helpers.py", qualname: `flask.helpers.caller_${hops}` },
      { file: "src/flask/helpers.py", qualname: "flask.helpers.target" },
    ],
    counts: [3, 1],
    weak: false,
    ...over,
  };
}

function file(path: string, symbols: number, minHops: number, tests = false): ImpactFile {
  return { path, symbols, min_hops: minHops, tests };
}

describe("impactPath", () => {
  it("always carries the symbol", () => {
    expect(impactPath({ symbol: "url_for" })).toBe(
      "/api/graph/impact?symbol=url_for",
    );
  });

  it("encodes scope, depth, weak flag, and limit", () => {
    const url = impactPath({
      symbol: "flask.helpers.url_for",
      file: "src/flask/helpers.py",
      depth: 3,
      includeWeak: true,
      limit: 40,
    });
    const params = new URLSearchParams(url.split("?")[1]);
    expect(params.get("symbol")).toBe("flask.helpers.url_for");
    expect(params.get("file")).toBe("src/flask/helpers.py");
    expect(params.get("depth")).toBe("3");
    expect(params.get("include_weak")).toBe("true");
    expect(params.get("limit")).toBe("40");
  });

  it("omits the weak flag when it is off (server default is off too)", () => {
    const url = impactPath({ symbol: "x", includeWeak: false });
    expect(url).not.toContain("include_weak");
  });
});

describe("parseDepth", () => {
  it("accepts the server's 1..6 range", () => {
    expect(parseDepth("1")).toBe(1);
    expect(parseDepth("6")).toBe(6);
  });

  it("falls back to 4 for missing, fractional, or out-of-range input", () => {
    expect(parseDepth(null)).toBe(4);
    expect(parseDepth("0")).toBe(4);
    expect(parseDepth("7")).toBe(4);
    expect(parseDepth("2.5")).toBe(4);
    expect(parseDepth("deep")).toBe(4);
  });
});

describe("chainLabel", () => {
  it("prints the chain in call order, caller first", () => {
    expect(
      chainLabel([
        { file: "a.py", qualname: "pkg.outer" },
        { file: "b.py", qualname: "pkg.middle" },
        { file: "c.py", qualname: "pkg.inner" },
      ]),
    ).toBe("outer → middle → inner");
  });

  it("keeps file qualification in the tooltip so shared names stay distinct", () => {
    expect(
      chainTitle([
        { file: "a.py", qualname: "pkg.outer" },
        { file: "c.py", qualname: "pkg.inner" },
      ]),
    ).toBe("pkg.outer (a.py) → pkg.inner (c.py)");
  });

  it("shortens a single qualified name to its terminal segment", () => {
    expect(shortName("flask.helpers.url_for")).toBe("url_for");
    expect(shortName("url_for")).toBe("url_for");
  });
});

describe("fileTail", () => {
  it("keeps the last two segments for place", () => {
    expect(fileTail("src/flask/helpers.py")).toBe("flask/helpers.py");
    expect(fileTail("app.py")).toBe("app.py");
  });
});

describe("ringStrength", () => {
  it("is full strength at the direct ring", () => {
    expect(ringStrength(1, 4)).toBe(1);
  });

  it("fades with distance and is monotonic", () => {
    expect(ringStrength(2, 4)).toBeLessThan(1);
    expect(ringStrength(3, 4)).toBeLessThan(ringStrength(2, 4));
    expect(ringStrength(4, 4)).toBeGreaterThan(0);
  });

  it("stays 1 when the only ring is the first", () => {
    expect(ringStrength(1, 1)).toBe(1);
  });
});

describe("rollupFiles", () => {
  it("splits source and test files and sums their symbols", () => {
    const rollup = rollupFiles([
      file("src/flask/app.py", 4, 1),
      file("tests/test_app.py", 2, 2, true),
      file("src/flask/helpers.py", 3, 1),
    ]);
    expect(rollup.source.map((f) => f.path)).toEqual([
      "src/flask/app.py",
      "src/flask/helpers.py",
    ]);
    expect(rollup.tests.map((f) => f.path)).toEqual(["tests/test_app.py"]);
    expect(rollup.sourceSymbols).toBe(7);
    expect(rollup.testSymbols).toBe(2);
  });

  it("handles a cone with no tests", () => {
    const rollup = rollupFiles([file("src/flask/app.py", 4, 1)]);
    expect(rollup.tests).toEqual([]);
    expect(rollup.testSymbols).toBe(0);
  });
});

describe("visibleRing", () => {
  it("shows the display cap and counts the hidden rows", () => {
    const symbols = Array.from({ length: 14 }, (_, i) => sym(i + 1));
    const { shown, hidden } = visibleRing(symbols, false);
    expect(shown).toHaveLength(9);
    expect(hidden).toBe(5);
  });

  it("shows everything when expanded", () => {
    const symbols = Array.from({ length: 14 }, (_, i) => sym(i + 1));
    const { shown, hidden } = visibleRing(symbols, true);
    expect(shown).toHaveLength(14);
    expect(hidden).toBe(0);
  });

  it("small rings never report hidden rows", () => {
    const { shown, hidden } = visibleRing([sym(1), sym(2)], false);
    expect(shown).toHaveLength(2);
    expect(hidden).toBe(0);
  });
});

describe("hopLabel", () => {
  it("singularizes one hop", () => {
    expect(hopLabel(1)).toBe("1 hop");
    expect(hopLabel(4)).toBe("4 hops");
  });
});
