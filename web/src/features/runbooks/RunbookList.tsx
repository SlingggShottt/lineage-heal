import { Link } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadError } from "@/features/overview/LoadError";

import { faultClassLabel } from "./faultClass";
import { useRunbooks } from "./useRunbooks";

/** Every runbook with its fault class and the feeds it applies to. */
export function RunbookList() {
  const { data, error, isPending, isError, refetch } = useRunbooks();
  return (
    <>
      {isPending && (
        <div role="status" aria-label="Loading runbooks" className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      )}
      {isError && (
        <Card>
          <CardContent className="pt-6">
            <LoadError
              what="Runbooks"
              error={error}
              onRetry={() => {
                void refetch();
              }}
            />
          </CardContent>
        </Card>
      )}
      {data && data.items.length === 0 && (
        <p className="text-muted-foreground text-sm">There are no runbooks on this server.</p>
      )}
      {data && data.items.length > 0 && (
        <ul className="grid gap-4 md:grid-cols-2">
          {data.items.map((runbook) => (
            <li key={runbook.slug}>
              <Card className="h-full">
                <CardHeader>
                  <CardTitle>
                    <Link
                      to={`/runbooks/${runbook.slug}`}
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      {runbook.title}
                    </Link>
                  </CardTitle>
                  <CardDescription>Feeds: {runbook.feeds}</CardDescription>
                </CardHeader>
                <CardContent>
                  <Badge variant="outline">{faultClassLabel(runbook.fault_class)}</Badge>
                </CardContent>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
