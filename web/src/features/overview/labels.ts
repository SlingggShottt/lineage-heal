import type { components } from "@/api/schema";

export type FeedStatus = components["schemas"]["FeedHealth"]["status"];

// A Record over the API's own values, so a new status stops the build until it has a label.
export const FEED_STATUS_LABELS: Record<FeedStatus, string> = {
  ok: "Up to date",
  late: "Late",
  failing: "Failing",
  no_data: "No data yet",
};
