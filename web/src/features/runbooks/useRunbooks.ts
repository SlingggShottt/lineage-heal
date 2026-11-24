import { useQuery } from "@tanstack/react-query";

import { unwrap } from "@/api/client";
import { useApiClient } from "@/api/context";

// The project has five runbooks, one per fault class; this is the API's page size cap, so the
// screen needs no pager unless the set grows past it.
const ALL = 100;

/** Every runbook, by name. */
export function useRunbooks() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["runbooks"],
    queryFn: async () =>
      unwrap(await client.GET("/runbooks", { params: { query: { limit: ALL } } })),
  });
}

/** One runbook with its sections. */
export function useRunbook(slug: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["runbook", slug],
    queryFn: async () =>
      unwrap(await client.GET("/runbooks/{slug}", { params: { path: { slug } } })),
  });
}
