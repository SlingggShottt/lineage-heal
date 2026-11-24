import { ApiProblem } from "@/api/client";
import { Button } from "@/components/ui/button";

/** What a card says when its own request failed; the other cards are unaffected. */
export function LoadError({
  what,
  error,
  onRetry,
}: {
  what: string;
  error: unknown;
  onRetry: () => void;
}) {
  return (
    <div role="alert" className="text-sm">
      <p className="font-medium">{what} could not be loaded</p>
      <p className="text-muted-foreground mb-3">
        {error instanceof ApiProblem ? error.message : "The API could not be reached."}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </div>
  );
}
