import type { components } from "@/api/schema";

import { Section } from "./Section";

type Hit = components["schemas"]["KnowledgeHit"];

const EXCERPT_CHARACTERS = 360;

/**
 * A short plain-text excerpt. The stored text starts with the runbook's title and the section's
 * name (so a search can match them), which the card already shows, and holds Markdown markers.
 */
function excerpt(hit: Hit): string {
  const [first = "", ...rest] = hit.text.split("\n\n");
  const [, section] = first.split("\n");
  const body = section === hit.heading ? rest.join("\n\n") : hit.text;
  const plain = body
    .replace(/^- /gm, "• ")
    .replace(/\*\*|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return plain.length > EXCERPT_CHARACTERS ? `${plain.slice(0, EXCERPT_CHARACTERS)}…` : plain;
}

function HitList({ title, hits }: { title: string; hits: Hit[] }) {
  return (
    <div>
      <h3 className="mb-1 font-medium">{title}</h3>
      <ul className="flex flex-col gap-3">
        {hits.map((hit) => (
          <li key={`${hit.document}-${hit.heading}`} className="border-l-2 pl-3">
            <p className="font-medium">
              {hit.document} · {hit.heading}
            </p>
            <p className="text-muted-foreground">{excerpt(hit)}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The runbook sections and past incidents the Investigator found similar. */
export function KnowledgeCard({ runbooks, incidents }: { runbooks: Hit[]; incidents: Hit[] }) {
  return (
    <Section
      title="Related knowledge"
      description="What the Investigator retrieved before concluding."
    >
      {runbooks.length === 0 && incidents.length === 0 && (
        <p className="text-muted-foreground">Nothing similar was found.</p>
      )}
      {runbooks.length > 0 && <HitList title="Runbook sections" hits={runbooks} />}
      {incidents.length > 0 && <HitList title="Similar past incidents" hits={incidents} />}
    </Section>
  );
}
