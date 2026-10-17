import { describe, expect, it } from "vitest";

import {
  formatMs,
  handoffBetween,
  phaseChips,
  runWhen,
  spineNodes,
  spineOrder,
} from "./crew";
import type { CrewPhase, CrewRun } from "./types";

function phase(
  role: string,
  ts: number | null,
  extra: Partial<CrewPhase> = {},
): CrewPhase {
  return {
    id: `run:${role}`,
    role,
    ts,
    question: null,
    input_truncated: false,
    answer: null,
    answer_truncated: false,
    duration_ms: null,
    steps: null,
    queries: null,
    llm_calls: null,
    partial: null,
    error: null,
    ...extra,
  };
}

function run(phases: CrewPhase[], handoffs: CrewRun["handoffs"] = []): CrewRun {
  return {
    run: "run-1",
    question: null,
    started_at: 100,
    ended_at: 160,
    status: "complete",
    phases,
    handoffs,
  };
}

describe("spineOrder", () => {
  it("orders the same-second spine by the known crew order", () => {
    const ordered = spineOrder([
      phase("verifier", 5),
      phase("planner", 5),
      phase("investigator", 5),
    ]);
    expect(ordered.map((p) => p.role)).toEqual([
      "planner",
      "investigator",
      "verifier",
    ]);
  });

  it("lets timestamps win over the role order", () => {
    const ordered = spineOrder([phase("verifier", 4), phase("planner", 9)]);
    expect(ordered.map((p) => p.role)).toEqual(["verifier", "planner"]);
  });

  it("breaks id ties deterministically", () => {
    const a = phase("planner", 5, { id: "run:a" });
    const b = phase("planner", 5, { id: "run:b" });
    const ordered = spineOrder([b, a]);
    expect(ordered.map((p) => p.id)).toEqual(["run:a", "run:b"]);
  });
});

describe("spineNodes", () => {
  it("keeps a complete spine as three phase slots", () => {
    const nodes = spineNodes(
      run([phase("planner", 5), phase("investigator", 6), phase("verifier", 7)]),
    );
    expect(nodes.map((n) => [n.role, n.phase !== null])).toEqual([
      ["planner", true],
      ["investigator", true],
      ["verifier", true],
    ]);
  });

  it("surfaces a missing verifier as a ghost slot", () => {
    const nodes = spineNodes(run([phase("planner", 5), phase("investigator", 6)]));
    expect(nodes.map((n) => [n.role, n.phase !== null])).toEqual([
      ["planner", true],
      ["investigator", true],
      ["verifier", false],
    ]);
  });

  it("shows a missing middle role as a ghost between its neighbours", () => {
    const nodes = spineNodes(run([phase("planner", 5), phase("verifier", 7)]));
    expect(nodes.map((n) => [n.role, n.phase !== null])).toEqual([
      ["planner", true],
      ["investigator", false],
      ["verifier", true],
    ]);
  });

  it("keeps the full expected spine whenever any known phase exists", () => {
    const nodes = spineNodes(run([phase("planner", 5)]));
    expect(nodes.map((n) => [n.role, n.phase !== null])).toEqual([
      ["planner", true],
      ["investigator", false],
      ["verifier", false],
    ]);
  });

  it("appends unknown roles after the known spine", () => {
    const nodes = spineNodes(
      run([phase("planner", 5), phase("auditor", 8)]),
    );
    expect(nodes.map((n) => n.role)).toEqual([
      "planner",
      "investigator",
      "verifier",
      "auditor",
    ]);
  });

  it("returns no slots for an empty run", () => {
    expect(spineNodes(run([]))).toEqual([]);
  });
});

describe("handoffBetween", () => {
  it("matches the recorded edge by from/to roles", () => {
    const r = run([], [
      { from: "planner", to: "investigator", note: "go", ts: 6 },
    ]);
    expect(handoffBetween(r, "planner", "investigator")?.note).toBe("go");
    expect(handoffBetween(r, "investigator", "verifier")).toBeNull();
  });
});

describe("runWhen", () => {
  it("collapses a single-second run to one stamp", () => {
    expect(runWhen({ ...run([]), ended_at: 100 })).toBe("Jan 1, 00:01");
  });

  it("draws the span when the run crossed seconds", () => {
    expect(runWhen(run([]))).toBe("Jan 1, 00:01 → Jan 1, 00:02");
  });

  it("reads honestly without stamps", () => {
    expect(runWhen({ ...run([]), started_at: null })).toBe("—");
  });
});

describe("formatMs", () => {
  it("scales units by magnitude", () => {
    expect(formatMs(72)).toBe("72ms");
    expect(formatMs(1400)).toBe("1.4s");
    expect(formatMs(125_400)).toBe("2m 05s");
  });

  it("reads null as an em dash", () => {
    expect(formatMs(null)).toBe("—");
  });
});

describe("phaseChips", () => {
  it("emits present figures in a fixed order, skipping nulls", () => {
    const chips = phaseChips(
      phase("planner", 5, { steps: 1, queries: 3, llm_calls: 2, duration_ms: 72 }),
    );
    expect(chips.map((c) => `${c.key}=${c.value}`)).toEqual([
      "steps=1",
      "queries=3",
      "llm calls=2",
      "time=72ms",
    ]);
    expect(phaseChips(phase("planner", 5))).toEqual([]);
  });
});
