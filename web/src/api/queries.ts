import { useQuery } from "@tanstack/react-query";

import { unwrap } from "./client";
import { useApiClient } from "./context";

/** Who is signed in (or nobody), and whether they may approve. */
export function useMe() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await client.GET("/me")),
    staleTime: 30_000,
  });
}
