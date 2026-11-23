import { describe, expect, it } from "vitest";

import type { components } from "@/api/schema";

import { isRootOnly, pathSteps } from "./lineagePath";

type Lineage = components["schemas"]["LineageOut"];

const node = (id: string, role: "root" | "affected" | "path", kind = "model") => {
  const [table = "", ...rest] = id.split(".");
  return { id, table, column: rest.join("."), kind, role } as Lineage["nodes"][number];
};
const edge = (source: string, target: string, kind = "direct") =>
  ({ source, target, kind, inferred: false }) as Lineage["edges"][number];

const base = { version: "v1", unreached: [], truncated: false };

describe("pathSteps", () => {
  it("orders a chain from the root to what it broke", () => {
    const lineage: Lineage = {
      ...base,
      root_node: "raw.nav",
      nodes: [node("gld.nav", "affected"), node("raw.nav", "root", "raw"), node("brz.nav", "path")],
      edges: [edge("brz.nav", "gld.nav"), edge("raw.nav", "brz.nav", "transform")],
    };

    const steps = pathSteps(lineage);

    expect(steps.map((s) => s.node.id)).toEqual(["raw.nav", "brz.nav", "gld.nav"]);
    expect(steps[0]?.feeds.map((e) => `${e.target} ${e.kind}`)).toEqual(["brz.nav transform"]);
    expect(steps[2]?.feeds).toEqual([]);
  });

  it("puts a node after everything that feeds it when paths join, ties by id", () => {
    const lineage: Lineage = {
      ...base,
      root_node: "raw.a",
      nodes: [
        node("join.x", "affected"),
        node("mid.b", "path"),
        node("mid.a", "path"),
        node("raw.a", "root", "raw"),
      ],
      edges: [
        edge("raw.a", "mid.b"),
        edge("raw.a", "mid.a"),
        edge("mid.a", "join.x"),
        edge("mid.b", "join.x"),
      ],
    };

    expect(pathSteps(lineage).map((s) => s.node.id)).toEqual(["raw.a", "mid.a", "mid.b", "join.x"]);
  });

  it("lists what a node feeds in a stable order", () => {
    const lineage: Lineage = {
      ...base,
      root_node: "r.x",
      nodes: [node("r.x", "root"), node("b.x", "affected"), node("a.x", "affected")],
      edges: [edge("r.x", "b.x"), edge("r.x", "a.x")],
    };

    expect(pathSteps(lineage)[0]?.feeds.map((e) => e.target)).toEqual(["a.x", "b.x"]);
  });

  it("ignores an edge whose end is not in the slice instead of failing", () => {
    const lineage: Lineage = {
      ...base,
      root_node: "r.x",
      nodes: [node("r.x", "root")],
      edges: [edge("r.x", "missing.x")],
    };

    expect(pathSteps(lineage).map((s) => s.node.id)).toEqual(["r.x"]);
  });
});

describe("isRootOnly", () => {
  it("is true for a lone root and false once there is a path", () => {
    const lone: Lineage = { ...base, root_node: "r.x", nodes: [node("r.x", "root")], edges: [] };
    const path: Lineage = {
      ...lone,
      nodes: [node("r.x", "root"), node("a.x", "affected")],
      edges: [edge("r.x", "a.x")],
    };

    expect(isRootOnly(lone)).toBe(true);
    expect(isRootOnly(path)).toBe(false);
  });
});
