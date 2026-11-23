import { useMe } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Shows who is signed in, or a link to sign in with GitHub.
 *
 * Signing out needs the CSRF token on a POST and arrives with the review controls (LH-065).
 */
export function SessionBadge() {
  const { data, isPending, isError } = useMe();

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
    <div className="flex items-center gap-2">
      <span className="text-sm font-medium">{data.login}</span>
      <Badge variant={data.is_approver ? "success" : "secondary"}>
        {data.is_approver ? "Approver" : "Read-only"}
      </Badge>
    </div>
  );
}
