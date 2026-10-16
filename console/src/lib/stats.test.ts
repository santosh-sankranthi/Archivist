import { describe, expect, it } from "vitest";

import { deriveLandingStats, graphTotals } from "./stats";
import type { GraphState } from "./types";

const STATE: GraphState = {
  graph: "repo:fixture",
  repo: null,
  nodes: [
    { label: "Author", count: 10 },
    { label: "Commit", count: 200 },
    { label: "File", count: 30 },
    { label: "Module", count: 5 },
    { label: "Symbol", count: 40 },
  ],
  edges: [
    { type: "CALLS", count: 25 },
    { type: "DEFINES", count: 40 },
  ],
  memory: { sessions: 0, facts: 0, latest: null },
  llm: { configured: false, model: null },
};

describe("graphTotals", () => {
  it("returns totals and a per-label lookup", () => {
    const totals = graphTotals(STATE);
    expect(totals.nodes).toBe(285);
    expect(totals.edges).toBe(65);
    expect(totals.byLabel.get("File")).toBe(30);
  });

  it("omits absent labels from the lookup", () => {
    const sparse: GraphState = { ...STATE, nodes: [{ label: "File", count: 3 }] };
    const totals = graphTotals(sparse);
    expect(totals.byLabel.get("Commit")).toBeUndefined();
    expect(totals.nodes).toBe(3);
  });
});

describe("deriveLandingStats", () => {
  it("sums nodes and edges across labels", () => {
    const stats = deriveLandingStats(STATE);
    expect(stats.nodes).toBe(285);
    expect(stats.edges).toBe(65);
  });

  it("reads commits and symbols by label", () => {
    const stats = deriveLandingStats(STATE);
    expect(stats.commits).toBe(200);
    expect(stats.symbols).toBe(40);
  });

  it("degrades to zero when a label is absent", () => {
    const sparse: GraphState = { ...STATE, nodes: [{ label: "File", count: 3 }] };
    const stats = deriveLandingStats(sparse);
    expect(stats.commits).toBe(0);
    expect(stats.symbols).toBe(0);
    expect(stats.nodes).toBe(3);
  });
});
