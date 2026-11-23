import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { DiffView } from "./DiffView";
import { SandboxVerdict } from "./SandboxVerdict";
import { Section } from "./Section";
import { SuggestionView } from "./SuggestionView";
import { isNotFound, useIncidentFix } from "./useIncident";

type FixSummary = components["schemas"]["FixSummary"];

const STATUS_LABELS: Record<string, string> = {
  unverified: "Unverified",
  verified: "Verified",
  published: "Published",
  merged: "Merged",
  closed: "Closed",
};

function Attempts({ attempts }: { attempts: FixSummary[] }) {
  return (
    <div>
      <h3 className="mb-1 font-medium">Attempts</h3>
      <ol className="text-muted-foreground list-decimal pl-5">
        {attempts.map((attempt, index) => (
          <li key={attempt.id}>
            Attempt {index + 1}:{" "}
            {attempt.sandbox_passed === null
              ? "not checked in the sandbox"
              : attempt.sandbox_passed
                ? "passed the sandbox"
                : "failed the sandbox"}
          </li>
        ))}
      </ol>
    </div>
  );
}

/** The proposed change: a patch with its sandbox verdict, or a suggestion for a cause outside dbt. */
export function FixCard({ incidentId, attempts }: { incidentId: number; attempts: FixSummary[] }) {
  const { data, error, isPending, isError, refetch } = useIncidentFix(incidentId);

  if (isPending) {
    return (
      <Section title="Proposed fix">
        <Skeleton className="h-24 w-full" />
      </Section>
    );
  }
  if (isError) {
    return (
      <Section title="Proposed fix">
        {isNotFound(error) ? (
          <p className="text-muted-foreground">No fix has been proposed yet.</p>
        ) : (
          <>
            <p role="alert">The fix could not be loaded.</p>
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

  const isPatch = data.kind === "dbt_patch";
  const label = isPatch ? "Pull request" : "Issue";
  return (
    <Section
      title="Proposed fix"
      description={
        isPatch ? "A dbt patch checked in a sandbox." : "A suggested change for a person to make."
      }
    >
      <p className="flex flex-wrap items-center gap-2">
        <Badge
          variant={
            data.status === "verified" || data.status === "published" ? "success" : "secondary"
          }
        >
          {STATUS_LABELS[data.status] ?? data.status}
        </Badge>
        {data.github_url && data.github_number !== null && (
          <a
            href={data.github_url}
            target="_blank"
            rel="noreferrer"
            className="text-primary underline-offset-4 hover:underline"
          >
            {label} #{data.github_number}
          </a>
        )}
      </p>
      {isPatch && data.files.length > 0 && (
        <div>
          <h3 className="mb-1 font-medium">Files changed</h3>
          <ul>
            {data.files.map((file) => (
              <li key={file}>
                <code className="text-xs break-all">{file}</code>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.sandbox && <SandboxVerdict sandbox={data.sandbox} />}
      {data.patch && <DiffView diff={data.patch} />}
      {data.suggestion && <SuggestionView suggestion={data.suggestion} />}
      {attempts.length > 1 && <Attempts attempts={attempts} />}
    </Section>
  );
}
