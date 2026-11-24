import { useMutation, useQueryClient } from "@tanstack/react-query";

import { unwrap } from "@/api/client";
import { useApiClient } from "@/api/context";
import { useMe } from "@/api/queries";

/** After a decision the incident, its fix and the list all change, so they are read again. */
function useRefreshAfterDecision(incidentId: number) {
  const queryClient = useQueryClient();
  return async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["incident", incidentId] }),
      queryClient.invalidateQueries({ queryKey: ["incidents"] }),
    ]);
  };
}

/** Approve and squash-merge the fix. The server uses the approver's own GitHub token. */
export function useApprove(incidentId: number) {
  const client = useApiClient();
  const { data: me } = useMe();
  const refresh = useRefreshAfterDecision(incidentId);
  return useMutation({
    mutationFn: async () =>
      unwrap(
        await client.POST("/incidents/{incident_id}/approve", {
          params: { path: { incident_id: incidentId } },
          headers: { "X-CSRF-Token": me?.csrf_token ?? "" },
        }),
      ),
    onSuccess: refresh,
  });
}

/** Close the fix's pull request unmerged, with the reason posted on it. */
export function useReject(incidentId: number) {
  const client = useApiClient();
  const { data: me } = useMe();
  const refresh = useRefreshAfterDecision(incidentId);
  return useMutation({
    mutationFn: async (reason: string) =>
      unwrap(
        await client.POST("/incidents/{incident_id}/reject", {
          params: { path: { incident_id: incidentId } },
          headers: { "X-CSRF-Token": me?.csrf_token ?? "" },
          body: { reason },
        }),
      ),
    onSuccess: refresh,
  });
}
