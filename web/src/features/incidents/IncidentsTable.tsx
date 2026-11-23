import { Link } from "react-router";

import type { components } from "@/api/schema";
import { exactTime, formatConfidence, relativeTime } from "@/lib/format";

import { FaultClassBadge, SeverityBadge, StatusBadge } from "./badges";

type Incident = components["schemas"]["IncidentSummary"];

/** The page's incidents, newest first. Each summary links to the incident. */
export function IncidentsTable({ incidents }: { incidents: Incident[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Incidents, newest first</caption>
        <thead className="bg-muted text-muted-foreground text-xs">
          <tr>
            <th scope="col" className="px-4 py-2 font-medium">
              ID
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Summary
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Status
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Severity
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Fault class
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Confidence
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Root cause
            </th>
            <th scope="col" className="px-4 py-2 font-medium">
              Opened
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {incidents.map((incident) => (
            <tr key={incident.id}>
              <td className="text-muted-foreground px-4 py-3 whitespace-nowrap">#{incident.id}</td>
              <td className="max-w-xs px-4 py-3">
                <Link
                  to={`/incidents/${String(incident.id)}`}
                  className="text-primary font-medium underline-offset-4 hover:underline"
                >
                  {incident.summary}
                </Link>
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={incident.status} />
              </td>
              <td className="px-4 py-3">
                <SeverityBadge severity={incident.severity} />
              </td>
              <td className="px-4 py-3">
                <FaultClassBadge faultClass={incident.fault_class} />
              </td>
              <td className="px-4 py-3 whitespace-nowrap tabular-nums">
                {formatConfidence(incident.confidence)}
              </td>
              <td className="px-4 py-3">
                {incident.root_node === null ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <code className="text-xs break-all">{incident.root_node}</code>
                )}
              </td>
              <td className="px-4 py-3 whitespace-nowrap">
                <time dateTime={incident.opened_at} title={exactTime(incident.opened_at)}>
                  {relativeTime(incident.opened_at)}
                </time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
