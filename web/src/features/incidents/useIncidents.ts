import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { unwrap } from "@/api/client";
import { useApiClient } from "@/api/context";

import { toApiQuery, type IncidentFilters } from "./filters";

/** One page of incidents. The previous page stays on screen while the next one loads. */
export function useIncidents(filters: IncidentFilters) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["incidents", filters],
    queryFn: async () =>
      unwrap(await client.GET("/incidents", { params: { query: toApiQuery(filters) } })),
    placeholderData: keepPreviousData,
  });
}
