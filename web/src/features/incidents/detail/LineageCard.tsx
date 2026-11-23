import { ApiProblem } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { isRootOnly, pathSteps } from "./lineagePath";
import { Section } from "./Section";
import { isNotFound, useIncidentLineage } from "./useIncident";

type Role = components["schemas"]["LineageNodeOut"]["role"];

const ROLE_LABELS: Record<Role, string> = {
  root: "Root cause",
  affected: "Affected",
  path: "On the path",
};
const ROLE_VARIANTS = { root: "destructive", affected: "warning", path: "outline" } as const;

/**
 * The path from the root cause to what it broke, as an ordered list.
 *
 * This is the text form of the graph; it stays as the accessible alternative when the graph is
 * added (LH-102). Nodes are listed in flow order, each with what it feeds next.
 */
export function LineageCard({ incidentId }: { incidentId: number }) {
  const { data, error, isPending, isError, refetch } = useIncidentLineage(incidentId);

  if (isPending) {
    return (
      <Section title="Lineage">
        <Skeleton className="h-16 w-full" />
      </Section>
    );
  }
  if (isError) {
    return (
      <Section title="Lineage">
        {isNotFound(error) ? (
          <p className="text-muted-foreground">
            No lineage can be drawn for this incident.{" "}
            {error instanceof ApiProblem ? error.message : ""}
          </p>
        ) : (
          <>
            <p role="alert">Lineage could not be loaded.</p>
            <div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void refetch();
                }}
              >
                Try again
              </Button>
            </div>
          </>
        )}
      </Section>
    );
  }

  const steps = pathSteps(data);
  return (
    <Section title="Lineage" description="From the root cause to the columns and tables it broke.">
      {isRootOnly(data) ? (
        <p className="text-muted-foreground">
          No path from the root cause to the affected tables could be traced, so only the root is
          shown. This is usual for a missing file, which feeds no column.
        </p>
      ) : (
        <ol className="flex flex-col gap-3">
          {steps.map(({ node, feeds }) => (
            <li key={node.id} className="border-l-2 pl-3">
              <p className="flex flex-wrap items-center gap-2">
                <code className="text-xs break-all">{node.id}</code>
                <Badge variant={ROLE_VARIANTS[node.role]}>{ROLE_LABELS[node.role]}</Badge>
                <span className="text-muted-foreground text-xs">{node.kind}</span>
              </p>
              {feeds.length > 0 && (
                <ul className="text-muted-foreground mt-1 text-xs">
                  {feeds.map((edge) => (
                    <li key={edge.target}>
                      feeds <code className="break-all">{edge.target}</code> ({edge.kind}
                      {edge.inferred ? ", inferred" : ""})
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
      {data.unreached.length > 0 && (
        <p className="text-muted-foreground">
          Not reached from the root: {data.unreached.join(", ")}.
        </p>
      )}
      {data.truncated && (
        <p className="text-muted-foreground">
          Only the first part of a very wide incident is shown.
        </p>
      )}
      <p className="text-muted-foreground text-xs">Graph version {data.version}</p>
    </Section>
  );
}
