import { Link } from "react-router";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SeverityBadge, StatusBadge } from "@/features/incidents/badges";
import { exactTime, relativeTime } from "@/lib/format";

import { LoadError } from "./LoadError";
import { useOpenIncidents } from "./useOpenIncidents";

/** Incidents still waiting on an agent or a person, newest first, linking to each. */
export function OpenIncidentsCard() {
  const open = useOpenIncidents();
  return (
    <Card className="lg:col-span-2">
      <CardHeader>
        <CardTitle>Open incidents</CardTitle>
        <CardDescription>
          {open.isPending || open.isError
            ? "Failures that are not yet resolved."
            : `${String(open.total)} open`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {open.isPending && !open.isError && (
          <div role="status" aria-label="Loading incidents" className="flex flex-col gap-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}
        {open.isError && <LoadError what="Incidents" error={open.error} onRetry={open.refetch} />}
        {!open.isPending && !open.isError && open.newest.length === 0 && (
          <p className="text-muted-foreground text-sm">No open incidents.</p>
        )}
        {!open.isPending && !open.isError && open.newest.length > 0 && (
          <ul className="divide-y text-sm">
            {open.newest.map((incident) => (
              <li key={incident.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                <Link
                  to={`/incidents/${String(incident.id)}`}
                  className="text-primary min-w-0 flex-1 font-medium underline-offset-4 hover:underline"
                >
                  {incident.summary}
                </Link>
                <StatusBadge status={incident.status} />
                <SeverityBadge severity={incident.severity} />
                <time
                  dateTime={incident.opened_at}
                  title={exactTime(incident.opened_at)}
                  className="text-muted-foreground whitespace-nowrap"
                >
                  {relativeTime(incident.opened_at)}
                </time>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-sm">
          <Link to="/incidents" className="text-primary underline-offset-4 hover:underline">
            All incidents
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
