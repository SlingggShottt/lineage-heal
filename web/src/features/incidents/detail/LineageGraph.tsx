import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Node,
  type NodeProps,
} from "@xyflow/react";

import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { toFlowElements, type NodeData } from "./flowElements";

type Lineage = components["schemas"]["LineageOut"];
type Role = components["schemas"]["LineageNodeOut"]["role"];

const ROLE_LABELS: Record<Role, string> = {
  root: "Root cause",
  affected: "Affected",
  path: "On the path",
};
const ROLE_VARIANTS = { root: "destructive", affected: "warning", path: "outline" } as const;
const ROLE_BORDERS: Record<Role, string> = {
  root: "border-destructive",
  affected: "border-warning",
  path: "border-border",
};

/** One column of one table. The role is written out, so colour is never the only signal. */
function LineageNodeCard({ data }: NodeProps<Node<NodeData>>) {
  const { node } = data;
  return (
    <div
      title={node.id}
      className={cn(
        "bg-card text-card-foreground flex h-full flex-col justify-center gap-0.5 rounded-md border-2 px-3 py-1.5 text-xs",
        ROLE_BORDERS[node.role],
      )}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} className="opacity-0" />
      <span className="flex items-center justify-between gap-2">
        <span className="text-muted-foreground truncate">{node.table}</span>
        <Badge variant={ROLE_VARIANTS[node.role]} className="shrink-0">
          {ROLE_LABELS[node.role]}
        </Badge>
      </span>
      <span className="truncate font-mono text-sm font-semibold">{node.column}</span>
      <Handle type="source" position={Position.Right} isConnectable={false} className="opacity-0" />
    </div>
  );
}

const NODE_TYPES = { lineage: LineageNodeCard };

/**
 * The path from the root cause to what it broke, drawn left to right.
 *
 * Panning and zooming use the buttons, a pinch or a double click; the mouse wheel is left to scroll
 * the page, because a graph that captures the wheel traps people who are scrolling past it. The
 * same path is listed as text beside it for keyboard and screen-reader users.
 */
export function LineageGraph({ lineage }: { lineage: Lineage }) {
  const { nodes, edges } = toFlowElements(lineage);

  return (
    <figure className="flex flex-col gap-2">
      <div className="h-[360px] w-full overflow-hidden rounded-md border">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.25}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          edgesFocusable={false}
          zoomOnScroll={false}
          aria-label="Lineage graph"
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <figcaption className="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span>Left to right, from the root cause to what it broke.</span>
        {(["root", "path", "affected"] as const).map((role) => (
          <span key={role} className="flex items-center gap-1">
            <Badge variant={ROLE_VARIANTS[role]}>{ROLE_LABELS[role]}</Badge>
          </span>
        ))}
        <span>Dashed arrows are inferred.</span>
      </figcaption>
    </figure>
  );
}
