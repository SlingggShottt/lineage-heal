import { useQuery } from "@tanstack/react-query";

import { ApiProblem, unwrap } from "@/api/client";
import { useApiClient } from "@/api/context";

/** A "not found" will not change by asking again, so only other failures are retried, once. */
function retryUnlessNotFound(failures: number, error: Error): boolean {
  return !(error instanceof ApiProblem && error.status === 404) && failures < 1;
}

/** True when the API answered that the thing is not there. */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiProblem && error.status === 404;
}

/** An address like /incidents/abc or /incidents/0 names no incident, so nothing is requested. */
export function isIncidentId(id: number): boolean {
  return Number.isSafeInteger(id) && id > 0;
}

/** One incident with its diagnosis, signals, evidence and fixes in brief. */
export function useIncident(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["incident", id],
    queryFn: async () =>
      unwrap(
        await client.GET("/incidents/{incident_id}", { params: { path: { incident_id: id } } }),
      ),
    retry: retryUnlessNotFound,
    enabled: isIncidentId(id),
  });
}

/** The paths from the incident's root cause to what it broke. A 404 means none could be drawn. */
export function useIncidentLineage(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["incident", id, "lineage"],
    queryFn: async () =>
      unwrap(
        await client.GET("/incidents/{incident_id}/lineage", {
          params: { path: { incident_id: id } },
        }),
      ),
    retry: retryUnlessNotFound,
    enabled: isIncidentId(id),
  });
}

/** The newest fix with its diff and sandbox verdict. A 404 means there is no fix yet. */
export function useIncidentFix(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["incident", id, "fix"],
    queryFn: async () =>
      unwrap(
        await client.GET("/incidents/{incident_id}/fix", { params: { path: { incident_id: id } } }),
      ),
    retry: retryUnlessNotFound,
    enabled: isIncidentId(id),
  });
}
