/** Trace-ribbon helpers — fixture-driven, no DOM. */

import { describe, expect, it } from "vitest";

import {
  evidenceRefs,
  mergeReplays,
  queriesForStep,
  replayRequest,
  rowLine,
  spotlightFor,
  stepFact,
  summarizeRows,
  toolLabel,
} from "./trace";
import type { AskAnswer, AskStep, GraphEdge, GraphNode } from "./types";

function makeStep(partial: Partial<AskStep>): AskStep {
  return {
    round: 0,
    tool: "callers",
    params: {},
    status: "ok",
    count: null,
    ms: 7,
    notes: [],
    error: null,
    result: null,
    ...partial,
  };
}

const callersStep = makeStep({
  tool: "callers",
  count: 2,
  params: { symbol: "url_for", depth: 1 },
  result: {
    results: [
      {
        file: "examples/tutorial/flaskr/auth.py",
        caller: "login_required.wrapped_view",
        chain: [
          { file: "examples/tutorial/flaskr/auth.py", qualname: "login_required.wrapped_view" },
          { file: "src/flask/helpers.py", qualname: "url_for" },
        ],
      },
      {
        file: "examples/tutorial/flaskr/auth.py",
        caller: "register",
        chain: [
          { file: "examples/tutorial/flaskr/auth.py", qualname: "register" },
          { file: "src/flask/helpers.py", qualname: "url_for" },
        ],
      },
    ],
  },
});

describe("evidenceRefs", () => {
  it("collects and dedupes chain symbols for callers", () => {
    const refs = evidenceRefs(callersStep);
    expect(refs.symbols).toContainEqual({
      file: "src/flask/helpers.py",
      qualname: "url_for",
    });
    expect(refs.symbols).toHaveLength(3); // two callers + the target
    expect(refs.files).toHaveLength(0);
    expect(refs.modules).toHaveLength(0);
  });

  it("maps importers rows to files and file_history params to the file", () => {
    const importers = evidenceRefs(
      makeStep({
        tool: "importers",
        count: 1,
        params: { module: "flask.helpers" },
        result: { results: [{ file: "src/flask/app.py", line: 12 }] },
      }),
    );
    expect(importers.files).toEqual([{ path: "src/flask/app.py" }]);
    expect(importers.modules).toEqual([{ name: "flask.helpers" }]);

    const history = evidenceRefs(
      makeStep({
        tool: "file_history",
        count: 5,
        params: { path: "src/flask/helpers.py" },
        result: { results: [{ sha: "a".repeat(40), summary: "x" }] },
      }),
    );
    expect(history.files).toEqual([{ path: "src/flask/helpers.py" }]);
    expect(history.commits).toEqual([{ sha: "a".repeat(40) }]);
  });

  it("maps commit rows to deduped commit refs for search steps", () => {
    const refs = evidenceRefs(
      makeStep({
        tool: "search_commits",
        count: 3,
        result: {
          results: [
            { sha: "d".repeat(40), summary: "x" },
            { sha: "d".repeat(40), summary: "x" },
            { sha: "e".repeat(40), summary: "y" },
          ],
        },
      }),
    );
    expect(refs.commits).toEqual([
      { sha: "d".repeat(40) },
      { sha: "e".repeat(40) },
    ]);
  });

  it("stays empty for author/memory/aggregate steps (no subgraph)", () => {
    for (const tool of ["owners", "recall", "remember", "overview"]) {
      const refs = evidenceRefs(
        makeStep({
          tool,
          result: { results: [{ author: "Dev", sha: "b".repeat(40) }] },
        }),
      );
      expect(refs.symbols).toHaveLength(0);
      expect(refs.files).toHaveLength(0);
      expect(refs.modules).toHaveLength(0);
      expect(refs.commits).toHaveLength(0);
    }
  });
});

describe("replayRequest", () => {
  it("keeps step indexes stable and skips replays-less steps", () => {
    const answer: AskAnswer = {
      question: "q",
      text: "t",
      partial: false,
      error: null,
      llm_calls: 2,
      steps: [callersStep, makeStep({ tool: "overview", result: { results: {} } })],
      queries: [],
      usage: {},
    };
    const request = replayRequest(answer);
    expect(request).not.toBeNull();
    expect(request!.steps).toHaveLength(1);
    expect(request!.steps[0].id).toBe("0");
  });

  it("returns null when nothing replays", () => {
    const answer: AskAnswer = {
      question: "q",
      text: "t",
      partial: false,
      error: null,
      llm_calls: 1,
      steps: [makeStep({ tool: "overview" })],
      queries: [],
      usage: {},
    };
    expect(replayRequest(answer)).toBeNull();
  });
});

