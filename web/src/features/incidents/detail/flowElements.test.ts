import { MarkerType } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import type { components } from "@/api/schema";

import { NODE_HEIGHT, NODE_WIDTH } from "./lineageLayout";
import { toFlowElements } from "./flowElements";

type Lineage = components["schemas"]["LineageOut"];

const lineage: Lineage = {
  version: "v1",
  root_node: "raw.a",
  truncated: false,
  unreached: [],
  nodes: [
    { id: "raw.a", table: "raw", column: "a", kind: "raw", role: "root" },
    { id: "brz.b", table: "brz", column: "b", kind: "model", role: "affected" },
  ],
  edges: [{ source: "raw.a", target: "brz.b", kind: "transform", inferred: false }],
};

describe("toFlowElements", () => {
  it("makes a sized node of the lineage type for each lineage node, carrying its data", () => {
    const { nodes } = toFlowElements(lineage);

    expect(nodes.map((n) => [n.id, n.type, n.width, n.height])).toEqual([
      ["brz.b", "lineage", NODE_WIDTH, NODE_HEIGHT],
      ["raw.a", "lineage", NODE_WIDTH, NODE_HEIGHT],
    ]);
    expect(nodes.find((n) => n.id === "raw.a")?.data.node.role).toBe("root");
    const x = (id: string) => nodes.find((n) => n.id === id)?.position.x ?? 0;
    expect(x("raw.a")).toBeLessThan(x("brz.b"));
  });

  it("labels an edge with its kind and ends it with an arrow", () => {
    const [edge] = toFlowElements(lineage).edges;

    expect(edge?.id).toBe("raw.a->brz.b");
    expect(edge?.label).toBe("transform");
    expect(edge?.markerEnd).toEqual({ type: MarkerType.ArrowClosed });
    expect(edge?.style).toBeUndefined();
  });

  it("draws an inferred edge dashed and says so in its label", () => {
    const inferred: Lineage = {
      ...lineage,
      edges: [{ source: "raw.a", target: "brz.b", kind: "filter", inferred: true }],
    };

    const [edge] = toFlowElements(inferred).edges;

    expect(edge?.label).toBe("filter, inferred");
    expect(edge?.style).toEqual({ strokeDasharray: "6 4" });
  });
});
