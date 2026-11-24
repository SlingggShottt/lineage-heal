import { useMutation, useQueryClient } from "@tanstack/react-query";

import { expectOk } from "@/api/client";
import { useApiClient } from "@/api/context";
import { useMe } from "@/api/queries";

/** Sign out: ends the session on the server (which needs the CSRF token) and refreshes `/me`. */
export function useLogout() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  return useMutation({
    mutationFn: async () => {
      expectOk(
        await client.POST("/auth/logout", { headers: { "X-CSRF-Token": me?.csrf_token ?? "" } }),
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["me"] });
    },
  });
}
