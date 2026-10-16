import { describe, expect, it } from "vitest";

import {
  filterWeakEdges,
  labelFor,
  neighborhood,
  orderChain,
  questionFor,
  serverIdOf,
  titleFor,
  toCyElements,
} from "./graph";
import type { GraphPayload } from "./types";

const payload: GraphPayload = {
  nodes: [
    {
      id: 1,
      type: "Symbol",
      props: { name: "main", qualname: "main", file: "app.py", kind: "function", lineno: 4 },
      focus: true,
    },
    {
      id: 2,
      type: "Symbol",
      props: { name: "helper", qualname: "helper", file: "util.py", kind: "function", lineno: 1 },
    },
    { id: 3, type: "File", props: { path: "util.py", loc: 8 } },
  ],
  edges: [
    { id: 11, rel: "CALLS", source: 1, target: 2, props: { weak: false } },
    { id: 12, rel: "CALLS", source: 2, target: 1, props: { weak: true } },
    { id: 13, rel: "CALLS", source: 1, target: 99, props: {} },
  ],
  notes: [],
};

describe("labels", () => {
  it("prefers name for symbols, path for files", () => {
    expect(labelFor(payload.nodes[0])).toBe("main");
    expect(labelFor(payload.nodes[2])).toBe("util.py");
  });

  it("falls back to qualname then id", () => {
    expect(
      labelFor({ id: 7, type: "Symbol", props: { qualname: "A.b" } }),
    ).toBe("A.b");
    expect(labelFor({ id: 8, type: "Module", props: {} })).toBe("8");
  });

  it("titles carry full coordinates", () => {
    expect(titleFor(payload.nodes[0])).toBe("main — app.py:4");
  });
});

describe("questionFor", () => {
  it("names the symbol and its file", () => {
    const q = questionFor(payload.nodes[1]);
    expect(q).toContain("helper");
    expect(q).toContain("util.py");
  });
});

describe("neighborhood", () => {
  it("keeps the node, its neighbours, and only touching edges", () => {
    const { nodes, edges } = neighborhood(payload, 1);
    // 99 is an endpoint of edge 13, so it counts as a neighbour here —
    // pruning edges with unplotted endpoints is toCyElements' job.
    expect(nodes).toEqual(new Set([1, 2, 99]));
    expect(edges).toEqual(new Set([11, 12, 13]));
    const isolated = neighborhood({ ...payload, edges: [] }, 3);
    expect(isolated.nodes).toEqual(new Set([3]));
    expect(isolated.edges.size).toBe(0);
  });
});

describe("filterWeakEdges", () => {
  it("counts what it hides, keeps weak when asked", () => {
    expect(filterWeakEdges(payload.edges, false)).toEqual({
      edges: [payload.edges[0], payload.edges[2]],
      hidden: 1,
    });
    expect(filterWeakEdges(payload.edges, true).hidden).toBe(0);
  });
});

describe("toCyElements", () => {
  it("prefixes ids and drops edges with missing endpoints", () => {
    const elements = toCyElements(payload);
    const ids = elements.map((el) => (el.data as { id: string }).id);
    expect(ids).toEqual(["n1", "n2", "n3", "e11", "e12"]);
  });

  it("carries nodeId, kind, and weak through to element data", () => {
    const elements = toCyElements(payload);
    const first = elements[0].data as Record<string, unknown>;
    expect(first.nodeId).toBe(1);
    expect(first.kind).toBe("function");
    expect(first.focus).toBe(true);
    const weakEdge = elements.find(
      (el) => (el.data as { id: string }).id === "e12",
    );
    expect((weakEdge?.data as Record<string, unknown>).weak).toBe(true);
  });

  it("round-trips server ids", () => {
    expect(serverIdOf("n42")).toBe(42);
    expect(serverIdOf("e7")).toBe(7);
  });
});

describe("orderChain", () => {
  const edge = (id: number, source: number, target: number) => ({
    id,
    rel: "CALLS",
    source,
    target,
    props: {},
  });

  it("orders a directed chain start to end", () => {
    const edges = [edge(4, 3, 4), edge(2, 1, 2), edge(3, 2, 3), edge(99, 7, 8)];
    const chain = orderChain(edges, new Set([2, 3, 4]));
    expect(chain).toEqual({ nodes: [1, 2, 3, 4], edges: [2, 3, 4] });
  });

  it("walks reverse-direction chains in edge direction", () => {
    const edges = [edge(1, 2, 1), edge(2, 3, 2)];
    expect(orderChain(edges, new Set([1, 2]))).toEqual({
      nodes: [3, 2, 1],
      edges: [2, 1],
    });
  });

  it("returns null for forks, cycles, and disconnected fragments", () => {
    const fork = [edge(1, 1, 2), edge(2, 1, 3)];
    expect(orderChain(fork, new Set([1, 2]))).toBeNull();
    const cycle = [edge(1, 1, 2), edge(2, 2, 1)];
    expect(orderChain(cycle, new Set([1, 2]))).toBeNull();
    const twoChains = [edge(1, 1, 2), edge(2, 3, 4)];
    expect(orderChain(twoChains, new Set([1, 2]))).toBeNull();
  });

  it("handles empty selections and single edges", () => {
    expect(orderChain([edge(1, 1, 2)], new Set())).toBeNull();
    expect(orderChain([edge(1, 1, 2)], new Set([1]))).toEqual({
      nodes: [1, 2],
      edges: [1],
    });
  });
});
