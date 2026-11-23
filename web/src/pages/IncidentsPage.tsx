import { useSearchParams } from "react-router";

import { ApiProblem } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { IncidentFilters } from "@/features/incidents/IncidentFilters";
import { IncidentsTable } from "@/features/incidents/IncidentsTable";
import { PAGE_SIZE, parseFilters, toSearchParams } from "@/features/incidents/filters";
import type { IncidentFilters as Filters } from "@/features/incidents/filters";
import { Pager } from "@/features/incidents/Pager";
import { useIncidents } from "@/features/incidents/useIncidents";

/** Detected failures, newest first, with filters and pages kept in the address. */
export function IncidentsPage() {
  const [params, setParams] = useSearchParams();
  const filters = parseFilters(params);
  const { data, error, isPending, isError, isFetching, refetch } = useIncidents(filters);
  const show = (next: Filters) => {
    setParams(toSearchParams(next));
  };
  const filtered = Object.keys(filters).some((key) => key !== "page");

  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">Incidents</h1>
      <IncidentFilters filters={filters} onChange={show} />
      <div aria-busy={isFetching}>
        {isPending && (
          <div role="status" aria-label="Loading incidents" className="flex flex-col gap-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        )}
        {isError && (
          <Card role="alert">
            <CardHeader>
              <CardTitle>Incidents could not be loaded</CardTitle>
              <CardDescription>
                {error instanceof ApiProblem ? error.message : "The API could not be reached."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void refetch();
                }}
              >
                Try again
              </Button>
            </CardContent>
          </Card>
        )}
        {data &&
          (data.items.length > 0 ? (
            <>
              <IncidentsTable incidents={data.items} />
              <Pager
                page={filters.page}
                pageSize={PAGE_SIZE}
                total={data.total}
                onPage={(page) => {
                  show({ ...filters, page });
                }}
              />
            </>
          ) : (
            <Card>
              <CardHeader>
                <CardTitle>{emptyTitle(filtered, data.total)}</CardTitle>
                <CardDescription>{emptyHelp(filtered, data.total)}</CardDescription>
              </CardHeader>
              {(filtered || data.total > 0) && (
                <CardContent>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      show(data.total > 0 ? { ...filters, page: 1 } : { page: 1 });
                    }}
                  >
                    {data.total > 0 ? "Go to the first page" : "Clear filters"}
                  </Button>
                </CardContent>
              )}
            </Card>
          ))}
      </div>
    </>
  );
}

function emptyTitle(filtered: boolean, total: number): string {
  if (total > 0) {
    return "There is nothing on this page";
  }
  return filtered ? "No incidents match these filters" : "No incidents yet";
}

function emptyHelp(filtered: boolean, total: number): string {
  if (total > 0) {
    return `There are ${String(total)} incidents, but not this many pages of them.`;
  }
  return filtered
    ? "Try a different combination, or clear the filters."
    : "When a pipeline run fails, the incident appears here.";
}
