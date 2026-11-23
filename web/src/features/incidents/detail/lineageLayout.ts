import dagre from "@dagrejs/dagre";

import type { components } from "@/api/schema";

type Lineage = components["schemas"]["LineageOut"];
type LineageNode = components["schemas"]["LineageNodeOut"];
type LineageEdge = components["schemas"]["LineageEdgeOut"];

export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 56;
const RANK_GAP = 70; // between columns, where the arrows and their labels go
const NODE_GAP = 18; // between nodes in one column
const MARGIN = 16;

export type PositionedNode = {
  node: LineageNode;
  /** Top-left corner. */
  x: number;
  y: number;
};

export type Layout = {
  nodes: PositionedNode[];
  edges: LineageEdge[];
  width: number;
  height: number;
};

/**
 * Place the slice left to right: what feeds a node is always in an earlier column.
 *
 * Nodes and edges are fed to dagre in sorted order, so the same slice always lays out the same
 * way and a reload does not shuffle the picture. An edge whose end is not in the slice is left
 * out instead of failing.
 */
export function layoutLineage(lineage: Lineage): Layout {
  const nodes = [...lineage.nodes].sort((a, b) => a.id.localeCompare(b.id));
  const known = new Set(nodes.map((node) => node.id));
  const edges = lineage.edges
    .filter((edge) => known.has(edge.source) && known.has(edge.target))
    .sort((a, b) => `${a.source}>${a.target}`.localeCompare(`${b.source}>${b.target}`));

  const graph = new dagre.graphlib.Graph();
  graph.setGraph({
    rankdir: "LR",
    ranksep: RANK_GAP,
    nodesep: NODE_GAP,
    marginx: MARGIN,
    marginy: MARGIN,
  });
  graph.setDefaultEdgeLabel(() => ({}));
  for (const node of nodes) {
    graph.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const edge of edges) {
    graph.setEdge(edge.source, edge.target);
  }
  dagre.layout(graph);

  const positioned = nodes.map((node) => {
    const centre = graph.node(node.id) as { x: number; y: number };
    return { node, x: centre.x - NODE_WIDTH / 2, y: centre.y - NODE_HEIGHT / 2 };
  });
  const size = graph.graph() as { width?: number; height?: number };
  return { nodes: positioned, edges, width: size.width ?? 0, height: size.height ?? 0 };
}
