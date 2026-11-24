import { FeedsCard } from "@/features/overview/FeedsCard";
import { OpenIncidentsCard } from "@/features/overview/OpenIncidentsCard";
import { RunCard } from "@/features/overview/RunCard";

/** Where the pipeline stands: feed freshness, the latest run and what is open. */
export function OverviewPage() {
  return (
    <>
      <h1 className="mb-6 text-2xl font-semibold">Overview</h1>
      <div className="grid gap-4 lg:grid-cols-2">
        <FeedsCard />
        <RunCard />
        <OpenIncidentsCard />
      </div>
    </>
  );
}
