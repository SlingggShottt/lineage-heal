import { useQuery } from "@tanstack/react-query";

import { unwrap } from "@/api/client";
import { useApiClient } from "@/api/context";

/** Each feed's freshness and the newest pipeline run. */
export function useFeedsHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["feeds-health"],
    queryFn: async () => unwrap(await client.GET("/feeds/health")),
  });
}
