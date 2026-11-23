import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { formatConfidence } from "@/lib/format";

import { Fact, Section } from "./Section";

type RootCause = components["schemas"]["RootCause"];

/** The Investigator's conclusion, or a plain statement that there is none yet. */
export function RootCauseCard({ rootCause }: { rootCause: RootCause | null }) {
  if (rootCause === null) {
    return (
      <Section title="Root cause">
        <p className="text-muted-foreground">
          No diagnosis yet. The Investigator has not concluded on this incident.
        </p>
      </Section>
    );
  }
  return (
    <Section title="Root cause">
      {rootCause.partial && (
        <p role="note" className="border-warning/40 text-warning rounded-md border px-3 py-2">
          Best guess only. The investigation ran out of budget before it could confirm this.
        </p>
      )}
      {rootCause.explanation && <p className="leading-relaxed">{rootCause.explanation}</p>}
      <dl className="flex flex-col gap-2">
        {rootCause.node && (
          <Fact term="Where">
            <code className="text-xs break-all">{rootCause.node}</code>
          </Fact>
        )}
        <Fact term="Confidence">{formatConfidence(rootCause.confidence)}</Fact>
        {rootCause.fix_location && (
          <Fact term="Fix location">
            <Badge variant={rootCause.fix_location === "dbt" ? "default" : "secondary"}>
              {rootCause.fix_location === "dbt" ? "In dbt" : "Outside dbt"}
            </Badge>
            {rootCause.fix_reason && <span className="ml-2">{rootCause.fix_reason}</span>}
          </Fact>
        )}
        {rootCause.evidence_ids.length > 0 && (
          <Fact term="Supported by">
            <ul className="flex flex-wrap gap-x-3">
              {rootCause.evidence_ids.map((number) => (
                <li key={number}>
                  <a
                    href={`#evidence-${String(number)}`}
                    className="text-primary underline-offset-4 hover:underline"
                  >
                    Evidence {number}
                  </a>
                </li>
              ))}
            </ul>
          </Fact>
        )}
      </dl>
      {rootCause.alternatives.length > 0 && (
        <div>
          <h3 className="mb-1 font-medium">Other causes considered</h3>
          <ul className="flex flex-col gap-2">
            {rootCause.alternatives.map((alternative) => (
              <li key={alternative.node} className="border-l-2 pl-3">
                <code className="text-xs break-all">{alternative.node}</code>
                <span className="text-muted-foreground">
                  {" "}
                  · {alternative.fault_class} · {formatConfidence(alternative.confidence)}
                </span>
                <p>{alternative.explanation}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Section>
  );
}
