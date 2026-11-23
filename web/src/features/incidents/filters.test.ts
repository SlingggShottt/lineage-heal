import { describe, expect, it } from "vitest";

import { PAGE_SIZE, parseFilters, toApiQuery, toSearchParams, withFilter } from "./filters";

const parse = (query: string) => parseFilters(new URLSearchParams(query));

describe("parseFilters", () => {
  it("reads every filter and the page", () => {
    expect(parse("status=fix_proposed&fault_class=nulls&severity=warn&feed=amfi&page=3")).toEqual({
      status: "fix_proposed",
      fault_class: "nulls",
      severity: "warn",
      feed: "amfi",
      page: 3,
    });
  });

  it("starts at page 1 with no filters for a plain URL", () => {
    expect(parse("")).toEqual({ page: 1 });
  });

  it.each(["status=bogus", "fault_class=", "severity=ERROR", "feed=bse"])(
    "ignores a value the API would refuse (%s)",
    (query) => {
      expect(parse(query)).toEqual({ page: 1 });
    },
  );

  it.each(["page=0", "page=-3", "page=2.5", "page=abc", "page="])(
    "ignores a page that is not a positive whole number (%s)",
    (query) => {
      expect(parse(query).page).toBe(1);
    },
  );
});

describe("toSearchParams", () => {
  it("leaves out the defaults so the plain list has a plain URL", () => {
    expect(toSearchParams({ page: 1 }).toString()).toBe("");
    expect(toSearchParams({ status: "detected", page: 2 }).toString()).toBe(
      "status=detected&page=2",
    );
  });

  it("round-trips through parseFilters", () => {
    const filters = { fault_class: "late_data", feed: "nse", page: 4 } as const;

    expect(parseFilters(toSearchParams(filters))).toEqual(filters);
  });
});

describe("toApiQuery", () => {
  it("asks for one page by offset", () => {
    expect(toApiQuery({ page: 1 })).toEqual({ limit: PAGE_SIZE, offset: 0 });
    expect(toApiQuery({ severity: "error", page: 3 })).toEqual({
      severity: "error",
      limit: PAGE_SIZE,
      offset: 2 * PAGE_SIZE,
    });
  });
});

describe("withFilter", () => {
  it("sets a filter and goes back to page 1", () => {
    expect(withFilter({ feed: "amfi", page: 4 }, "status", "detected")).toEqual({
      feed: "amfi",
      status: "detected",
      page: 1,
    });
  });

  it("clears a filter with an empty value or undefined", () => {
    expect(withFilter({ status: "detected", page: 2 }, "status", "")).toEqual({ page: 1 });
    expect(withFilter({ status: "detected", page: 2 }, "status", undefined)).toEqual({ page: 1 });
  });
});
