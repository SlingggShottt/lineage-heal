import type { components } from "@/api/schema";
import { exactTime } from "@/lib/format";

import { Fact, Section } from "./Section";

type Investigation = components["schemas"]["InvestigationOut"];

/** The record of the run: how long, how many model calls, which models. */
export function InvestigationCard({ investigation }: { investigation: Investigation | null }) {
  if (investigation === null) {
    return (
      <Section title="Investigation">
        <p className="text-muted-foreground">Not investigated yet.</p>
      </Section>
    );
  }
  const models = Object.entries(investigation.models);
  return (
    <Section title="Investigation">
      <dl className="flex flex-col gap-2">
        <Fact term="Status">{investigation.status.replace(/_/g, " ")}</Fact>
        <Fact term="Started">
          <time dateTime={investigation.started_at}>{exactTime(investigation.started_at)}</time>
        </Fact>
        {investigation.finished_at && (
          <Fact term="Finished">
            <time dateTime={investigation.finished_at}>{exactTime(investigation.finished_at)}</time>
          </Fact>
        )}
        <Fact term="Model calls">{investigation.llm_calls}</Fact>
        <Fact term="Tokens">
          {investigation.input_tokens} in · {investigation.output_tokens} out
        </Fact>
        {models.length > 0 && (
          <Fact term="Models">
            {models.map(([prompt, model]) => `${prompt}: ${model}`).join(", ")}
          </Fact>
        )}
        {investigation.trace_url && (
          <Fact term="Trace">
            <a
              href={investigation.trace_url}
              target="_blank"
              rel="noreferrer"
              className="text-primary underline-offset-4 hover:underline"
            >
              Open the agent trace
            </a>
          </Fact>
        )}
        {investigation.error && <Fact term="Error">{investigation.error}</Fact>}
      </dl>
    </Section>
  );
}
