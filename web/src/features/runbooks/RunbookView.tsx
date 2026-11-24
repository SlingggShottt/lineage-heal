import { Link } from "react-router";

import { ApiProblem } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadError } from "@/features/overview/LoadError";

import { faultClassLabel } from "./faultClass";
import { Markdown } from "./markdown";
import { useRunbook } from "./useRunbooks";

/** One runbook, section by section in the order the file has them. */
export function RunbookView({ slug }: { slug: string }) {
  const { data, error, isPending, isError, refetch } = useRunbook(slug);
  const missing = error instanceof ApiProblem && error.status === 404;
  return (
    <>
      <p className="mb-4 text-sm">
        <Link to="/runbooks" className="text-primary underline-offset-4 hover:underline">
          All runbooks
        </Link>
      </p>
      {isPending && (
        <div role="status" aria-label="Loading the runbook" className="flex flex-col gap-2">
          <Skeleton className="h-8 w-1/2" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}
      {missing && (
        <>
          <h1 className="mb-2 text-2xl font-semibold">Runbook not found</h1>
          <p className="text-muted-foreground">There is no runbook with this name.</p>
        </>
      )}
      {isError && !missing && (
        <LoadError
          what="The runbook"
          error={error}
          onRetry={() => {
            void refetch();
          }}
        />
      )}
      {data && (
        <>
          <h1 className="mb-2 text-2xl font-semibold">{data.title}</h1>
          <p className="text-muted-foreground mb-6 flex flex-wrap items-center gap-2 text-sm">
            <Badge variant="outline">{faultClassLabel(data.fault_class)}</Badge>
            Feeds: {data.feeds}
          </p>
          <div className="flex flex-col gap-4">
            {data.sections.map((section) => (
              <Card key={section.heading}>
                <CardHeader>
                  <CardTitle>{section.heading}</CardTitle>
                  <CardDescription className="sr-only">
                    {section.heading} section of {data.title}
                  </CardDescription>
                </CardHeader>
                <CardContent className="text-sm">
                  <Markdown text={section.text} />
                </CardContent>
              </Card>
            ))}
          </div>
        </>
      )}
    </>
  );
}
