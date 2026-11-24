import { useMe } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useLogout } from "./useLogout";

/** Shows who is signed in with a way to sign out, or a link to sign in with GitHub. */
export function SessionBadge() {
  const { data, isPending, isError } = useMe();
  const logout = useLogout();

  if (isPending) {
    return <Skeleton className="h-8 w-40" />;
  }
  if (isError) {
    return (
      <Badge variant="warning" role="status">
        Sign-in status unavailable
      </Badge>
    );
  }
  if (!data.signed_in) {
    return (
      <Button asChild size="sm" variant="outline">
        <a href="/api/auth/login">Sign in with GitHub</a>
      </Button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-sm font-medium">{data.login}</span>
      <Badge variant={data.is_approver ? "success" : "secondary"}>
        {data.is_approver ? "Approver" : "Read-only"}
      </Badge>
      <Button
        variant="ghost"
        size="sm"
        disabled={logout.isPending}
        onClick={() => {
          logout.mutate();
        }}
      >
        Sign out
      </Button>
      {logout.isError && (
        <span role="alert" className="text-destructive text-xs">
          Could not sign out. Try again.
        </span>
      )}
    </div>
  );
}
