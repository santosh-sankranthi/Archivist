import { describe, expect, it } from "vitest";

import { clampText, formatInt, relTime, shortSha, stampDate } from "./format";

describe("formatInt", () => {
  it("uses thousands separators", () => {
    expect(formatInt(8812)).toBe("8,812");
    expect(formatInt(75360)).toBe("75,360");
    expect(formatInt(0)).toBe("0");
  });
});

describe("shortSha", () => {
  it("truncates to 7 by default", () => {
    expect(shortSha("859d63902e5c9549fda4c167e9787a42c0136413")).toBe("859d639");
  });
  it("keeps short input intact", () => {
    expect(shortSha("abc")).toBe("abc");
  });
});

describe("relTime", () => {
  const now = 1_700_000_000;
  it("buckets seconds, minutes, hours, days, weeks", () => {
    expect(relTime(now - 5, now)).toBe("just now");
    expect(relTime(now - 44, now)).toBe("just now");
    expect(relTime(now - 440, now)).toBe("7m ago");
    expect(relTime(now - 3 * 3600, now)).toBe("3h ago");
    expect(relTime(now - 2 * 86400, now)).toBe("2d ago");
    expect(relTime(now - 20 * 86400, now)).toBe("3w ago");
  });
  it("falls back to month + year for old timestamps (UTC)", () => {
    // 1681603200 = 2023-04-16T00:00:00Z; 1650000000 ~= 2022-04-15.
    expect(relTime(1681603200, 1700000000)).toBe("Apr 2023");
    expect(relTime(1650000000, 1681603200)).toBe("Apr 2022");
  });
  it("never counts backwards", () => {
    expect(relTime(now + 500, now)).toBe("just now");
  });
});

describe("clampText", () => {
  it("leaves short text alone", () => {
    expect(clampText("fix registry panic", 30)).toBe("fix registry panic");
  });
  it("ellipsizes at exactly n chars", () => {
    const out = clampText("Merge pull request #5111 from pallets/update-tests", 20);
    expect(out.length).toBe(20);
    expect(out.endsWith("…")).toBe(true);
  });
});

describe("stampDate", () => {
  it("carries the year (UTC)", () => {
    // 1270080000 = 2010-04-01T00:00:00Z
    expect(stampDate(1270080000)).toBe("Apr 1, 2010");
  });
});
