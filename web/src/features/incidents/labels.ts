import type { paths } from "@/api/schema";

type Query = NonNullable<paths["/incidents"]["get"]["parameters"]["query"]>;

export type Status = NonNullable<Query["status"]>;
export type FaultClass = NonNullable<Query["fault_class"]>;
export type Severity = NonNullable<Query["severity"]>;
export type Feed = NonNullable<Query["feed"]>;

// Each map is a Record over the API's own values, so when the API gains a value and the types
// are regenerated, this file stops compiling until the new value has a label.
export const STATUS_LABELS: Record<Status, string> = {
  detected: "Detected",
  investigating: "Investigating",
  fix_proposed: "Fix proposed",
  suggestion_filed: "Suggestion filed",
  fix_unverified: "Fix unverified",
  unresolved: "Unresolved",
  approved: "Approved",
  rejected: "Rejected",
  resolved: "Resolved",
};

export const FAULT_CLASS_LABELS: Record<FaultClass, string> = {
  schema_drift: "Schema drift",
  nulls: "Nulls",
  duplicates: "Duplicates",
  unit_change: "Unit change",
  late_data: "Late data",
  unknown: "Unknown",
};

export const SEVERITY_LABELS: Record<Severity, string> = {
  error: "Error",
  warn: "Warning",
};

export const FEED_LABELS: Record<Feed, string> = {
  amfi: "AMFI",
  nse: "NSE",
  fbil: "FBIL",
};
