import { useId } from "react";

import { Button } from "@/components/ui/button";

import { withFilter, type IncidentFilters as Filters } from "./filters";
import { FAULT_CLASS_LABELS, FEED_LABELS, SEVERITY_LABELS, STATUS_LABELS } from "./labels";

type FilterBarProps = {
  filters: Filters;
  onChange: (next: Filters) => void;
};

type Field = {
  key: "status" | "fault_class" | "severity" | "feed";
  label: string;
  options: Record<string, string>;
};

const FIELDS: readonly Field[] = [
  { key: "status", label: "Status", options: STATUS_LABELS },
  { key: "fault_class", label: "Fault class", options: FAULT_CLASS_LABELS },
  { key: "severity", label: "Severity", options: SEVERITY_LABELS },
  { key: "feed", label: "Feed", options: FEED_LABELS },
];

/** Four filters as native selects: keyboard and screen-reader behaviour for free. */
export function IncidentFilters({ filters, onChange }: FilterBarProps) {
  const prefix = useId();
  const active = FIELDS.some(({ key }) => filters[key] !== undefined);
  return (
    <form
      role="search"
      aria-label="Filter incidents"
      className="mb-4 flex flex-wrap items-end gap-4"
      onSubmit={(event) => {
        event.preventDefault();
      }}
    >
      {FIELDS.map(({ key, label, options }) => {
        const id = `${prefix}-${key}`;
        return (
          <div key={key} className="flex flex-col gap-1">
            <label htmlFor={id} className="text-muted-foreground text-xs font-medium">
              {label}
            </label>
            <select
              id={id}
              value={filters[key] ?? ""}
              onChange={(event) => {
                onChange(withFilter(filters, key, event.target.value));
              }}
              className="bg-background h-9 min-w-40 rounded-md border px-3 text-sm"
            >
              <option value="">All</option>
              {Object.entries(options).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </select>
          </div>
        );
      })}
      {active && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            onChange({ page: 1 });
          }}
        >
          Clear filters
        </Button>
      )}
    </form>
  );
}
