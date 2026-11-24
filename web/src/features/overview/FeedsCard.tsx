import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { LoadError } from "./LoadError";
import { FEED_STATUS_LABELS, type FeedStatus } from "./labels";
import { useFeedsHealth } from "./useFeedsHealth";

type Variant = NonNullable<React.ComponentProps<typeof Badge>["variant"]>;

const VARIANTS: Record<FeedStatus, Variant> = {
  ok: "success",
  late: "warning",
  failing: "destructive",
  no_data: "secondary",
};

function lag(days: number | null): string {
  if (days === null) {
    return "—";
  }
  return days === 0 ? "current" : `${String(days)} ${days === 1 ? "day" : "days"} behind`;
}

/** Whether each source feed is arriving, judged against the newest date any feed has. */
export function FeedsCard() {
  const { data, error, isPending, isError, refetch } = useFeedsHealth();
  return (
    <Card>
      <CardHeader>
        <CardTitle>Feeds</CardTitle>
        <CardDescription>Newest stored file for each dataset.</CardDescription>
      </CardHeader>
      <CardContent>
        {isPending && (
          <div role="status" aria-label="Loading feeds" className="flex flex-col gap-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}
        {isError && (
          <LoadError
            what="Feeds"
            error={error}
            onRetry={() => {
              void refetch();
            }}
          />
        )}
        {data && (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Feed freshness</caption>
            <thead className="text-muted-foreground text-xs">
              <tr>
                <th scope="col" className="py-1 pr-2 font-medium">
                  Dataset
                </th>
                <th scope="col" className="py-1 pr-2 font-medium">
                  Status
                </th>
                <th scope="col" className="py-1 pr-2 font-medium">
                  Newest day
                </th>
                <th scope="col" className="py-1 font-medium">
                  Lag
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.feeds.map((feed) => (
                <tr key={`${feed.source}/${feed.dataset}`}>
                  <td className="py-2 pr-2 font-mono text-xs">
                    {feed.source}/{feed.dataset}
                  </td>
                  <td className="py-2 pr-2">
                    <Badge variant={VARIANTS[feed.status]}>{FEED_STATUS_LABELS[feed.status]}</Badge>
                    {feed.last_error && (
                      <p className="text-muted-foreground mt-1 text-xs">{feed.last_error}</p>
                    )}
                  </td>
                  <td className="py-2 pr-2 whitespace-nowrap">{feed.latest_trade_date ?? "—"}</td>
                  <td className="py-2 whitespace-nowrap">{lag(feed.lag_days)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
