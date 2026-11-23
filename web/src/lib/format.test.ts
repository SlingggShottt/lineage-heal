import { describe, expect, it } from "vitest";

import { formatConfidence, relativeTime } from "./format";

const NOW = new Date("2026-11-23T12:00:00Z");
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString();

describe("relativeTime", () => {
  it.each([
    [10, "just now"],
    [90, "1 minute ago"],
    [45 * 60, "45 minutes ago"],
    [3 * 3600 + 20 * 60, "3 hours ago"],
    [26 * 3600, "yesterday"],
    [2 * 86400 + 3600, "2 days ago"],
  ])("says %i seconds as %s", (seconds, text) => {
    expect(relativeTime(ago(seconds), NOW)).toBe(text);
  });

  it("falls back to the date after a month, where 'months ago' hides the day", () => {
    expect(relativeTime("2026-09-02T08:00:00Z", NOW)).toBe("Sep 2, 2026");
  });

  it("shows the date for a time in the future rather than a negative 'ago'", () => {
    expect(relativeTime("2026-11-24T12:00:00Z", NOW)).toBe("Nov 24, 2026");
  });
});

describe("formatConfidence", () => {
  it("rounds to a whole percentage", () => {
    expect(formatConfidence(0.9)).toBe("90%");
    expect(formatConfidence(0.934)).toBe("93%");
    expect(formatConfidence(1)).toBe("100%");
  });

  it("shows a dash when there is no confidence", () => {
    expect(formatConfidence(null)).toBe("—");
    expect(formatConfidence(undefined)).toBe("—");
  });
});
