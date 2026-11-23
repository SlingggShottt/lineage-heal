import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";

import { Fact } from "./Section";
import { shortNode } from "./text";

type Sandbox = components["schemas"]["SandboxOut"];

function NodeList({ term, nodes }: { term: string; nodes: string[] }) {
  if (nodes.length === 0) {
    return null;
  }
  return (
    <Fact term={term}>
      <ul>
        {nodes.map((node) => (
          <li key={node}>
            <code className="text-xs break-all">{shortNode(node)}</code>
          </li>
        ))}
      </ul>
    </Fact>
  );
}

/** How the sandbox judged the patch: what it fixed, what still fails and what it broke. */
export function SandboxVerdict({ sandbox }: { sandbox: Sandbox }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="flex flex-wrap items-center gap-2">
        <Badge variant={sandbox.passed ? "success" : "destructive"}>
          {sandbox.passed ? "Passed the sandbox" : "Failed the sandbox"}
        </Badge>
        <span>{sandbox.reason}</span>
      </p>
      <dl className="flex flex-col gap-2">
        <NodeList term="Fixed" nodes={sandbox.fixed} />
        <NodeList term="Still failing" nodes={sandbox.still_failing} />
        <NodeList term="Newly broken" nodes={sandbox.regressions} />
        <Fact term="Checked">
          <code className="text-xs">{sandbox.selector}</code> in {sandbox.seconds.toFixed(1)} s
        </Fact>
      </dl>
    </div>
  );
}
