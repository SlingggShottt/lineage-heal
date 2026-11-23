import { Badge } from "@/components/ui/badge";

import {
  FAULT_CLASS_LABELS,
  SEVERITY_LABELS,
  STATUS_LABELS,
  type FaultClass,
  type Severity,
  type Status,
} from "./labels";

type Variant = NonNullable<React.ComponentProps<typeof Badge>["variant"]>;

const STATUS_VARIANTS: Record<Status, Variant> = {
  detected: "secondary",
  investigating: "secondary",
  fix_proposed: "default",
  suggestion_filed: "default",
  fix_unverified: "warning",
  unresolved: "warning",
  approved: "success",
  rejected: "destructive",
  resolved: "success",
};

/** Where an incident is in its life, in words (colour alone never carries the meaning). */
export function StatusBadge({ status }: { status: Status }) {
  return <Badge variant={STATUS_VARIANTS[status]}>{STATUS_LABELS[status]}</Badge>;
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge variant={severity === "error" ? "destructive" : "warning"}>
      {SEVERITY_LABELS[severity]}
    </Badge>
  );
}

export function FaultClassBadge({ faultClass }: { faultClass: FaultClass | null }) {
  if (faultClass === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  return <Badge variant="outline">{FAULT_CLASS_LABELS[faultClass]}</Badge>;
}