describe("mergeReplays + spotlightFor", () => {
  const node = (id: number): GraphNode => ({ id, type: "Symbol", props: {} });
  const edge = (id: number, source: number, target: number): GraphEdge => ({
    id,
    rel: "CALLS",
    source,
    target,
    props: {},
  });

  it("unions by id and drops edges whose endpoints are missing", () => {
    const merged = mergeReplays(
      new Map([
        [0, { nodes: [node(1), node(2)], edges: [edge(10, 1, 2)], notes: [] }],
        [1, { nodes: [node(2), node(3)], edges: [edge(11, 2, 3), edge(12, 3, 99)], notes: [] }],
      ]),
    );
    expect(merged.nodes.map((n) => n.id).sort()).toEqual([1, 2, 3]);
    expect(merged.edges.map((e) => e.id).sort()).toEqual([10, 11]);
  });

  it("spotlightFor is an honest empty set for a missing replay", () => {
    const empty = spotlightFor(undefined);
    expect(empty.nodes.size).toBe(0);
    expect(empty.edges.size).toBe(0);
    const lit = spotlightFor({
      nodes: [node(1)],
      edges: [edge(2, 1, 1)],
      notes: [],
    });
    expect([...lit.nodes]).toEqual([1]);
    expect([...lit.edges]).toEqual([2]);
  });
});

describe("step labels", () => {
  it("pluralizes honestly", () => {
    expect(stepFact(makeStep({ tool: "callers", count: 1 }))).toBe("1 caller");
    expect(stepFact(makeStep({ tool: "callers", count: 12 }))).toBe("12 callers");
    expect(stepFact(makeStep({ tool: "file_history", count: 0 }))).toBe("0 commits");
    expect(stepFact(makeStep({ tool: "overview" }))).toBe("graph at a glance");
    expect(stepFact(makeStep({ tool: "remember" }))).toBe("saved");
  });

  it("surfaces non-ok statuses", () => {
    expect(stepFact(makeStep({ status: "error", error: "boom" }))).toBe("error");
    expect(stepFact(makeStep({ status: "not_found" }))).toBe("nothing found");
  });

  it("toolLabel is human", () => {
    expect(toolLabel("file_history")).toBe("file history");
  });
});

describe("queriesForStep", () => {
  it("matches by round and tool", () => {
    const answer = {
      queries: [
        { round: 0, tool: "callers", cypher: "MATCH a", params: {}, ms: 1, rows: 2 },
        { round: 0, tool: "overview", cypher: "MATCH b", params: {}, ms: 1, rows: 2 },
        { round: 1, tool: "callers", cypher: "MATCH c", params: {}, ms: 1, rows: 2 },
      ],
    } as AskAnswer;
    const hits = queriesForStep(answer, makeStep({ tool: "callers", round: 1 }));
    expect(hits).toHaveLength(1);
    expect(hits[0].cypher).toBe("MATCH c");
  });
});

describe("row rendering", () => {
  it("keeps identifying facts first", () => {
    expect(rowLine({ sha: "abcdef123456", summary: "Fix bug" })).toBe("abcdef12 Fix bug");
    expect(rowLine({ file: "a.py", qualname: "fn" })).toBe("a.py · fn");
    expect(rowLine({ file: "a.py", line: 12 })).toBe("a.py:12");
    expect(rowLine({ author: "Dev", commits: 3 })).toBe("Dev · 3 commits");
  });

  it("caps summaries and counts the rest", () => {
    const step = makeStep({
      tool: "find_symbols",
      result: {
        results: [
          { file: "a.py", qualname: "f1" },
          { file: "a.py", qualname: "f2" },
          { file: "a.py", qualname: "f3" },
          { file: "a.py", qualname: "f4" },
        ],
      },
    });
    const lines = summarizeRows(step);
    expect(lines).toHaveLength(4);
    expect(lines[3]).toBe("… 1 more row(s)");
  });
});
