import { describe, expect, it } from "vitest";

import { prettyJson, shortNode } from "./text";

describe("shortNode", () => {
  it.each([
    [
      "test.lineageheal.not_null_brz_amfi__navall_nav_inr.5b0d921a09",
      "not_null_brz_amfi__navall_nav_inr",
    ],
    ["model.lineageheal.brz_amfi__navall", "brz_amfi__navall"],
    [
      "test.lineageheal.assert_gld_mf_nav_daily_match_silver",
      "assert_gld_mf_nav_daily_match_silver",
    ],
    ["not_null_x", "not_null_x"],
  ])("shortens %s", (id, name) => {
    expect(shortNode(id)).toBe(name);
  });
});

describe("prettyJson", () => {
  it("indents small values", () => {
    expect(prettyJson({ a: 1 })).toBe('{\n  "a": 1\n}');
  });

  it("cuts a long value and says so", () => {
    const text = prettyJson({ rows: "x".repeat(10_000) });

    expect(text.length).toBeLessThan(4200);
    expect(text).toMatch(/… shortened \(\d+ characters in all\)$/);
  });
});
