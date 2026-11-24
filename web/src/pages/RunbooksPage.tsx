import { useParams } from "react-router";

import { RunbookList } from "@/features/runbooks/RunbookList";
import { RunbookView } from "@/features/runbooks/RunbookView";

/** The written procedures the Investigator retrieves, one per fault class. */
export function RunbooksPage() {
  const { slug } = useParams();
  if (slug) {
    return <RunbookView slug={slug} />;
  }
  return (
    <>
      <h1 className="mb-2 text-2xl font-semibold">Runbooks</h1>
      <p className="text-muted-foreground mb-6 text-sm">
        The written procedures the Investigator retrieves for each fault class.
      </p>
      <RunbookList />
    </>
  );
}
