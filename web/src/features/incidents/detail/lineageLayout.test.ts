import { describe, expect, it } from "vitest";

import type { components } from "@/api/schema";

import { layoutLineage, NODE_HEIGHT, NODE_WIDTH } from "./lineageLayout";

type Lineage = components["schemas"]["LineageOut"];

const node = (id: string, role: "root" | "affected" | "path") =>
  ({
    id,
    table: id.split(".")[0],
    column: id.split(".")[1],
    kind: "model",
    role,
  }) as Lineage["nodes"][number];
const edge = (source: string, target: string) =>
  ({ source, target, kind: "direct", inferred: false }) as Lineage["edges"][number];

const base = { version: "v1", root_node: "raw.a", unreached: [], truncated: false };

const chain: Lineage = {
  ...base,
  nodes: [node("gld.c", "affected"), node("raw.a", "root"), node("brz.b", "path")],
  edges: [edge("brz.b", "gld.c"), edge("raw.a", "brz.b")],
};

const at = (layout: ReturnType<typeof layoutLineage>, id: string) => {
  const found = layout.nodes.find((n) => n.node.id === id);
  if (!found) {
    throw new Error(`no node ${id}`);
  }
  return found;
};

describe("layoutLineage", () => {
  it("puts what feeds a node in an earlier column, left to right", () => {
    const layout = layoutLineage(chain);

    const [root, mid, last] = ["raw.a", "brz.b", "gld.c"].map((id) => at(layout, id).x);
    expect(root).toBeLessThan(mid ?? 0);
    expect(mid).toBeLessThan(last ?? 0);
  });

  it("leaves a gap between columns for the arrows and their labels", () => {
    const layout = layoutLineage(chain);

    expect(at(layout, "brz.b").x - at(layout, "raw.a").x).toBeGreaterThan(NODE_WIDTH + 50);
  });

  it("does not overlap nodes that share a column", () => {
    const wide: Lineage = {
      ...base,
      nodes: [
        node("raw.a", "root"),
        node("a.x", "affected"),
        node("b.x", "affected"),
        node("c.x", "affected"),
      ],
      edges: [edge("raw.a", "a.x"), edge("raw.a", "b.x"), edge("raw.a", "c.x")],
    };

    const layout = layoutLineage(wide);

    const column = ["a.x", "b.x", "c.x"].map((id) => at(layout, id));
    expect(new Set(column.map((n) => n.x)).size).toBe(1);
    const [first = 0, second = 0, third = 0] = column.map((n) => n.y).sort((a, b) => a - b);
    expect(second - first).toBeGreaterThanOrEqual(NODE_HEIGHT);
    expect(third - second).toBeGreaterThanOrEqual(NODE_HEIGHT);
  });

  it("lays the same slice out the same way whatever order the API listed it in", () => {
    const shuffled: Lineage = {
      ...chain,
      nodes: [...chain.nodes].reverse(),
      edges: [...chain.edges].reverse(),
    };

    expect(layoutLineage(shuffled)).toEqual(layoutLineage(chain));
  });

  it("reports a size that holds every node", () => {
    const layout = layoutLineage(chain);

    for (const { x, y } of layout.nodes) {
      expect(x).toBeGreaterThanOrEqual(0);
      expect(y).toBeGreaterThanOrEqual(0);
      expect(x + NODE_WIDTH).toBeLessThanOrEqual(layout.width);
      expect(y + NODE_HEIGHT).toBeLessThanOrEqual(layout.height);
    }
  });

  it("drops an edge whose end is not in the slice instead of failing", () => {
    const layout = layoutLineage({ ...chain, edges: [...chain.edges, edge("raw.a", "missing.x")] });

    expect(layout.edges).toHaveLength(2);
  });

  it("places a lone root", () => {
    const layout = layoutLineage({ ...base, nodes: [node("raw.a", "root")], edges: [] });

    expect(layout.nodes).toHaveLength(1);
    expect(layout.edges).toEqual([]);
  });
});
