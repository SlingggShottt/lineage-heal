import { useQueries } from "@tanstack/react-query";

import { unwrap } from "@/api/client";
import { useApiClient } from "@/api/context";
import type { components } from "@/api/schema";
import type { Status } from "@/features/incidents/labels";

type Incident = components["schemas"]["IncidentSummary"];

/** Statuses where a person or an agent still has something to do; the rest are finished. */
export const OPEN_STATUSES: readonly Status[] = [
  "detected",
  "investigating",
  "fix_proposed",
  "fix_unverified",
  "suggestion_filed",
  "unresolved",
];

const SHOWN = 5;

/**
 * How many incidents are open and the newest few. The list endpoint filters by one status, so
 * this asks once per open status for its newest few and merges them; the counts are exact and no
 * endpoint had to change.
 */
export function useOpenIncidents() {
  const client = useApiClient();
  return useQueries({
    queries: OPEN_STATUSES.map((status) => ({
      queryKey: ["incidents", { status, page: 1, overview: true }],
      queryFn: async () =>
        unwrap(await client.GET("/incidents", { params: { query: { status, limit: SHOWN } } })),
    })),
    combine: (results) => ({
      isPending: results.some((r) => r.isPending),
      isError: results.some((r) => r.isError),
      error: results.find((r) => r.isError)?.error ?? null,
      total: results.reduce((sum, r) => sum + (r.data?.total ?? 0), 0),
      newest: results
        .flatMap((r): Incident[] => r.data?.items ?? [])
        .sort((a, b) => b.opened_at.localeCompare(a.opened_at))
        .slice(0, SHOWN),
      refetch: () => {
        results.forEach((r) => void r.refetch());
      },
    }),
  });
}
