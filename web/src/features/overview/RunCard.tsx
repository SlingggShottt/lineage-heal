import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { exactTime, relativeTime } from "@/lib/format";

import { LoadError } from "./LoadError";
import { useFeedsHealth } from "./useFeedsHealth";

function count(n: number, noun: string): string {
  return `${String(n)} ${noun}${n === 1 ? "" : "s"}`;
}

/** The newest dbt run: did it pass, when, and how many problems it raised. */
export function RunCard() {
  const { data, error, isPending, isError, refetch } = useFeedsHealth();
  const run = data?.latest_run ?? null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Latest pipeline run</CardTitle>
        <CardDescription>The most recent dbt build and its test results.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending && <Skeleton role="status" aria-label="Loading the run" className="h-12" />}
        {isError && (
          <LoadError
            what="The run"
            error={error}
            onRetry={() => {
              void refetch();
            }}
          />
        )}
        {data && !run && <p className="text-muted-foreground text-sm">No run recorded yet.</p>}
        {run && (
          <div className="flex flex-col gap-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={run.status === "failed" ? "destructive" : "success"}>
                {run.status === "failed" ? "Failed" : "Succeeded"}
              </Badge>
              <time dateTime={run.generated_at} title={exactTime(run.generated_at)}>
                {relativeTime(run.generated_at)}
              </time>
            </div>
            <p>
              {count(run.errors, "error")}, {count(run.warnings, "warning")},{" "}
              {count(run.skipped_models, "model")} skipped
            </p>
            <p className="text-muted-foreground font-mono text-xs">{run.run_id}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
