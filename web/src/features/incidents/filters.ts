import type { paths } from "@/api/schema";

import {
  FAULT_CLASS_LABELS,
  FEED_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  type FaultClass,
  type Feed,
  type Severity,
  type Status,
} from "./labels";

export const PAGE_SIZE = 25;

/** What the incidents page is showing: up to four filters and a 1-based page. */
export type IncidentFilters = {
  status?: Status;
  fault_class?: FaultClass;
  severity?: Severity;
  feed?: Feed;
  page: number;
};

type FilterKey = Exclude<keyof IncidentFilters, "page">;
type ListQuery = NonNullable<paths["/incidents"]["get"]["parameters"]["query"]>;

const FILTER_KEYS: readonly FilterKey[] = ["status", "fault_class", "severity", "feed"];

/** The values each filter accepts, from the labels, which cover every value the API has. */
const ALLOWED: Record<FilterKey, readonly string[]> = {
  status: Object.keys(STATUS_LABELS),
  fault_class: Object.keys(FAULT_CLASS_LABELS),
  severity: Object.keys(SEVERITY_LABELS),
  feed: Object.keys(FEED_LABELS),
};

/**
 * Read the filters from a URL's query string.
 *
 * A value the API would refuse, or a page that is not a positive whole number, is ignored
 * rather than passed on, so a mistyped or stale link shows the unfiltered list instead of an error.
 */
export function parseFilters(params: URLSearchParams): IncidentFilters {
  const filters: IncidentFilters = { page: 1 };
  for (const key of FILTER_KEYS) {
    const value = params.get(key);
    if (value !== null && ALLOWED[key].includes(value)) {
      Object.assign(filters, { [key]: value });
    }
  }
  const page = Number(params.get("page"));
  if (Number.isInteger(page) && page >= 1) {
    filters.page = page;
  }
  return filters;
}

/** The query string for these filters; defaults are left out so the plain list has a plain URL. */
export function toSearchParams(filters: IncidentFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value !== undefined) {
      params.set(key, value);
    }
  }
  if (filters.page > 1) {
    params.set("page", String(filters.page));
  }
  return params;
}

/** The parameters of the API request for these filters. */
export function toApiQuery(filters: IncidentFilters): ListQuery {
  const query: ListQuery = { limit: PAGE_SIZE, offset: (filters.page - 1) * PAGE_SIZE };
  for (const key of FILTER_KEYS) {
    const value = filters[key];
    if (value !== undefined) {
      Object.assign(query, { [key]: value });
    }
  }
  return query;
}

/** Change one filter (or clear it with `undefined`). The page goes back to 1. */
export function withFilter(
  filters: IncidentFilters,
  key: FilterKey,
  value: string | undefined,
): IncidentFilters {
  const next: IncidentFilters = { ...filters, page: 1 };
  if (value === undefined || value === "") {
    // eslint-disable-next-line @typescript-eslint/no-dynamic-delete
    delete next[key];
  } else {
    Object.assign(next, { [key]: value });
  }
  return next;
}
