import { describe, expect, it } from "vitest";

import { buildPaletteItems, groupPaletteItems, tokensMatch } from "./palette";
import { STARTERS } from "./questions";
import type { FindResult } from "./types";

const FIND: FindResult = {
  query: "url",
  symbols: [
    {
      file: "src/flask/helpers.py",
      qualname: "flask.helpers.url_for",
      name: "url_for",
      kind: "function",
      lineno: 200,
    },
  ],
  files: [{ path: "src/flask/helpers.py", loc: 900 }],
  notes: [],
};

describe("tokensMatch", () => {
  it("matches on every token, case-insensitively", () => {
    expect(tokensMatch("Go to Explorer", "expl")).toBe(true);
    expect(tokensMatch("Go to Explorer", "go expl")).toBe(true);
    expect(tokensMatch("Go to Explorer", "go graph")).toBe(false);
  });

  it("treats an empty query as match-all", () => {
    expect(tokensMatch("anything", "")).toBe(true);
  });
});

describe("buildPaletteItems", () => {
  it("empty query → actions + starters only, in order", () => {
    const items = buildPaletteItems("", null);
    const kinds = items.map((item) => item.kind);
    expect(kinds).toEqual([...Array(9).fill("action"), ...Array(STARTERS.length).fill("ask")]);
    expect(items[0].id).toBe("action:/console/overview");
  });

  it("finds the Memory action by its words", () => {
    const items = buildPaletteItems("scrubber", null);
    expect(items[0].id).toBe("action:/console/memory");
  });

  it("finds the Crew action by its words", () => {
    const items = buildPaletteItems("verifier", null);
    expect(items[0].id).toBe("action:/console/crew");
  });

  it("finds the Time machine action by its words", () => {
    const items = buildPaletteItems("time machine", null);
    expect(items[0].id).toBe("action:/console/history");
  });

  it("finds the Blast radius action by its words", () => {
    expect(buildPaletteItems("blast radius", null)[0].id).toBe(
      "action:/console/impact",
    );
    expect(buildPaletteItems("what breaks", null)[0].id).toBe(
      "action:/console/impact",
    );
  });

  it("filters actions by keyword", () => {
    const items = buildPaletteItems("judges", null);
    expect(items[0].id).toBe("action:/console/about");
    expect(items.some((item) => item.id === "action:/console/explorer")).toBe(false);
  });

  it("maps symbols and files to explorer drill-down targets", () => {
    const items = buildPaletteItems("url", FIND);
    const symbol = items.find((item) => item.kind === "symbol");
    const file = items.find((item) => item.kind === "file");
    expect(symbol?.to).toBe(
      "/console/explorer?file=src%2Fflask%2Fhelpers.py&qualname=flask.helpers.url_for",
    );
    expect(file?.to).toBe("/console/explorer?path=src%2Fflask%2Fhelpers.py");
  });

  it("hides find rows below the 2-char floor", () => {
    const items = buildPaletteItems("u", FIND);
    expect(items.some((item) => item.kind === "symbol" || item.kind === "file")).toBe(false);
  });

  it("adds a free-text ask fallback last for non-empty queries", () => {
    const items = buildPaletteItems("who calls url_for", FIND);
    const last = items[items.length - 1];
    expect(last.kind).toBe("ask");
    expect(last.label).toBe("Ask the archive: “who calls url_for”");
    expect(last.to).toBe("/console/ask?q=who+calls+url_for");
  });

  it("matched starters rank above the free-text fallback", () => {
    const items = buildPaletteItems("who should review app.py", null);
    const asks = items.filter((item) => item.kind === "ask");
    expect(asks[0].label).toBe(STARTERS[1]);
    expect(asks[asks.length - 1].label).toContain("Ask the archive:");
  });

  it("does not duplicate an exact starter as free-text", () => {
    const items = buildPaletteItems(STARTERS[0], null);
    const asks = items.filter((item) => item.kind === "ask");
    expect(asks).toHaveLength(1);
    expect(asks[0].label).toBe(STARTERS[0]);
  });

  it("keeps the promised group order: actions → symbols → files → asks", () => {
    const items = buildPaletteItems("go", FIND);
    const kinds = groupPaletteItems(items).map((group) => group.kind);
    expect(kinds).toEqual(["action", "symbol", "file", "ask"]);
  });
});

describe("groupPaletteItems", () => {
  it("starts runs and carries flat indices", () => {
    const items = buildPaletteItems("", null);
    const groups = groupPaletteItems(items);
    expect(groups[0]).toMatchObject({ kind: "action", label: "Actions", start: 0 });
    expect(groups[1]).toMatchObject({ kind: "ask", label: "Asks", start: 9 });
    expect(groups[1].items).toHaveLength(STARTERS.length);
  });
});
