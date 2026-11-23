import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { exactTime } from "@/lib/format";

import { prettyJson } from "./text";
import { Section } from "./Section";

type Evidence = components["schemas"]["EvidenceOut"];

/**
 * The Investigator's tool results, numbered as the conclusion cites them.
 *
 * Each row is a native `<details>`, closed until opened, so the list stays scannable and works
 * from the keyboard. Rows the conclusion relies on are marked.
 */
export function EvidenceList({ evidence, cited }: { evidence: Evidence[]; cited: number[] }) {
  return (
    <Section
      title="Evidence"
      description="What the Investigator looked at, in the order it looked."
    >
      {evidence.length === 0 ? (
        <p className="text-muted-foreground">No evidence was recorded.</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {evidence.map((item) => (
            <li key={item.number} id={`evidence-${String(item.number)}`} className="scroll-mt-4">
              <details className="rounded-md border">
                <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2">
                  <span className="text-muted-foreground tabular-nums">{item.number}.</span>
                  <code className="text-xs">{item.tool}</code>
                  {cited.includes(item.number) && <Badge variant="success">Cited</Badge>}
                  {item.hypothesis && (
                    <span className="text-muted-foreground">{item.hypothesis}</span>
                  )}
                </summary>
                <div className="flex flex-col gap-3 border-t px-3 py-3">
                  <div>
                    <h3 className="text-muted-foreground mb-1 text-xs font-medium">Arguments</h3>
                    <pre className="bg-muted overflow-x-auto rounded-md p-2 text-xs">
                      {prettyJson(item.arguments)}
                    </pre>
                  </div>
                  <div>
                    <h3 className="text-muted-foreground mb-1 text-xs font-medium">Result</h3>
                    <pre className="bg-muted max-h-96 overflow-auto rounded-md p-2 text-xs">
                      {prettyJson(item.output)}
                    </pre>
                  </div>
                  <p className="text-muted-foreground text-xs">
                    Collected{" "}
                    <time dateTime={item.collected_at}>{exactTime(item.collected_at)}</time>
                  </p>
                </div>
              </details>
            </li>
          ))}
        </ol>
      )}
    </Section>
  );
}
