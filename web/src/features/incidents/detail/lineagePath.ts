import type { components } from "@/api/schema";

type Lineage = components["schemas"]["LineageOut"];
type LineageNode = components["schemas"]["LineageNodeOut"];
type LineageEdge = components["schemas"]["LineageEdgeOut"];

/** One step of the path: a node and what it feeds next. */
export type PathStep = {
  node: LineageNode;
  feeds: LineageEdge[];
};

/**
 * The lineage slice as an ordered list, from the root cause towards what it broke.
 *
 * Nodes come out in topological order (a node always follows everything that feeds it), with
 * ties broken by id so the same slice always reads the same. This is the text form of the graph:
 * it is what a screen reader or a keyboard user reads, and what the graph is checked against.
 */
export function pathSteps(lineage: Lineage): PathStep[] {
  const byId = new Map(lineage.nodes.map((node) => [node.id, node]));
  const feeds = new Map<string, LineageEdge[]>();
  const waiting = new Map<string, number>(lineage.nodes.map((node) => [node.id, 0]));
  for (const edge of lineage.edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) {
      continue;
    }
    feeds.set(edge.source, [...(feeds.get(edge.source) ?? []), edge]);
    waiting.set(edge.target, (waiting.get(edge.target) ?? 0) + 1);
  }

  const ready = lineage.nodes
    .filter((node) => waiting.get(node.id) === 0)
    .map((node) => node.id)
    .sort();
  const ordered: string[] = [];
  while (ready.length > 0) {
    const id = ready.shift();
    if (id === undefined) {
      break;
    }
    ordered.push(id);
    for (const edge of feeds.get(id) ?? []) {
      const left = (waiting.get(edge.target) ?? 1) - 1;
      waiting.set(edge.target, left);
      if (left === 0) {
        ready.push(edge.target);
        ready.sort();
      }
    }
  }

  return ordered.flatMap((id) => {
    const node = byId.get(id);
    if (node === undefined) {
      return [];
    }
    const out = [...(feeds.get(id) ?? [])].sort((a, b) => a.target.localeCompare(b.target));
    return [{ node, feeds: out }];
  });
}

/** True when the slice holds nothing but the root: no path to what broke could be traced. */
export function isRootOnly(lineage: Lineage): boolean {
  return lineage.edges.length === 0 && lineage.nodes.length <= 1;
}
