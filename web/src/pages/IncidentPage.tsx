import { Link, useParams } from "react-router";

import { Placeholder } from "./Placeholder";

/** One incident. The full page (root cause, evidence, lineage, fix) is LH-064. */
export function IncidentPage() {
  const { id } = useParams();
  return (
    <>
      <p className="mb-4 text-sm">
        <Link to="/incidents" className="text-primary underline-offset-4 hover:underline">
          ← All incidents
        </Link>
      </p>
      <Placeholder
        title={`Incident #${id ?? ""}`}
        description="The root cause, evidence, lineage graph and proposed fix."
      />
    </>
  );
}
