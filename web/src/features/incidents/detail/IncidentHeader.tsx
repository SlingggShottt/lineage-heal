import { Link } from "react-router";

import type { components } from "@/api/schema";
import { exactTime, formatConfidence, relativeTime } from "@/lib/format";

import { FaultClassBadge, SeverityBadge, StatusBadge } from "../badges";

type Incident = components["schemas"]["IncidentDetail"];

function When({ label, iso }: { label: string; iso: string }) {
  return (
    <span>
      {label}{" "}
      <time dateTime={iso} title={exactTime(iso)}>
        {relativeTime(iso)}
      </time>
    </span>
  );
}

/** What the incident is, at a glance: the summary, its state and how sure the diagnosis is. */
export function IncidentHeader({ incident }: { incident: Incident }) {
  return (
    <header className="mb-6">
      <p className="mb-3 text-sm">
        <Link to="/incidents" className="text-primary underline-offset-4 hover:underline">
          ← All incidents
        </Link>
      </p>
      <p className="text-muted-foreground text-sm">Incident #{incident.id}</p>
      <h1 className="mb-3 text-2xl font-semibold break-words">{incident.summary}</h1>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <StatusBadge status={incident.status} />
        <SeverityBadge severity={incident.severity} />
        <FaultClassBadge faultClass={incident.fault_class} />
      </div>
      <p className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <span>Confidence {formatConfidence(incident.confidence)}</span>
        {incident.classified_by && <span>Classified by {incident.classified_by}</span>}
        <When label="Opened" iso={incident.opened_at} />
        <When label="Updated" iso={incident.updated_at} />
      </p>
    </header>
  );
}
