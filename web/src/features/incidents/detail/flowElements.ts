import { MarkerType, type Edge, type Node } from "@xyflow/react";

import type { components } from "@/api/schema";

import { layoutLineage, NODE_HEIGHT, NODE_WIDTH } from "./lineageLayout";

type Lineage = components["schemas"]["LineageOut"];

export type NodeData = { node: components["schemas"]["LineageNodeOut"] };

/** The slice as React Flow's nodes and edges, positioned by `layoutLineage`. */
export function toFlowElements(lineage: Lineage): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const layout = layoutLineage(lineage);
  return {
    nodes: layout.nodes.map(({ node, x, y }) => ({
      id: node.id,
      type: "lineage",
      position: { x, y },
      data: { node },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
    edges: layout.edges.map((edge) => ({
      id: `${edge.source}->${edge.target}`,
      source: edge.source,
      target: edge.target,
      label: edge.inferred ? `${edge.kind}, inferred` : edge.kind,
      markerEnd: { type: MarkerType.ArrowClosed },
      // Inferred edges are guesses at table level, so they are drawn as such.
      style: edge.inferred ? { strokeDasharray: "6 4" } : undefined,
      labelStyle: { fontSize: 11 },
    })),
  };
}
