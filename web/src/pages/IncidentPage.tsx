import { useParams } from "react-router";

import { ApiProblem } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EvidenceList } from "@/features/incidents/detail/EvidenceList";
import { FixCard } from "@/features/incidents/detail/FixCard";
import { IncidentHeader } from "@/features/incidents/detail/IncidentHeader";
import { InvestigationCard } from "@/features/incidents/detail/InvestigationCard";
import { KnowledgeCard } from "@/features/incidents/detail/KnowledgeCard";
import { LineageCard } from "@/features/incidents/detail/LineageCard";
import { RootCauseCard } from "@/features/incidents/detail/RootCauseCard";
import { SignalsCard } from "@/features/incidents/detail/SignalsCard";
import { isIncidentId, isNotFound, useIncident } from "@/features/incidents/detail/useIncident";
import { Link } from "react-router";

/** One incident: what failed, why, what it touches and what is proposed to fix it. */
export function IncidentPage() {
  const id = Number(useParams().id);
  const { data, error, isPending, isError, refetch } = useIncident(id);

  if (!isIncidentId(id) || (isError && isNotFound(error))) {
    return <NotFound />;
  }
  if (isPending) {
    return (
      <div role="status" aria-label="Loading incident" className="flex flex-col gap-3">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <Card role="alert">
        <CardHeader>
          <CardTitle>The incident could not be loaded</CardTitle>
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
    );
  }

  const citedEvidence = data.root_cause?.evidence_ids ?? [];
  return (
    <>
      <IncidentHeader incident={data} />
      <div className="flex flex-col gap-6">
        <LineageCard incidentId={id} />
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <RootCauseCard rootCause={data.root_cause} />
          <SignalsCard signals={data.signals} />
        </div>
        <FixCard incidentId={id} attempts={data.fixes} />
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <EvidenceList evidence={data.investigation?.evidence ?? []} cited={citedEvidence} />
          <div className="flex min-w-0 flex-col gap-6">
            <KnowledgeCard runbooks={data.related_runbooks} incidents={data.similar_incidents} />
            <InvestigationCard investigation={data.investigation} />
          </div>
        </div>
      </div>
    </>
  );
}

function NotFound() {
  return (
    <>
      <h1 className="mb-2 text-2xl font-semibold">Incident not found</h1>
      <p className="text-muted-foreground">
        There is no incident at this address.{" "}
        <Link to="/incidents" className="text-primary underline">
          Back to all incidents
        </Link>
      </p>
    </>
  );
}
